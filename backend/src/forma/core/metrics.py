"""Prometheus metrics (ADR-014). Counters/histograms are updated in-process; gauges that describe the
database (rounds by status, queue depth, review age, LLM cost) are computed at scrape time so that every
API replica reports the same truth regardless of which worker did the work."""

from __future__ import annotations

import datetime as dt
from typing import Any

from prometheus_client import CONTENT_TYPE_LATEST as _CT
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

CONTENT_TYPE_LATEST = _CT
REGISTRY = CollectorRegistry()

JOB_DURATION = Histogram(
    "forma_job_duration_seconds",
    "Job duration by kind and outcome",
    ["kind", "status"],
    registry=REGISTRY,
    buckets=(0.5, 1, 2, 5, 10, 20, 30, 60, 120, 300, 600, 1800),
)
JOBS_TOTAL = Counter(
    "forma_jobs_total", "Jobs finished by kind and status", ["kind", "status"], registry=REGISTRY
)
ENGINE_ROWS = Counter(
    "forma_engine_rows_total", "Rows produced by the transform engine", ["stage"], registry=REGISTRY
)
ENGINE_DURATION = Histogram(
    "forma_engine_run_seconds",
    "Transform engine run duration",
    registry=REGISTRY,
    buckets=(0.1, 0.5, 1, 2, 5, 10, 20, 60, 120),
)
LLM_CALLS = Counter(
    "forma_llm_calls_total", "LLM calls by agent and outcome", ["agent", "outcome"], registry=REGISTRY
)
LLM_COST = Counter("forma_llm_cost_usd_total", "Estimated LLM spend", ["agent"], registry=REGISTRY)
LLM_TOKENS = Counter("forma_llm_tokens_total", "LLM tokens", ["agent", "direction"], registry=REGISTRY)
HTTP_REQUESTS = Counter(
    "forma_http_requests_total",
    "HTTP requests by method, route and status",
    ["method", "route", "status"],
    registry=REGISTRY,
)
HTTP_LATENCY = Histogram(
    "forma_http_request_seconds",
    "HTTP latency by route",
    ["route"],
    registry=REGISTRY,
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)
BREAKER_STATE = Gauge("forma_llm_breaker_open", "1 when the LLM circuit breaker is open", registry=REGISTRY)

# ---- scrape-time gauges ----------------------------------------------------------------------
ROUNDS_BY_STATUS = Gauge("forma_rounds", "Import rounds by status", ["status"], registry=REGISTRY)
REVIEW_OPEN = Gauge("forma_review_open", "Open review tasks", registry=REGISTRY)
REVIEW_OLDEST_AGE = Gauge(
    "forma_review_oldest_age_seconds", "Age of the oldest open review task", registry=REGISTRY
)
QUEUE = Gauge("forma_jobs_queue", "Jobs by status", ["status"], registry=REGISTRY)
QUEUE_OLDEST_AGE = Gauge(
    "forma_jobs_oldest_queued_age_seconds", "Age of the oldest queued job", registry=REGISTRY
)
AUTOMATION_RATE = Gauge(
    "forma_automation_rate", "Share of mapping versions auto-approved by policy", registry=REGISTRY
)
LLM_COST_BY_ROUND = Gauge(
    "forma_llm_cost_usd_per_round",
    "LLM spend per round (last 100 rounds with calls)",
    ["round_id"],
    registry=REGISTRY,
)
WORKERS = Gauge("forma_workers", "Configured workers for this node", registry=REGISTRY)


def _age(now: dt.datetime, d: dt.datetime | None) -> float:
    if d is None:
        return 0.0
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.UTC)
    return max(0.0, (now - d).total_seconds())


def refresh_from_db() -> None:
    """Fill the scrape-time gauges from the database. Errors are swallowed: a scrape never 500s."""
    from sqlalchemy import func, select

    from forma.core.settings import get_settings
    from forma.db.models import ImportRound, Job, LLMCall, MappingVersion, ReviewTask
    from forma.db.session import session_scope

    now = dt.datetime.now(dt.UTC)
    try:
        with session_scope() as s:
            for status, n in s.execute(select(ImportRound.status, func.count()).group_by(ImportRound.status)):
                ROUNDS_BY_STATUS.labels(status=status).set(n)
            open_n = (
                s.scalar(select(func.count()).select_from(ReviewTask).where(ReviewTask.status == "open")) or 0
            )
            REVIEW_OPEN.set(open_n)
            oldest = s.scalar(select(func.min(ReviewTask.created_at)).where(ReviewTask.status == "open"))
            REVIEW_OLDEST_AGE.set(_age(now, oldest))
            for st in ("queued", "running", "done", "failed"):
                QUEUE.labels(status=st).set(0)
            for status, n in s.execute(select(Job.status, func.count()).group_by(Job.status)):
                QUEUE.labels(status=status).set(n)
            oq = s.scalar(select(func.min(Job.created_at)).where(Job.status == "queued"))
            QUEUE_OLDEST_AGE.set(_age(now, oq))
            total = s.scalar(select(func.count()).select_from(MappingVersion)) or 0
            auto = (
                s.scalar(
                    select(func.count())
                    .select_from(MappingVersion)
                    .where(MappingVersion.approved_by == "policy")
                )
                or 0
            )
            AUTOMATION_RATE.set(auto / total if total else 0.0)
            rows = s.execute(
                select(LLMCall.round_id, func.sum(LLMCall.cost_usd))
                .where(LLMCall.round_id.is_not(None))
                .group_by(LLMCall.round_id)
                .order_by(func.max(LLMCall.created_at).desc())
                .limit(100)
            ).all()
            LLM_COST_BY_ROUND.clear()
            for rid, cost in rows:
                LLM_COST_BY_ROUND.labels(round_id=str(rid)).set(float(cost or 0.0))
        WORKERS.set(get_settings().workers)
    except Exception:  # pragma: no cover - DB unreachable: in-process metrics still render
        pass


def render() -> bytes:
    refresh_from_db()
    return bytes(generate_latest(REGISTRY))


def observe_job(kind: str, status: str, duration_s: float | None) -> None:
    JOBS_TOTAL.labels(kind=kind, status=status).inc()
    if duration_s is not None:
        JOB_DURATION.labels(kind=kind, status=status).observe(duration_s)


def observe_llm(agent: str, usage: Any, *, ok: bool) -> None:
    LLM_CALLS.labels(agent=agent, outcome="ok" if ok else "error").inc()
    LLM_COST.labels(agent=agent).inc(float(getattr(usage, "cost_usd", 0.0) or 0.0))
    LLM_TOKENS.labels(agent=agent, direction="input").inc(int(getattr(usage, "input_tokens", 0) or 0))
    LLM_TOKENS.labels(agent=agent, direction="output").inc(int(getattr(usage, "output_tokens", 0) or 0))
