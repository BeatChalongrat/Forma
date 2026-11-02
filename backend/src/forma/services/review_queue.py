"""The review queue's clock (ADR-053): how long a question has waited, and questions parked while the
customer answers.

A task that waits for the customer leaves the person's queue and comes back on the day they chose, or
when they bring it back themselves. Nothing here decides anything about the file; it only says whose
turn it is.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from forma.auth import Principal
from forma.core.errors import Conflict, ValidationFailed
from forma.db.models import REVIEW_UNRESOLVED, Customer, ImportRound, ReviewTask
from forma.services.rounds import audit

STALE_DAYS = 7  # a question nobody has touched for this long is flagged
IDLE_ROUND_DAYS = 60  # a round with no activity for this long is flagged, never closed
MAX_NOTE = 500


def _today() -> dt.date:
    return dt.datetime.now(dt.UTC).date()


def age_days(t: ReviewTask, now: dt.datetime | None = None) -> int:
    now = now or dt.datetime.now(dt.UTC)
    created = t.created_at if t.created_at.tzinfo else t.created_at.replace(tzinfo=dt.UTC)
    return max(0, (now - created).days)


def wait(session: Session, p: Principal, t: ReviewTask, until: dt.date, note: str) -> ReviewTask:
    """Park an open task until a date: it leaves the queue and comes back on that day."""
    if t.status != "open":
        raise Conflict("Only an open question can wait for the customer", details={"status": t.status})
    if until < _today():
        raise ValidationFailed("Pick a day from today on")
    note = " ".join(note.split())
    if not note:
        raise ValidationFailed("Say what you are waiting for, in a few words")
    if len(note) > MAX_NOTE:
        raise ValidationFailed(f"Keep the note under {MAX_NOTE} characters")
    t.status = "waiting"
    t.waiting_until = until
    t.waiting_note = note
    audit(session, "review.wait", "review", t.id, actor=p.actor, until=until.isoformat(), note=note)
    return t


def resume(session: Session, p: Principal, t: ReviewTask, *, actor: str | None = None) -> ReviewTask:
    if t.status != "waiting":
        raise Conflict("This question is not waiting", details={"status": t.status})
    t.status = "open"
    audit(session, "review.resume", "review", t.id, actor=actor or p.actor, until=_date(t.waiting_until))
    t.waiting_until = None
    t.waiting_note = None
    return t


def confirm_audit(session: Session, p: Principal, t: ReviewTask, comment: str | None) -> ReviewTask:
    """A spot check of an automatic approval found nothing wrong (ADR-059). The approval already stood; this
    closes the task and records the look, which calibration counts as the automatic path being right."""
    if t.kind != "audit":
        raise ValidationFailed("Only a spot check can be confirmed; other questions are answered on the card")
    if t.status not in REVIEW_UNRESOLVED:
        raise Conflict("This spot check is already closed")
    t.status = "approved"
    t.resolver = p.actor
    t.comment = comment
    t.resolved_at = dt.datetime.now(dt.UTC)
    audit(session, "review.audit_ok", "review", t.id, actor=p.actor, round_id=t.round_id, comment=comment)
    return t


def _date(d: dt.date | None) -> str | None:
    return d.isoformat() if d else None


def wake_due(session: Session, today: dt.date | None = None) -> int:
    """Bring back every waiting task whose day has come. Cheap, so the queue read does it."""
    today = today or _today()
    due = list(
        session.scalars(
            select(ReviewTask).where(ReviewTask.status == "waiting", ReviewTask.waiting_until <= today)
        )
    )
    for t in due:
        t.status = "open"
        audit(session, "review.resume", "review", t.id, actor="clock", until=_date(t.waiting_until))
        t.waiting_until = None
        t.waiting_note = None
    return len(due)


def counts(session: Session, scoped: Any, now: dt.datetime | None = None) -> dict[str, Any]:
    """Figures for the home page: open, stale (open longer than STALE_DAYS), waiting, and the oldest."""
    now = now or dt.datetime.now(dt.UTC)
    cutoff = now - dt.timedelta(days=STALE_DAYS)
    base = scoped(select(func.count()).select_from(ReviewTask), ReviewTask.customer_id)
    open_n = session.scalar(base.where(ReviewTask.status == "open")) or 0
    stale = session.scalar(base.where(ReviewTask.status == "open", ReviewTask.created_at < cutoff)) or 0
    waiting = session.scalar(base.where(ReviewTask.status == "waiting")) or 0
    oldest = session.scalar(
        scoped(select(func.min(ReviewTask.created_at)), ReviewTask.customer_id).where(
            ReviewTask.status == "open"
        )
    )
    if oldest is not None and oldest.tzinfo is None:
        oldest = oldest.replace(tzinfo=dt.UTC)
    return {
        "open": open_n,
        "stale": stale,
        "waiting": waiting,
        "oldest_days": max(0, (now - oldest).days) if oldest else None,
        "stale_after_days": STALE_DAYS,
    }


def attention(session: Session, p: Principal, now: dt.datetime | None = None) -> list[dict[str, Any]]:
    """What foresight tells the person about their own side: questions waiting too long, and customers
    whose answer is due. Sorted by how late, longest first."""
    now = now or dt.datetime.now(dt.UTC)
    today = now.date()
    q = select(ReviewTask).where(ReviewTask.status.in_(REVIEW_UNRESOLVED))
    if p.customers is not None:
        q = q.where(ReviewTask.customer_id.in_(sorted(p.customers)))
    tasks = list(session.scalars(q))
    if not tasks:
        return []
    customers = {
        c.id: c.code
        for c in session.scalars(select(Customer).where(Customer.id.in_({t.customer_id for t in tasks})))
    }
    rounds = {
        r.id: r.label
        for r in session.scalars(select(ImportRound).where(ImportRound.id.in_({t.round_id for t in tasks})))
    }
    out: list[dict[str, Any]] = []
    for t in tasks:
        code, label = customers.get(t.customer_id, "?"), rounds.get(t.round_id) or t.round_id
        if t.status == "open":
            days = age_days(t, now)
            if days >= STALE_DAYS:
                out.append(
                    {
                        "kind": "stale_question",
                        "task_id": t.id,
                        "round_id": t.round_id,
                        "customer_code": code,
                        "days": days,
                        "text": {
                            "en": f"{code} · {label}: a question has waited {days} days for you",
                            "th": f"{code} · {label}: คำถามรอคุณมา {days} วันแล้ว",
                        },
                    }
                )
        elif t.waiting_until is not None:
            late = (today - t.waiting_until).days
            if late >= 0:
                out.append(
                    {
                        "kind": "customer_due",
                        "task_id": t.id,
                        "round_id": t.round_id,
                        "customer_code": code,
                        "days": late,
                        "text": {
                            "en": f"{code} · {label}: the customer's answer was due "
                            + ("today" if late == 0 else f"{late} days ago")
                            + f" — {t.waiting_note}",
                            "th": f"{code} · {label}: ถึงกำหนดที่ลูกค้าต้องตอบ"
                            + ("วันนี้" if late == 0 else f"เมื่อ {late} วันก่อน")
                            + f" — {t.waiting_note}",
                        },
                    }
                )
    out.sort(key=lambda x: (-int(x["days"]), str(x["customer_code"])))
    return out


def idle_rounds(session: Session, p: Principal, now: dt.datetime | None = None) -> list[dict[str, Any]]:
    """Open rounds nobody has touched for IDLE_ROUND_DAYS. Flagged for a person; retention never closes
    a round (ADR-015, ADR-052)."""
    from forma.services.round_lifecycle import CLOSED

    now = now or dt.datetime.now(dt.UTC)
    cutoff = now - dt.timedelta(days=IDLE_ROUND_DAYS)
    done = ("published", "rejected", "blocked", "failed", *CLOSED)
    q = (
        select(ImportRound)
        .where(ImportRound.status.not_in(done), ImportRound.updated_at < cutoff)
        .order_by(ImportRound.updated_at.asc())
    )
    if p.customers is not None:
        q = q.where(ImportRound.customer_id.in_(sorted(p.customers)))
    rounds = list(session.scalars(q))
    codes = (
        {
            c.id: c.code
            for c in session.scalars(select(Customer).where(Customer.id.in_({r.customer_id for r in rounds})))
        }
        if rounds
        else {}
    )
    out = []
    for r in rounds:
        updated = r.updated_at if r.updated_at.tzinfo else r.updated_at.replace(tzinfo=dt.UTC)
        out.append(
            {
                "round_id": r.id,
                "customer_code": codes.get(r.customer_id, "?"),
                "label": r.label,
                "status": r.status,
                "idle_days": (now - updated).days,
            }
        )
    return out
