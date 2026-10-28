"""Background job runner.

Dev/single-node: a thread pool inside the API process with durable Job rows (status, progress, result).
The interface is deliberately tiny (`enqueue(kind, payload)` + handlers) so it can be swapped for
Celery/arq/Temporal when scale demands it without touching services or the API.
"""

from __future__ import annotations

import datetime as dt
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from forma.core.logging import get_logger
from forma.db.models import Job
from forma.db.session import session_scope
from forma.services.context import ServiceContext, get_context

log = get_logger(__name__)

Handler = Callable[[ServiceContext, dict[str, Any], "JobProgress"], dict[str, Any]]
_HANDLERS: dict[str, Handler] = {}
_POOL: ThreadPoolExecutor | None = None


class JobProgress:
    def __init__(self, job_id: str) -> None:
        self.job_id = job_id

    def update(self, progress: int, message: str | None = None) -> None:
        with session_scope() as s:
            j = s.get(Job, self.job_id)
            if j:
                j.progress = progress
                j.message = message


def handler(kind: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        _HANDLERS[kind] = fn
        return fn

    return deco


def _pool() -> ThreadPoolExecutor:
    global _POOL
    if _POOL is None:
        _POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="forma-job")
    return _POOL


def enqueue(kind: str, payload: dict[str, Any], *, round_id: str | None = None, sync: bool = False) -> str:
    if kind not in _HANDLERS:
        raise KeyError(f"no handler for job kind {kind!r}")
    with session_scope() as s:
        j = Job(kind=kind, payload=payload, round_id=round_id)
        s.add(j)
        s.flush()
        job_id = j.id
    if sync:
        _run(job_id)
    else:
        _pool().submit(_run, job_id)
    return job_id


def _run(job_id: str) -> None:
    ctx = get_context()
    with session_scope() as s:
        j = s.get(Job, job_id)
        if j is None:
            return
        j.status = "running"
        j.started_at = dt.datetime.now(dt.UTC)
        kind, payload = j.kind, dict(j.payload)
    try:
        result = _HANDLERS[kind](ctx, payload, JobProgress(job_id))
        with session_scope() as s:
            j = s.get(Job, job_id)
            if j:
                j.status = "done"
                j.result = result
                j.progress = 100
                j.finished_at = dt.datetime.now(dt.UTC)
    except Exception as e:
        log.error("job failed", job=job_id, kind=kind, error=str(e))
        with session_scope() as s:
            j = s.get(Job, job_id)
            if j:
                j.status = "failed"
                j.error = f"{e}\n{traceback.format_exc()[-2000:]}"
                j.finished_at = dt.datetime.now(dt.UTC)
            if payload.get("round_id"):
                from forma.db.models import ImportRound

                r = s.get(ImportRound, payload["round_id"])
                if r:
                    r.status = "failed"
                    r.summary = {**(r.summary or {}), "error": str(e)[:500]}


def job_status(job_id: str) -> dict[str, Any]:
    with session_scope() as s:
        j = s.get(Job, job_id)
        if j is None:
            raise KeyError(job_id)
        return {
            "id": j.id,
            "kind": j.kind,
            "status": j.status,
            "progress": j.progress,
            "message": j.message,
            "result": j.result,
            "error": j.error,
            "round_id": j.round_id,
            "created_at": j.created_at.isoformat(),
            "finished_at": j.finished_at.isoformat() if j.finished_at else None,
        }
