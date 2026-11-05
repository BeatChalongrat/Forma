"""Closing a round without deleting anything: cancel, move to another customer, replace with a new file (ADR-052).

A closed round keeps its files, versions and history. It leaves every queue and count, and its JSON leaves
the work round's zip. Each change records who did it, when and why, and a cancelled round can be reopened.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from forma.auth import Principal
from forma.core.errors import Conflict, ValidationFailed
from forma.db.models import REVIEW_UNRESOLVED, BatchItem, ImportRound, ReviewTask, SourceFile, utcnow
from forma.services import rounds as rs
from forma.services.context import ServiceContext

CLOSED = ("cancelled", "moved", "superseded")
MAX_REASON = 500


def is_closed(r: ImportRound) -> bool:
    return r.status in CLOSED


def lock_round(session: Session, round_id: str, *, require_open: bool = True) -> ImportRound:
    """Serialize lifecycle and dataset writes, always refreshing state after the lock is acquired."""
    if session.get_bind().dialect.name == "sqlite":
        # SQLite ignores FOR UPDATE. Acquire its writer lock before reading the current head.
        session.execute(
            update(ImportRound)
            .where(ImportRound.id == round_id)
            .values(status=ImportRound.status)
            .execution_options(synchronize_session=False)
        )
    r = session.scalar(
        select(ImportRound)
        .where(ImportRound.id == round_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if r is None:
        from forma.core.errors import NotFound

        raise NotFound(f"round {round_id} not found")
    if require_open and is_closed(r):
        raise Conflict(
            "ไฟล์ชุดนี้ถูกยกเลิก ย้าย หรือแทนที่แล้ว กรุณาเปิดชุดที่ใช้งานอยู่",
            details={"status": r.status, "successor_id": r.successor_id},
        )
    return r


def _reason(reason: str) -> str:
    reason = " ".join(reason.split())
    if not reason:
        raise ValidationFailed("Say why, in a few words")
    if len(reason) > MAX_REASON:
        raise ValidationFailed(f"Keep the reason under {MAX_REASON} characters")
    return reason


def _close(session: Session, p: Principal, r: ImportRound, kind: str, reason: str) -> list[str]:
    """Mark the round closed and set its open review tasks aside. Returns the ids of the tasks set aside."""
    # Same lock order as publication fencing: round first, then its jobs.
    r = lock_round(session, r.id)
    from sqlalchemy import update

    from forma.db.models import Job

    # Revoke ownership in the same transaction as the lifecycle change. A stale worker's
    # business commit is rejected even if its in-memory round still looks active.
    session.execute(
        update(Job)
        .where(Job.round_id == r.id, Job.status.in_(("queued", "running", "cancelled")))
        .values(
            status="cancelled",
            lease_token=None,
            lease_expires_at=None,
            finished_at=utcnow(),
            message=f"File {kind}: {reason}",
        )
    )
    tasks = list(
        session.scalars(
            select(ReviewTask).where(ReviewTask.round_id == r.id, ReviewTask.status.in_(REVIEW_UNRESOLVED))
        )
    )
    for t in tasks:
        t.status = "dismissed"
        t.waiting_until = None
        t.waiting_note = None
        t.resolver = p.actor
        t.comment = f"{kind}: {reason}"
        t.resolved_at = utcnow()
    r.closed_from = r.status
    r.closed_by = p.actor
    r.closed_at = utcnow()
    r.closed_reason = reason
    r.status = kind
    r.summary = {**(r.summary or {}), "closed_tasks": [t.id for t in tasks]}
    return [t.id for t in tasks]


def cancel(session: Session, p: Principal, r: ImportRound, reason: str) -> ImportRound:
    reason = _reason(reason)
    tasks = _close(session, p, r, "cancelled", reason)
    rs.audit(
        session, "round.cancel", "round", r.id, actor=p.actor, reason=reason, was=r.closed_from, tasks=tasks
    )
    return r


def reopen(session: Session, p: Principal, r: ImportRound) -> ImportRound:
    """Only a cancelled round comes back: a moved or replaced one has a successor that took its place."""
    r = lock_round(session, r.id, require_open=False)
    if r.status != "cancelled":
        raise Conflict("Only a cancelled round can be reopened", details={"status": r.status})
    if any(f.status == "purged" for f in r.files):
        raise Conflict("Retention has removed this round's files. Upload them again as a new round.")
    was = r.closed_from or "uploaded"
    for tid in (r.summary or {}).get("closed_tasks", []):
        t = session.get(ReviewTask, tid)
        if t is not None and t.status == "dismissed":
            t.status = "open"
            t.resolver = None
            t.comment = None
            t.resolved_at = None
    r.status = was
    r.closed_from = None
    r.closed_by = None
    r.closed_at = None
    r.closed_reason = None
    r.summary = {k: v for k, v in (r.summary or {}).items() if k != "closed_tasks"}
    rs.audit(session, "round.reopen", "round", r.id, actor=p.actor, status=was)
    return r


def _successor(
    session: Session, r: ImportRound, customer_id: str, label: str | None, kind: str, reason: str
) -> ImportRound:
    new = ImportRound(
        customer_id=customer_id,
        label=label or r.label,
        target_schema=r.target_schema,
        notes=r.notes,
        predecessor_id=r.id,
        summary={k: v for k, v in (r.summary or {}).items() if k in ("batch_id", "auto_publish")}
        | {"predecessor": {"id": r.id, "kind": kind, "reason": reason}},
    )
    session.add(new)
    session.flush()
    r.successor_id = new.id
    # A work round's file row follows the file, so the zip and the counts show the round that is current.
    for item in session.scalars(select(BatchItem).where(BatchItem.round_id == r.id)):
        item.round_id = new.id
        item.customer_id = customer_id
    return new


def move(session: Session, p: Principal, r: ImportRound, customer_id: str, reason: str) -> ImportRound:
    """The same files become a new round of another customer; the old round is closed as moved."""
    reason = _reason(reason)
    if customer_id == r.customer_id:
        raise ValidationFailed("That is the customer the round already belongs to")
    c = rs.get_customer(session, customer_id)
    tasks = _close(session, p, r, "moved", reason)
    new = _successor(session, r, c.id, r.label, "moved", reason)
    for f in r.files:
        session.add(
            SourceFile(
                round_id=new.id,
                customer_id=c.id,
                sha256=f.sha256,
                original_name=f.original_name,
                size=f.size,
                kind=f.kind,
                storage_key=f.storage_key,
                duplicate_of=f.id,
            )
        )
    session.flush()
    rs.audit(
        session,
        "round.move",
        "round",
        r.id,
        actor=p.actor,
        reason=reason,
        to_customer=c.id,
        new_round=new.id,
        tasks=tasks,
    )
    rs.audit(session, "round.create", "round", new.id, actor=p.actor, customer_id=c.id, moved_from=r.id)
    return new


def replace(
    ctx: ServiceContext,
    session: Session,
    p: Principal,
    r: ImportRound,
    files: list[tuple[str, bytes]],
    reason: str,
    label: str | None = None,
) -> ImportRound:
    """A corrected delivery: a new round of the same customer takes the old one's place."""
    reason = _reason(reason)
    if not files:
        raise ValidationFailed("Add the corrected file first")
    tasks = _close(session, p, r, "superseded", reason)
    new = _successor(session, r, r.customer_id, label, "superseded", reason)
    for name, data in files:
        rs.add_file(ctx, session, new, name, data)
    rs.audit(
        session, "round.replace", "round", r.id, actor=p.actor, reason=reason, new_round=new.id, tasks=tasks
    )
    rs.audit(
        session, "round.create", "round", new.id, actor=p.actor, customer_id=r.customer_id, replaces=r.id
    )
    return new


def related(session: Session, r: ImportRound) -> dict[str, Any]:
    """The rounds on either side of this one, for the page to link to."""

    def brief(rid: str | None) -> dict[str, Any] | None:
        other = session.get(ImportRound, rid) if rid else None
        if other is None:
            return None
        return {
            "id": other.id,
            "label": other.label,
            "status": other.status,
            "customer_id": other.customer_id,
            "customer_code": other.customer.code if other.customer else None,
        }

    return {"successor": brief(r.successor_id), "predecessor": brief(r.predecessor_id)}
