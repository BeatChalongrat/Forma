"""Observable work receipts, not model reasoning. Durable even if the business transaction rolls back.

Postgres uses the application database. SQLite uses an adjacent journal because a worker can hold
the business writer lock while a tool runs. Back up both files when using SQLite.
"""

from __future__ import annotations

import datetime as dt
import functools
import inspect
import json
import re
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from typing import Any, ParamSpec, TypeVar, cast

import structlog
from sqlalchemy import MetaData, Table, create_engine, select
from sqlalchemy.engine import Engine, make_url

from forma.core.ids import new_id
from forma.core.settings import get_settings
from forma.db.models import JobActivity

metadata = MetaData()
events = cast(Table, JobActivity.__table__).to_metadata(metadata)
context: ContextVar[tuple[str, int] | None] = ContextVar("activity_job", default=None)
role: ContextVar[str] = ContextVar("activity_role", default="system")
parent: ContextVar[str | None] = ContextVar("activity_parent", default=None)
_setup_lock = threading.Lock()
P = ParamSpec("P")
T = TypeVar("T")


@lru_cache(maxsize=8)
def _engine(url: str) -> Engine:
    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite":
        # Derive from the database, not a shared data_dir: isolated tests/repro stay isolated.
        name = parsed.database
        parsed = parsed.set(database=f"{name}.activity.sqlite" if name and name != ":memory:" else ":memory:")
        engine = create_engine(parsed, connect_args={"check_same_thread": False, "timeout": 5})
        with _setup_lock:
            metadata.create_all(engine)
            with engine.begin() as conn:
                conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        return engine
    return create_engine(parsed, pool_pre_ping=True, connect_args={"connect_timeout": 5})


def engine() -> Engine:
    return _engine(get_settings().sqlalchemy_url)


def safe(value: Any, depth: int = 0) -> Any:
    """Bound evidence volume and omit credentials/prompts/private reasoning from the journal."""
    if depth > 12:
        return {"omitted": "depth limit"}
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    elif hasattr(value, "to_dict"):
        value = value.to_dict()
    if isinstance(value, dict):
        return {
            str(k): "[redacted]"
            if re.search(
                r"api.?key|authorization|password|secret|access.token|reasoning_content|chain.of.thought|system_prompt",
                str(k),
                re.I,
            )
            else safe(v, depth + 1)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [safe(v, depth + 1) for v in value[:200]] + (
            [{"omitted_items": len(value) - 200}] if len(value) > 200 else []
        )
    if isinstance(value, str):
        value = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}", "[redacted]", value)
        return value[:24000] + ("\n[truncated]" if len(value) > 24000 else "")
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return f"<{type(value).__name__}>"


def emit(
    action: str,
    state: str = "info",
    *,
    span_id: str | None = None,
    duration_ms: int | None = None,
    detail: Any = None,
) -> None:
    scope = context.get()
    if scope is None:
        return
    data = safe(detail)
    encoded = json.dumps(data, ensure_ascii=False)
    if len(encoded) > 256000:
        encoded = json.dumps({"truncated": True, "preview": encoded[:24000]}, ensure_ascii=False)
    try:
        with engine().begin() as conn:
            conn.execute(
                events.insert().values(
                    job_id=scope[0],
                    attempt=scope[1],
                    span_id=span_id or new_id("act"),
                    parent_id=parent.get(),
                    role=role.get(),
                    action=action,
                    state=state,
                    at=dt.datetime.now(dt.UTC).isoformat(),
                    duration_ms=duration_ms,
                    detail=encoded,
                )
            )
    except Exception:
        # Telemetry is not allowed to roll back a correct import or consume model retries.
        structlog.get_logger().exception("activity journal unavailable", job_id=scope[0])


@contextmanager
def span(action: str, *, agent: str | None = None, inputs: Any = None) -> Iterator[dict[str, Any]]:
    sid = new_id("act")
    token = role.set(agent or role.get())
    emit(action, "started", span_id=sid, detail={"input": inputs})
    ptoken = parent.set(sid)
    outer_parent = ptoken.old_value if ptoken.old_value is not ptoken.MISSING else None
    started = time.monotonic()
    result: dict[str, Any] = {}
    try:
        yield result
    except BaseException as exc:
        parent.set(outer_parent)
        emit(
            action,
            "failed",
            span_id=sid,
            duration_ms=round((time.monotonic() - started) * 1000),
            detail={"error": str(exc)[:1000], "type": type(exc).__name__},
        )
        raise
    else:
        parent.set(outer_parent)
        emit(
            action,
            "failed" if result.get("failure") else "completed",
            span_id=sid,
            duration_ms=round((time.monotonic() - started) * 1000),
            detail=result,
        )
    finally:
        parent.reset(ptoken)
        role.reset(token)


def observed(
    action: str, *, agent: str | None = None, arguments: tuple[str, ...] = ()
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Instrument actual tool boundaries, retaining only explicitly allowed arguments."""

    def decorate(fn: Callable[P, T]) -> Callable[P, T]:
        signature = inspect.signature(fn)

        @functools.wraps(fn)
        def run(*args: P.args, **kwargs: P.kwargs) -> T:
            if context.get() is None:
                return fn(*args, **kwargs)
            bound = signature.bind(*args, **kwargs)
            with span(action, agent=agent, inputs={k: bound.arguments.get(k) for k in arguments}) as receipt:
                result = fn(*args, **kwargs)
                receipt["output"] = result
                return result

        return run

    return decorate


def read(job_id: str, attempt: int, after: int = 0, limit: int = 100) -> list[dict[str, Any]]:
    with engine().connect() as conn:
        rows = conn.execute(
            select(events)
            .where(
                events.c.job_id == job_id,
                events.c.attempt == attempt,
                events.c.id > after,
            )
            .order_by(events.c.id)
            .limit(limit)
        ).mappings()
        return [{**r, "detail": json.loads(r["detail"])} for r in rows]


def purge_jobs(ids: list[str]) -> None:
    """Called only after retention commits. Never removes an active job's evidence."""
    from forma.db.models import Job
    from forma.db.session import get_engine

    try:
        with get_engine().connect() as conn:
            existing = set(conn.scalars(select(Job.id).where(Job.id.in_(ids))))
        absent = [jid for jid in ids if jid not in existing]
        if absent:
            with engine().begin() as conn:
                conn.execute(events.delete().where(events.c.job_id.in_(absent)))
    except Exception:
        structlog.get_logger().exception("activity retention cleanup failed", job_ids=ids)
