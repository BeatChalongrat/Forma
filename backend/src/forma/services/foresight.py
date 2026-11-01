"""Proactive intelligence (ADR-039): what FORMA expects before a file arrives.

Everything here is derived from history already in the database — when each customer's rounds arrived,
how their layouts drifted, how their reviews went — and is recomputed on read. Nothing is stored and
nothing is decided: the output is a list of customers worth a look today, each with the reason in words.
"""

from __future__ import annotations

import datetime as dt
import itertools
import statistics
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from forma.auth import Principal
from forma.db.models import Customer, ImportRound
from forma.services import review_queue
from forma.services.round_lifecycle import CLOSED

MIN_ROUNDS = 2  # rounds before a cadence can be estimated
RECENT = 3  # rounds the watchlist looks at
DRIFTED = ("columns_renamed", "columns_removed", "sheets_removed", "types_changed", "unrelated_file")


def _text(en: str, th: str) -> dict[str, str]:
    return {"en": en, "th": th}


def _rounds_by_customer(session: Session, p: Principal) -> dict[str, list[ImportRound]]:
    # A cancelled, moved or replaced round was not a delivery (ADR-052).
    q = (
        select(ImportRound)
        .where(ImportRound.status.not_in(CLOSED))
        .order_by(ImportRound.customer_id, ImportRound.created_at)
    )
    if p.customers is not None:
        q = q.where(ImportRound.customer_id.in_(sorted(p.customers)))
    out: dict[str, list[ImportRound]] = {}
    for r in session.scalars(q):
        out.setdefault(r.customer_id, []).append(r)
    return out


# --------------------------------------------------------------------------------------------
# cadence: when the next file is due
# --------------------------------------------------------------------------------------------
def cadence(rounds: list[ImportRound], *, today: dt.date | None = None) -> dict[str, Any]:
    """Median interval between a customer's rounds, the expected next date and how late it is."""
    today = today or dt.datetime.now(dt.UTC).date()
    days = sorted({r.created_at.date() for r in rounds if r.created_at})
    if len(days) < MIN_ROUNDS:
        return {"status": "unknown", "rounds": len(days), "last": days[-1].isoformat() if days else None}
    gaps = [(b - a).days for a, b in itertools.pairwise(days) if (b - a).days > 0]
    if not gaps:
        return {"status": "unknown", "rounds": len(days), "last": days[-1].isoformat()}
    every = round(statistics.median(gaps))
    grace = max(2, round(every * 0.25))
    expected = days[-1] + dt.timedelta(days=every)
    late = (today - expected).days
    status = "overdue" if late > grace else "due" if late >= 0 else "ahead"
    return {
        "status": status,
        "rounds": len(days),
        "every_days": every,
        "last": days[-1].isoformat(),
        "expected": expected.isoformat(),
        "late_days": late,
        "grace_days": grace,
    }


def expected_deliveries(session: Session, p: Principal, *, today: dt.date | None = None) -> dict[str, Any]:
    """Customers whose next file is due or overdue, by how late they are."""
    by_customer = _rounds_by_customer(session, p)
    customers = {
        c.id: c
        for c in session.scalars(
            select(Customer).where(Customer.id.in_(list(by_customer)), Customer.active.is_(True))
        )
    }
    items: list[dict[str, Any]] = []
    counts = {"overdue": 0, "due": 0, "ahead": 0, "unknown": 0}
    for cid, rounds in by_customer.items():
        c = customers.get(cid)
        if c is None:
            continue
        cad = cadence(rounds, today=today)
        counts[cad["status"]] += 1
        if cad["status"] in ("overdue", "due"):
            late = int(cad["late_days"])
            items.append(
                {
                    "customer_id": cid,
                    "code": c.code,
                    "name": c.name,
                    **cad,
                    "text": _text(
                        f"{c.code} usually sends every {cad['every_days']} days; the last file came on {cad['last']}"
                        + (f" — {late} days overdue" if late > 0 else " — due today"),
                        f"{c.code} ปกติส่งทุก {cad['every_days']} วัน ไฟล์ล่าสุดเมื่อ {cad['last']}"
                        + (f" — เลยกำหนด {late} วัน" if late > 0 else " — ครบกำหนดวันนี้"),
                    ),
                }
            )
    items.sort(key=lambda x: (-int(x["late_days"]), x["code"]))
    return {"items": items, "counts": counts}


# --------------------------------------------------------------------------------------------
# watchlist: who will probably need a person next time
# --------------------------------------------------------------------------------------------
def watchlist(session: Session, p: Principal) -> list[dict[str, Any]]:
    by_customer = _rounds_by_customer(session, p)
    customers = {c.id: c for c in session.scalars(select(Customer).where(Customer.id.in_(list(by_customer))))}
    out: list[dict[str, Any]] = []
    for cid, rounds in by_customer.items():
        c = customers.get(cid)
        if c is None:
            continue
        recent = rounds[-RECENT:]
        drifted = [r for r in recent if ((r.summary or {}).get("drift") in DRIFTED)]
        reviewed = [r for r in recent if (r.summary or {}).get("decision") in ("review", "block")]
        last = recent[-1]
        reasons: list[dict[str, str]] = []
        if len(drifted) >= 2:
            reasons.append(
                _text(
                    f"layout changed in {len(drifted)} of the last {len(recent)} rounds",
                    f"โครงสร้างไฟล์เปลี่ยน {len(drifted)} ใน {len(recent)} รอบล่าสุด",
                )
            )
        if last.status == "blocked":
            reasons.append(
                _text("the last file was blocked (possible wrong file)", "ไฟล์ล่าสุดถูกระงับ (อาจส่งไฟล์ผิด)")
            )
        elif len(reviewed) >= 2 and len(recent) >= 2:
            reasons.append(
                _text(
                    f"a person was needed in {len(reviewed)} of the last {len(recent)} rounds",
                    f"ต้องใช้คน {len(reviewed)} ใน {len(recent)} รอบล่าสุด",
                )
            )
        if not reasons:
            continue
        out.append(
            {
                "customer_id": cid,
                "code": c.code,
                "name": c.name,
                "reasons": reasons,
                "text": _text(
                    f"{c.code}: " + "; ".join(r["en"] for r in reasons) + " — expect a check next time",
                    f"{c.code}: " + " · ".join(r["th"] for r in reasons) + " — รอบหน้าน่าจะต้องตรวจ",
                ),
                "last_round_id": last.id,
            }
        )
    out.sort(key=lambda x: (-len(x["reasons"]), x["code"]))
    return out


def view(session: Session, p: Principal, *, today: dt.date | None = None) -> dict[str, Any]:
    exp = expected_deliveries(session, p, today=today)
    return {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "attention": review_queue.attention(session, p)[:50],
        "idle_rounds": review_queue.idle_rounds(session, p)[:50],
        "expected": exp["items"][:50],
        "counts": exp["counts"],
        "watchlist": watchlist(session, p)[:50],
    }
