"""Ops page data (ADR-014): one query set, admin only."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from forma.core import telemetry
from forma.db.models import Customer, ImportRound, Job, LLMCall, ReviewTask
from forma.jobs import runner
from forma.services.context import ServiceContext


def _aware(d: dt.datetime | None) -> dt.datetime | None:
    if d is None:
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.UTC)


def ops_summary(session: Session, ctx: ServiceContext, ready: dict[str, Any]) -> dict[str, Any]:
    now = dt.datetime.now(dt.UTC)
    customers = dict(session.execute(select(Customer.id, Customer.code)).all())
    rounds = dict(session.execute(select(ImportRound.id, ImportRound.customer_id)).all())
    labels = dict(session.execute(select(ImportRound.id, ImportRound.label)).all())

    failed = [
        {**runner._job_dict(j), "customer_code": customers.get(rounds.get(j.round_id or "", ""))}
        for j in session.scalars(
            select(Job).where(Job.status == "failed").order_by(Job.finished_at.desc()).limit(50)
        )
    ]
    since = now - dt.timedelta(days=7)
    spend: dict[str, dict[str, float]] = {}
    for c in session.scalars(select(LLMCall).where(LLMCall.created_at >= since)):
        day = (_aware(c.created_at) or now).date().isoformat()
        d = spend.setdefault(day, {"calls": 0, "tokens": 0, "cost_usd": 0.0})
        d["calls"] += 1
        d["tokens"] += c.input_tokens + c.output_tokens
        d["cost_usd"] += c.cost_usd
    llm_by_day = [{"day": k, **{kk: round(vv, 4) for kk, vv in v.items()}} for k, v in sorted(spend.items())]

    slow: list[dict[str, Any]] = []
    for j in session.scalars(
        select(Job)
        .where(Job.status == "done", Job.kind.in_(["run_pipeline", "transform_round", "profile_round"]))
        .order_by(Job.finished_at.desc())
        .limit(200)
    ):
        stages = (j.result or {}).get("stages", {}) if j.result else {}
        slow.append(
            {
                "job_id": j.id,
                "kind": j.kind,
                "round_id": j.round_id,
                "customer_code": customers.get(rounds.get(j.round_id or "", "")),
                "label": labels.get(j.round_id or ""),
                "duration_ms": runner._duration_ms(j),
                "stages": {k: (v or {}).get("duration_ms") for k, v in stages.items() if isinstance(v, dict)},
            }
        )
    slow.sort(key=lambda x: -(x["duration_ms"] or 0))

    open_n = (
        session.scalar(select(func.count()).select_from(ReviewTask).where(ReviewTask.status == "open")) or 0
    )
    oldest = _aware(
        session.scalar(select(func.min(ReviewTask.created_at)).where(ReviewTask.status == "open"))
    )
    from forma.db import migrate
    from forma.db.session import get_engine

    try:
        from forma.ai.breaker import state as breaker_state

        breaker: dict[str, Any] | None = breaker_state()
    except Exception:  # pragma: no cover - breaker lands in a later step
        breaker = None
    return {
        "queue": runner.queue_stats(),
        "ready": ready,
        "review": {
            "open": open_n,
            "oldest_age_s": round((now - oldest).total_seconds(), 1) if oldest else None,
        },
        "failed_jobs": failed,
        "llm_spend_by_day": llm_by_day,
        "slowest_rounds": slow[:15],
        "breaker": breaker,
        "storage": ctx.storage.name,
        "database": get_engine().url.get_backend_name(),
        "auth": ctx.settings.auth,
        "tracing": telemetry.enabled(),
        "schema_revision": migrate.current_revision(get_engine()),
    }
