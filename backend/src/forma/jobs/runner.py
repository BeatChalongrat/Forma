"""Background job runner (ADR-009, ADR-010).

The interface is deliberately tiny — ``enqueue(kind, payload)`` + ``@handler`` — and the execution
backend is chosen by ``FORMA_JOB_BACKEND``:

* ``thread``  — thread pool inside the API process (dev default, zero services)
* ``process`` — ``multiprocessing`` pool (``FORMA_WORKERS`` processes), same machine
* ``arq``     — Redis-backed queue; workers run ``forma worker`` on any number of nodes

Every job is a durable ``Job`` row. Workers *claim* a row atomically (``queued → running``), so a job
submitted twice runs once; a worker that dies leaves the row ``running`` and ``recover_stale()``
re-queues it on the next start. Handlers are idempotent/resumable through ``JobProgress.stages``.
"""

from __future__ import annotations

import datetime as dt
import os
import socket
import threading
import traceback
from collections.abc import Callable, Sequence
from concurrent.futures import Executor, ProcessPoolExecutor, ThreadPoolExecutor
from typing import Any, Protocol

from sqlalchemy import func, select, update

from forma.core.errors import Conflict, NotFound, QueueFull
from forma.core.ids import new_id
from forma.core.logging import get_logger
from forma.core.settings import get_settings
from forma.db.models import ImportRound, Job
from forma.db.session import session_scope
from forma.services.context import ServiceContext, get_context

log = get_logger(__name__)

Handler = Callable[[ServiceContext, dict[str, Any], "JobProgress"], dict[str, Any]]
_HANDLERS: dict[str, Handler] = {}
_BACKEND: Backend | None = None
_LOCK = threading.Lock()
TERMINAL = ("done", "failed")


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _aware(d: dt.datetime | None) -> dt.datetime | None:
    """SQLite returns naive datetimes; treat them as UTC (ADR-011)."""
    if d is None:
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.UTC)


def worker_name() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


# --------------------------------------------------------------------------------------------
# Progress + resumable stages
# --------------------------------------------------------------------------------------------
class JobProgress:
    """Progress reporter and stage ledger. ``stages`` survives a crash: a re-run skips finished stages."""

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        self.stages: dict[str, Any] = {}
        with session_scope() as s:
            j = s.get(Job, self.job_id)
            if j and j.result and isinstance(j.result.get("stages"), dict):
                self.stages = dict(j.result["stages"])

    def update(self, progress: int, message: str | None = None) -> None:
        with session_scope() as s:
            j = s.get(Job, self.job_id)
            if j:
                j.progress = progress
                j.message = message

    def done(self, stage: str) -> bool:
        return stage in self.stages

    def mark(self, stage: str, **info: Any) -> None:
        """Persist a finished stage immediately (its own transaction) so a crash later does not redo it."""
        self.stages[stage] = {"at": _utcnow().isoformat(), **info}
        with session_scope() as s:
            j = s.get(Job, self.job_id)
            if j:
                j.result = {**(j.result or {}), "stages": self.stages}


def handler(kind: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        _HANDLERS[kind] = fn
        return fn

    return deco


def handlers() -> dict[str, Handler]:
    return dict(_HANDLERS)


# --------------------------------------------------------------------------------------------
# Backends
# --------------------------------------------------------------------------------------------
class Backend(Protocol):
    name: str

    def submit(self, job_id: str) -> None: ...
    def stats(self) -> dict[str, Any]: ...
    def shutdown(self) -> None: ...


class _PoolBackend:
    """Shared implementation for the thread and process pools."""

    name = "thread"

    def __init__(self, workers: int) -> None:
        self.workers = max(1, workers)
        self._pool: Executor | None = None
        self._inflight = 0
        self._lock = threading.Lock()

    def _make_pool(self) -> Executor:
        return ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="forma-job")

    def _ensure(self) -> Executor:
        if self._pool is None:
            self._pool = self._make_pool()
        return self._pool

    def submit(self, job_id: str) -> None:
        with self._lock:
            self._inflight += 1
        fut = self._ensure().submit(run_job, job_id)

        def _done(_: Any) -> None:
            with self._lock:
                self._inflight -= 1

        fut.add_done_callback(_done)

    def stats(self) -> dict[str, Any]:
        return {"backend": self.name, "workers": self.workers, "inflight": self._inflight, "nodes": 1}

    def shutdown(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None


class ThreadBackend(_PoolBackend):
    name = "thread"


class ProcessBackend(_PoolBackend):
    name = "process"

    def _make_pool(self) -> Executor:
        import multiprocessing as mp

        return ProcessPoolExecutor(max_workers=self.workers, mp_context=mp.get_context("spawn"))


class ArqBackend:
    """Redis-backed queue (``arq``). The API only enqueues; ``forma worker`` executes."""

    name = "arq"

    def __init__(self, redis_url: str) -> None:
        self.redis_url = redis_url

    def submit(self, job_id: str) -> None:
        import asyncio

        asyncio.run(self._enqueue(job_id))

    async def _enqueue(self, job_id: str) -> None:
        from arq import create_pool
        from arq.connections import RedisSettings

        pool = await create_pool(RedisSettings.from_dsn(self.redis_url))
        try:
            await pool.enqueue_job("arq_run_job", job_id, _job_id=job_id)
        finally:
            await pool.close()

    def stats(self) -> dict[str, Any]:
        import asyncio

        try:
            return asyncio.run(self._stats())
        except Exception as e:  # redis down: report, never raise from a stats call
            return {"backend": self.name, "error": str(e)[:200]}

    async def _stats(self) -> dict[str, Any]:
        from arq import create_pool
        from arq.connections import RedisSettings
        from arq.constants import default_queue_name, health_check_key_suffix

        pool = await create_pool(RedisSettings.from_dsn(self.redis_url))
        try:
            depth = await pool.zcard(default_queue_name)
            health = await pool.get(default_queue_name + health_check_key_suffix)
        finally:
            await pool.close()
        return {
            "backend": self.name,
            "redis_queue_depth": int(depth or 0),
            "health": health.decode() if isinstance(health, bytes) else health,
        }

    def shutdown(self) -> None:
        return None


def make_backend(name: str | None = None) -> Backend:
    s = get_settings()
    kind = (name or s.job_backend).lower()
    if kind == "thread":
        return ThreadBackend(s.workers)
    if kind == "process":
        return ProcessBackend(s.workers)
    if kind == "arq":
        return ArqBackend(s.redis_url)
    raise ValueError(f"unknown FORMA_JOB_BACKEND {kind!r} (thread | process | arq)")


def get_backend() -> Backend:
    global _BACKEND
    with _LOCK:
        if _BACKEND is None:
            _BACKEND = make_backend()
        return _BACKEND


def reset_backend() -> None:
    """Drop the cached backend (tests / config reload)."""
    global _BACKEND
    with _LOCK:
        if _BACKEND is not None:
            _BACKEND.shutdown()
        _BACKEND = None


def start() -> dict[str, Any]:
    """Worker/API start: re-queue stale ``running`` jobs and re-submit ``queued`` ones (in-memory queues
    do not survive a restart; arq's does, and a duplicate submit is harmless thanks to the claim)."""
    stale = recover_stale()
    resubmitted: list[str] = []
    backend = get_backend()
    if backend.name != "arq":
        with session_scope() as s:
            ids = list(s.scalars(select(Job.id).where(Job.status == "queued").order_by(Job.created_at)))
        for jid in ids:
            backend.submit(jid)
            resubmitted.append(jid)
    return {"backend": backend.name, "stale_requeued": stale, "resubmitted": resubmitted}


def stop() -> None:
    reset_backend()


# --------------------------------------------------------------------------------------------
# Enqueue / run
# --------------------------------------------------------------------------------------------
def queue_depth() -> int:
    with session_scope() as s:
        return int(
            s.scalar(select(func.count()).select_from(Job).where(Job.status.in_(["queued", "running"]))) or 0
        )


def check_capacity(n: int = 1) -> None:
    limit = get_settings().queue_max
    depth = queue_depth()
    if depth + n > limit:
        raise QueueFull(
            f"job queue is full ({depth}/{limit}); retry later",
            retry_after_s=5,
            details={"queued_or_running": depth, "limit": limit, "requested": n},
        )


def enqueue(
    kind: str,
    payload: dict[str, Any],
    *,
    round_id: str | None = None,
    sync: bool = False,
    batch_id: str | None = None,
    request_id: str | None = None,
    check: bool = True,
) -> str:
    if kind not in _HANDLERS:
        raise KeyError(f"no handler for job kind {kind!r}")
    if not sync and check:
        check_capacity(1)
    with session_scope() as s:
        j = Job(kind=kind, payload=payload, round_id=round_id, batch_id=batch_id, request_id=request_id)
        s.add(j)
        s.flush()
        job_id = j.id
    if sync:
        run_job(job_id)
    else:
        get_backend().submit(job_id)
    return job_id


def enqueue_batch(
    kind: str,
    payloads: list[dict[str, Any]],
    *,
    round_ids: Sequence[str | None] | None = None,
    sync: bool = False,
) -> tuple[str, list[str]]:
    """One batch id for many jobs; capacity is checked for the whole batch before any job is created."""
    if not sync:
        check_capacity(len(payloads))
    batch_id = new_id("bat")
    ids = []
    for i, p in enumerate(payloads):
        rid = round_ids[i] if round_ids else p.get("round_id")
        ids.append(enqueue(kind, p, round_id=rid, sync=sync, batch_id=batch_id, check=False))
    return batch_id, ids


def _claim(job_id: str) -> tuple[str, dict[str, Any]] | None:
    """Atomically move queued → running. Returns (kind, payload) or None when someone else has it."""
    with session_scope() as s:
        res = s.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == "queued")
            .values(status="running", started_at=_utcnow(), attempts=Job.attempts + 1, worker=worker_name())
        )
        if int(getattr(res, "rowcount", 0)) != 1:
            return None
        j = s.get(Job, job_id)
        assert j is not None
        return j.kind, dict(j.payload)


def run_job(job_id: str) -> bool:
    """Execute one job in this process. Safe to call from any backend, any number of times."""
    from forma.jobs import handlers as _h  # noqa: F401  (register handlers in worker processes)

    claimed = _claim(job_id)
    if claimed is None:
        return False
    kind, payload = claimed
    ctx = get_context()
    prog = JobProgress(job_id)
    try:
        with _trace_job(job_id, kind):
            result = _HANDLERS[kind](ctx, payload, prog)
        with session_scope() as s:
            j = s.get(Job, job_id)
            if j:
                j.status = "done"
                j.result = {**result, "stages": prog.stages} if prog.stages else result
                j.progress = 100
                j.message = None
                j.finished_at = _utcnow()
        return True
    except Exception as e:
        log.error("job failed", job=job_id, kind=kind, error=str(e))
        with session_scope() as s:
            j = s.get(Job, job_id)
            if j:
                j.status = "failed"
                j.error = f"{e}\n{traceback.format_exc()[-2000:]}"
                j.finished_at = _utcnow()
            rid = payload.get("round_id")
            if rid:
                r = s.get(ImportRound, rid)
                if r:
                    r.status = "failed"
                    r.summary = {**(r.summary or {}), "error": str(e)[:500]}
        return True


def _trace_job(job_id: str, kind: str) -> Any:
    from forma.core.telemetry import span

    return span("job.run", job_id=job_id, job_kind=kind)


# --------------------------------------------------------------------------------------------
# Recovery / retry / status
# --------------------------------------------------------------------------------------------
def recover_stale(stale_s: int | None = None) -> list[str]:
    """Re-queue ``running`` jobs whose worker stopped reporting; give up after ``job_max_attempts``."""
    s_ = get_settings()
    cutoff = _utcnow() - dt.timedelta(seconds=stale_s if stale_s is not None else s_.job_stale_s)
    requeued: list[str] = []
    with session_scope() as s:
        for j in s.scalars(select(Job).where(Job.status == "running")):
            started = _aware(j.started_at) or _aware(j.created_at)
            if started is None or started > cutoff:
                continue
            if j.attempts >= s_.job_max_attempts:
                j.status = "failed"
                j.error = f"worker died; gave up after {j.attempts} attempts"
                j.finished_at = _utcnow()
                if j.round_id:
                    r = s.get(ImportRound, j.round_id)
                    if r:
                        r.status = "failed"
                        r.summary = {**(r.summary or {}), "error": j.error}
                continue
            j.status = "queued"
            j.message = f"re-queued after stale worker ({j.worker})"
            requeued.append(j.id)
    backend = get_backend()
    for jid in requeued:
        backend.submit(jid)
    return requeued


def retry_job(job_id: str) -> str:
    """Put a failed job back on the queue (same row, same payload; stages already done are skipped)."""
    with session_scope() as s:
        j = s.get(Job, job_id)
        if j is None:
            raise NotFound("job not found")
        if j.status != "failed":
            raise Conflict(f"job is {j.status}; only failed jobs can be retried")
        j.status = "queued"
        j.error = None
        j.progress = 0
        j.message = "retry requested"
        j.finished_at = None
    get_backend().submit(job_id)
    return job_id


def _job_dict(j: Job) -> dict[str, Any]:
    return {
        "id": j.id,
        "kind": j.kind,
        "status": j.status,
        "progress": j.progress,
        "message": j.message,
        "result": j.result,
        "error": j.error,
        "round_id": j.round_id,
        "batch_id": j.batch_id,
        "attempts": j.attempts,
        "worker": j.worker,
        "created_at": j.created_at.isoformat(),
        "started_at": j.started_at.isoformat() if j.started_at else None,
        "finished_at": j.finished_at.isoformat() if j.finished_at else None,
        "duration_ms": _duration_ms(j),
    }


def _duration_ms(j: Job) -> float | None:
    a, b = _aware(j.started_at), _aware(j.finished_at)
    if a and b:
        return round((b - a).total_seconds() * 1000, 1)
    return None


def job_status(job_id: str) -> dict[str, Any]:
    with session_scope() as s:
        j = s.get(Job, job_id)
        if j is None:
            raise KeyError(job_id)
        return _job_dict(j)


def list_jobs(
    *, status: str | None = None, kind: str | None = None, limit: int = 100, offset: int = 0
) -> list[dict[str, Any]]:
    with session_scope() as s:
        q = select(Job).order_by(Job.created_at.desc()).limit(limit).offset(offset)
        if status:
            q = q.where(Job.status == status)
        if kind:
            q = q.where(Job.kind == kind)
        return [_job_dict(j) for j in s.scalars(q)]


def batch_status(batch_id: str) -> dict[str, Any]:
    with session_scope() as s:
        jobs = list(s.scalars(select(Job).where(Job.batch_id == batch_id).order_by(Job.created_at)))
        if not jobs:
            raise KeyError(batch_id)
        by: dict[str, int] = {}
        for j in jobs:
            by[j.status] = by.get(j.status, 0) + 1
        finished = sum(1 for j in jobs if j.status in TERMINAL)
        progress = round(sum(j.progress for j in jobs) / len(jobs))
        return {
            "batch_id": batch_id,
            "total": len(jobs),
            "by_status": by,
            "finished": finished,
            "progress": progress,
            "done": finished == len(jobs),
            "jobs": [
                {
                    "id": j.id,
                    "round_id": j.round_id,
                    "status": j.status,
                    "progress": j.progress,
                    "error": (j.error or "").splitlines()[0] if j.error else None,
                    "duration_ms": _duration_ms(j),
                }
                for j in jobs
            ],
        }


def queue_stats() -> dict[str, Any]:
    with session_scope() as s:
        rows = s.execute(select(Job.status, func.count()).group_by(Job.status)).all()
        oldest_queued = s.scalar(select(func.min(Job.created_at)).where(Job.status == "queued"))
    by = {str(k): int(v) for k, v in rows}
    oq = _aware(oldest_queued)
    return {
        "by_status": by,
        "queued": by.get("queued", 0),
        "running": by.get("running", 0),
        "failed": by.get("failed", 0),
        "oldest_queued_age_s": round((_utcnow() - oq).total_seconds(), 1) if oq else None,
        "limit": get_settings().queue_max,
        **get_backend().stats(),
    }


# --------------------------------------------------------------------------------------------
# arq worker definition (``forma worker``)
# --------------------------------------------------------------------------------------------
async def arq_run_job(ctx: dict[str, Any], job_id: str) -> bool:
    import asyncio

    return await asyncio.to_thread(run_job, job_id)


async def _arq_startup(ctx: dict[str, Any]) -> None:
    log.info("arq worker start", **start())


def arq_worker_settings() -> type:
    from arq.connections import RedisSettings

    s = get_settings()

    class WorkerSettings:
        functions = [arq_run_job]
        redis_settings = RedisSettings.from_dsn(s.redis_url)
        max_jobs = s.workers
        job_timeout = max(600, s.job_stale_s)
        on_startup = _arq_startup
        keep_result = 3600

    return WorkerSettings
