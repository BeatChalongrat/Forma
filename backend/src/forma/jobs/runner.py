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
import hashlib
import os
import socket
import threading
import time
import traceback
from collections.abc import Callable, Sequence
from concurrent.futures import Executor, ProcessPoolExecutor, ThreadPoolExecutor
from contextlib import suppress
from typing import Any, Protocol

from sqlalchemy import func, or_, select, text, true, update
from sqlalchemy.orm import Session

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
_REPORT_BACKEND: Backend | None = None
_ASSISTANT_BACKEND: Backend | None = None
_LOCK = threading.Lock()
TERMINAL = ("done", "failed", "cancelled")


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
            s.info["job_bookkeeping"] = True
            j = s.get(Job, self.job_id)
            if j and j.result and isinstance(j.result.get("stages"), dict):
                self.stages = dict(j.result["stages"])

    def update(self, progress: int, message: str | None = None) -> None:
        from forma.jobs.activity import emit

        emit("progress", detail={"message": message, "progress": progress})
        with session_scope() as s:
            s.info["job_bookkeeping"] = True
            j = s.get(Job, self.job_id)
            if j:
                j.progress = progress
                j.message = message

    def done(self, stage: str) -> bool:
        return stage in self.stages

    def mark(self, stage: str, **info: Any) -> None:
        """Persist a finished stage immediately (its own transaction) so a crash later does not redo it."""
        with session_scope() as s:
            s.info["job_bookkeeping"] = True
            self.mark_in(s, stage, **info)

    def mark_in(self, session: Session, stage: str, **info: Any) -> None:
        """Commit a stage receipt atomically with its business output."""
        self.stages[stage] = {"at": _utcnow().isoformat(), **info}
        j = session.get(Job, self.job_id)
        if j:
            j.result = {**(j.result or {}), "stages": dict(self.stages)}


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
        from concurrent.futures.process import BrokenProcessPool

        with self._lock:
            self._inflight += 1
        try:
            fut = self._ensure().submit(run_job, job_id)
        except BrokenProcessPool:
            # a worker process died (OOM, kill): rebuild the pool, the job row stays `running` until
            # recover_stale() re-queues it
            log.warning("worker pool broken; recreating", backend=self.name)
            self._pool = None
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

    def __init__(self, redis_url: str, queue: str = "arq:queue") -> None:
        self.redis_url = redis_url
        self.queue = queue

    def submit(self, job_id: str) -> None:
        import asyncio

        asyncio.run(self._enqueue(job_id))

    async def _enqueue(self, job_id: str) -> None:
        from arq import create_pool
        from arq.connections import RedisSettings
        from arq.constants import (
            in_progress_key_prefix,
            job_key_prefix,
            result_key_prefix,
        )

        with session_scope() as session:
            job = session.get(Job, job_id)
            generation = job.attempts if job else 0
        pool = await create_pool(RedisSettings.from_dsn(self.redis_url))
        try:
            delivery = f"{job_id}:{generation}"
            # Restore a lost queue membership without duplicating a live delivery. A retained result
            # from a delivery that died before claiming the DB row must not block redispatch forever.
            await pool.eval(  # type: ignore[misc]  # arq's Redis subtype uses async eval at runtime
                """
                if redis.call('EXISTS', KEYS[3]) == 0 then
                  redis.call('DEL', KEYS[2])
                  if redis.call('EXISTS', KEYS[1]) == 1 then
                    local t = redis.call('TIME')
                    redis.call('ZADD', KEYS[4], 'NX', tonumber(t[1])*1000, ARGV[1])
                  end
                end
                """,
                4,
                job_key_prefix + delivery,
                result_key_prefix + delivery,
                in_progress_key_prefix + delivery,
                self.queue,
                delivery,
            )
            await pool.enqueue_job(
                "arq_run_job", job_id, generation, _job_id=delivery, _queue_name=self.queue
            )
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
        from arq.constants import health_check_key_suffix

        pool = await create_pool(RedisSettings.from_dsn(self.redis_url))
        try:
            depth = await pool.zcard(self.queue)
            health = await pool.get(self.queue + health_check_key_suffix)
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


def get_backend(kind: str | None = None) -> Backend:
    global _BACKEND, _REPORT_BACKEND, _ASSISTANT_BACKEND
    with _LOCK:
        if kind == "assistant_message":
            if _ASSISTANT_BACKEND is None:
                settings = get_settings()
                _ASSISTANT_BACKEND = (
                    ArqBackend(settings.redis_url, "arq:assistant")
                    if settings.job_backend == "arq"
                    else ThreadBackend(settings.assistant_workers)
                )
            return _ASSISTANT_BACKEND
        if kind == "create_document":
            if _REPORT_BACKEND is None:
                s = get_settings()
                _REPORT_BACKEND = (
                    ArqBackend(s.redis_url, "arq:reports")
                    if s.job_backend == "arq"
                    else ThreadBackend(s.report_workers)
                )
            return _REPORT_BACKEND
        if _BACKEND is None:
            _BACKEND = make_backend()
        return _BACKEND


def reset_backend() -> None:
    """Drop the cached backend (tests / config reload)."""
    global _BACKEND, _REPORT_BACKEND, _ASSISTANT_BACKEND
    _stop_recovery_scheduler()
    with _LOCK:
        if _BACKEND is not None:
            _BACKEND.shutdown()
        _BACKEND = None
        if _REPORT_BACKEND is not None:
            _REPORT_BACKEND.shutdown()
        _REPORT_BACKEND = None
        if _ASSISTANT_BACKEND is not None:
            _ASSISTANT_BACKEND.shutdown()
        _ASSISTANT_BACKEND = None


def start() -> dict[str, Any]:
    """Reconcile finished work, recover stale workers, then restore durable pending jobs to the queue."""
    from forma.jobs.recovery import reconcile_completed_jobs

    # CREATE INDEX CONCURRENTLY in the checkpoint saver must run before business transactions.
    # Doing its first setup inside a mapping transaction can wait on that very transaction.
    if get_settings().mapping_workflow == "graph" and get_settings().worker_queue != "reports":
        from forma.ai.workflow_store import workflow_storage

        with workflow_storage(get_settings(), "startup-schema"):
            pass

    reconciled = reconcile_completed_jobs()
    stale = recover_stale()
    _start_retention_scheduler()
    backend = get_backend()
    resubmitted = dispatch_pending()
    _start_recovery_scheduler()
    return {
        "backend": backend.name,
        "stale_requeued": stale,
        "resubmitted": resubmitted,
        "reconciled": reconciled,
    }


def stop() -> None:
    _stop_retention_scheduler()
    reset_backend()


_RECOVERY_STOP = threading.Event()
_RECOVERY_THREAD: threading.Thread | None = None


def _recovery_loop() -> None:
    while not _RECOVERY_STOP.wait(get_settings().job_recovery_interval_s):
        try:
            recover_stale()
            dispatch_pending()
        except Exception:
            log.exception("job recovery temporarily unavailable; will retry")


def _start_recovery_scheduler() -> None:
    global _RECOVERY_THREAD
    if _RECOVERY_THREAD is not None:
        return
    _RECOVERY_STOP.clear()
    _RECOVERY_THREAD = threading.Thread(target=_recovery_loop, name="forma-job-recovery", daemon=True)
    _RECOVERY_THREAD.start()


def _stop_recovery_scheduler() -> None:
    global _RECOVERY_THREAD
    _RECOVERY_STOP.set()
    if _RECOVERY_THREAD is not None:
        _RECOVERY_THREAD.join(timeout=5)
    _RECOVERY_THREAD = None


def dispatch_pending(only: str | None = None) -> list[str]:
    """Job rows are the outbox. Broker failure must not undo an accepted upload.

    CAS the dispatch receipt so API and worker reconcilers can run together. A lost message is
    redelivered after a bounded interval. Duplicate delivery is safe because execution is fenced.
    """
    cutoff = _utcnow() - dt.timedelta(seconds=get_settings().job_dispatch_retry_s)
    eligible = (Job.status == "queued", or_(Job.dispatched_at.is_(None), Job.dispatched_at < cutoff))
    with session_scope() as s:
        query = select(Job.id).where(*eligible).order_by(Job.created_at).limit(500)
        if only:
            query = query.where(Job.id == only)
        ids = list(s.scalars(query))
    sent = []
    for jid in ids:
        with session_scope() as s:
            changed = s.execute(update(Job).where(Job.id == jid, *eligible).values(dispatched_at=_utcnow()))
            won = getattr(changed, "rowcount", 0) == 1
        if won:
            try:
                with session_scope() as s:
                    kind = s.scalar(select(Job.kind).where(Job.id == jid))
                backend = (
                    get_backend(kind) if kind in ("create_document", "assistant_message") else get_backend()
                )
                backend.submit(jid)
                sent.append(jid)
            except Exception:
                log.exception("job saved; dispatch will retry", job_id=jid)
    return sent


# --------------------------------------------------------------------------------------------
# Scheduled retention (ADR-015): a daemon thread per API/worker process, off unless configured
# --------------------------------------------------------------------------------------------
_RETENTION_STOP = threading.Event()
_RETENTION_THREAD: threading.Thread | None = None


def _retention_loop(interval_s: float, apply: bool) -> None:
    from forma.services import retention

    # The first pass comes soon after start, so a restart does not push it a whole interval away.
    wait_s = min(60.0, interval_s)
    while not _RETENTION_STOP.wait(wait_s):
        wait_s = interval_s
        try:
            ctx = get_context()
            with session_scope() as s:
                if retention.ran_within(s, interval_s * 0.5):
                    continue
                result = retention.run(ctx, s, apply_=apply, actor="retention.scheduler")
            log.info("retention run", apply=apply, **result["counts"])
        except Exception as e:
            log.error("retention run failed", error=str(e)[:300])


def _start_retention_scheduler() -> None:
    global _RETENTION_THREAD
    s = get_settings()
    if s.retention_interval_h <= 0 or _RETENTION_THREAD is not None:
        return
    _RETENTION_STOP.clear()
    _RETENTION_THREAD = threading.Thread(
        target=_retention_loop,
        args=(s.retention_interval_h * 3600.0, s.retention_apply),
        name="forma-retention",
        daemon=True,
    )
    _RETENTION_THREAD.start()


def _stop_retention_scheduler() -> None:
    global _RETENTION_THREAD
    _RETENTION_STOP.set()
    if _RETENTION_THREAD is not None:
        _RETENTION_THREAD.join(timeout=2)
    _RETENTION_THREAD = None


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


def _capacity_in(session: Session, n: int) -> None:
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(736211935)"))
    elif not getattr(session.connection().connection.driver_connection, "in_transaction", False):
        session.execute(text("BEGIN IMMEDIATE"))
    depth = int(
        session.scalar(select(func.count()).select_from(Job).where(Job.status.in_(("queued", "running"))))
        or 0
    )
    limit = get_settings().queue_max
    if depth + n > limit:
        raise QueueFull(
            f"job queue is full ({depth}/{limit}); retry later",
            retry_after_s=5,
            details={"queued_or_running": depth, "limit": limit, "requested": n},
        )


def stage_job(
    session: Session,
    kind: str,
    payload: dict[str, Any],
    *,
    round_id: str | None = None,
    batch_id: str | None = None,
    request_id: str | None = None,
    sync: bool = False,
    check: bool = True,
) -> str:
    """Persist an outbox row WITH the business change. Caller commits before delivery."""
    if kind not in _HANDLERS:
        raise KeyError(f"no handler for job kind {kind!r}")
    if not sync and check:
        _capacity_in(session, 1)
    j = Job(kind=kind, payload=payload, round_id=round_id, batch_id=batch_id, request_id=request_id)
    session.add(j)
    session.flush()
    return j.id


def deliver_jobs(ids: Sequence[str], *, sync: bool = False) -> None:
    """Best effort notification after commit; the durable outbox remains authoritative."""
    for jid in ids:
        if sync:
            run_job(jid)
        else:
            try:
                dispatch_pending(jid)
            except Exception:
                log.warning("job accepted; delivery will be retried", job_id=jid)


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
    with session_scope() as s:
        job_id = stage_job(
            s,
            kind,
            payload,
            round_id=round_id,
            batch_id=batch_id,
            request_id=request_id,
            sync=sync,
            check=check,
        )
    deliver_jobs([job_id], sync=sync)
    return job_id


def enqueue_batch(
    kind: str,
    payloads: list[dict[str, Any]],
    *,
    round_ids: Sequence[str | None] | None = None,
    sync: bool = False,
    request_id: str | None = None,
) -> tuple[str, list[str]]:
    """One batch id for many jobs; capacity is checked for the whole batch before any job is created."""
    if kind not in _HANDLERS:
        raise KeyError(f"no handler for job kind {kind!r}")
    batch_id = new_id("bat")
    ids = []
    with session_scope() as s:
        if not sync:
            _capacity_in(s, len(payloads))
        for i, p in enumerate(payloads):
            rid = round_ids[i] if round_ids else p.get("round_id")
            j = Job(kind=kind, payload=p, round_id=rid, batch_id=batch_id, request_id=request_id)
            s.add(j)
            s.flush()
            ids.append(j.id)
    for jid in ids:
        if sync:
            run_job(jid)
        else:
            dispatch_pending(jid)
    return batch_id, ids


def _claim(
    job_id: str, generation: int | None = None, lease_token: str | None = None
) -> tuple[str, dict[str, Any], str] | None:
    """Atomically move queued → running. Returns (kind, payload) or None when someone else has it."""
    with session_scope() as s:
        if s.get_bind().dialect.name == "sqlite":
            s.execute(text("BEGIN IMMEDIATE"))
        candidate = s.get(Job, job_id)
        if candidate is None or candidate.status != "queued":
            return None
        resource = candidate.round_id or str((candidate.payload or {}).get("item_id") or job_id)
        if s.get_bind().dialect.name == "postgresql":
            key = int.from_bytes(hashlib.sha256(resource.encode()).digest()[:8], "big", signed=True)
            s.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
        if candidate.round_id:
            busy = s.scalar(
                select(Job.id)
                .where(Job.round_id == candidate.round_id, Job.status == "running", Job.id != job_id)
                .limit(1)
            )
            if busy:
                return None
        token = lease_token or new_id("lease")
        now = _utcnow()
        res = s.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == "queued")
            .where(Job.attempts == generation if generation is not None else true())
            .values(
                status="running",
                started_at=now,
                attempts=Job.attempts + 1,
                worker=worker_name(),
                lease_token=token,
                heartbeat_at=now,
                lease_expires_at=now + dt.timedelta(seconds=get_settings().job_lease_s),
                error=None,
                finished_at=None,
            )
        )
        if int(getattr(res, "rowcount", 0)) != 1:
            return None
        j = s.get(Job, job_id)
        assert j is not None
        return j.kind, dict(j.payload), token


def _heartbeat(job_id: str, token: str, stopped: threading.Event, deadline: float) -> None:
    interval = max(0.5, get_settings().job_lease_s / 3)
    while not stopped.wait(interval):
        if time.monotonic() >= deadline:
            stopped.set()
            return
        try:
            now = _utcnow()
            with session_scope() as s:
                changed = s.execute(
                    update(Job)
                    .where(
                        Job.id == job_id,
                        Job.status == "running",
                        Job.lease_token == token,
                        Job.lease_expires_at > now,
                    )
                    .values(
                        heartbeat_at=now,
                        lease_expires_at=now + dt.timedelta(seconds=get_settings().job_lease_s),
                    )
                )
                owned = getattr(changed, "rowcount", 0) == 1
            if not owned:
                stopped.set()
        except Exception:
            # Never invent a renewal when the DB is unavailable. Commit fencing checks real expiry.
            log.warning("heartbeat unavailable", job_id=job_id)


def run_job(job_id: str, generation: int | None = None, lease_token: str | None = None) -> bool:
    """Execute one job in this process. Safe to call from any backend, any number of times."""
    from forma.jobs import activity
    from forma.jobs import handlers as _h  # noqa: F401  (register handlers in worker processes)
    from forma.jobs.execution import Execution, JobStopped, current
    from forma.jobs.recovery import completed_job_result, record_reconciliation

    claimed = _claim(job_id, generation, lease_token)
    if claimed is None:
        return False
    kind, payload, lease = claimed
    ctx = get_context()
    prog = JobProgress(job_id)
    import structlog

    from forma.core import metrics

    with session_scope() as s:
        j0 = s.get(Job, job_id)
        req_id = j0.request_id if j0 else None
        attempt = j0.attempts if j0 else 0
    structlog.contextvars.bind_contextvars(job_id=job_id, job_kind=kind, request_id=req_id)
    t0 = time.perf_counter()
    execution = Execution(job_id, lease, time.monotonic() + get_settings().job_timeout_s)
    pulse = threading.Thread(
        target=_heartbeat, args=(job_id, lease, execution.stopped, execution.deadline), daemon=True
    )
    pulse.start()
    activity_token = activity.context.set((job_id, attempt))
    activity.emit("job", "started", detail={"kind": kind, "attempt": attempt})
    try:
        scope = current.set(execution)
        try:
            with _trace_job(job_id, kind):
                ctx.refresh()  # Read config committed before this job was enqueued.
                with session_scope() as s:
                    job = s.get(Job, job_id)
                    completed = completed_job_result(s, job) if job else None
                result = completed if completed is not None else _HANDLERS[kind](ctx, payload, prog)
        finally:
            current.reset(scope)
        with session_scope() as s:
            j = s.scalar(
                select(Job)
                .where(
                    Job.id == job_id,
                    Job.status == "running",
                    Job.lease_token == lease,
                    Job.lease_expires_at > _utcnow(),
                )
                .with_for_update()
            )
            if j:
                j.status = "done"
                j.result = {**result, "stages": prog.stages} if prog.stages else result
                j.progress = 100
                j.message = None
                j.finished_at = _utcnow()
                j.lease_token = None
                j.lease_expires_at = None
                if result.get("reconciled"):
                    j.round_id = result["round_id"]
                    j.message = "Reconciled with existing published output"
                    record_reconciliation(s, job_id, result)
        metrics.observe_job(kind, "done", time.perf_counter() - t0)
        activity.emit("job", "completed", detail={"result": result, "committed": j is not None})
        return True
    except (Exception, JobStopped) as e:
        log.error("job failed", job=job_id, kind=kind, error=str(e))
        with session_scope() as s:
            rid = s.scalar(select(Job.round_id).where(Job.id == job_id))
            if rid:
                s.scalar(select(ImportRound).where(ImportRound.id == rid).with_for_update())
            j = s.scalar(
                select(Job)
                .where(Job.id == job_id, Job.status == "running", Job.lease_token == lease)
                .with_for_update()
            )
            if j:
                j.status = "failed"
                j.error = f"{e}\n{traceback.format_exc()[-2000:]}"
                j.finished_at = _utcnow()
                j.lease_token = None
                j.lease_expires_at = None
            if j and rid:
                r = s.get(ImportRound, rid)
                if r and r.status not in ("cancelled", "moved", "superseded", "published"):
                    r.status = "failed"
                    r.summary = {**(r.summary or {}), "error": str(e)[:500]}
        metrics.observe_job(kind, "failed", time.perf_counter() - t0)
        activity.emit("job", "failed", detail={"error": str(e)[:1000]})
        return True
    finally:
        activity.context.reset(activity_token)
        execution.stopped.set()
        pulse.join(timeout=2)
        structlog.contextvars.unbind_contextvars("job_id", "job_kind", "request_id")


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
        candidates = list(s.execute(select(Job.id, Job.round_id).where(Job.status == "running")))
    for jid, rid in candidates:
        with session_scope() as s:
            # Do not hold job locks while waiting for a business transaction's round lock.
            if (
                rid
                and s.scalar(
                    select(ImportRound.id).where(ImportRound.id == rid).with_for_update(skip_locked=True)
                )
                is None
            ):
                continue
            j = s.scalar(
                select(Job).where(Job.id == jid, Job.status == "running").with_for_update(skip_locked=True)
            )
            if j is None:
                continue
            started = _aware(j.started_at) or _aware(j.created_at)
            expiry = _aware(j.lease_expires_at)
            if (expiry and expiry > _utcnow()) or (not expiry and (started is None or started > cutoff)):
                continue
            exhausted = j.attempts >= s_.job_max_attempts
            failure = f"worker died; gave up after {j.attempts} attempts"
            changed = s.execute(
                update(Job)
                .where(
                    Job.id == j.id,
                    Job.status == "running",
                    Job.lease_token == j.lease_token,
                    Job.lease_expires_at == j.lease_expires_at,
                )
                .values(
                    status="failed" if exhausted else "queued",
                    lease_token=None,
                    lease_expires_at=None,
                    dispatched_at=None,
                    error=failure if exhausted else None,
                    finished_at=_utcnow() if exhausted else None,
                    message="ประมวลผลหยุดชั่วคราว · ส่งเข้าคิวเพื่อลองต่อจากขั้นตอนเดิม",
                )
                .execution_options(synchronize_session=False)
            )
            if getattr(changed, "rowcount", 0) != 1:
                continue
            if exhausted:
                if j.round_id:
                    r = s.get(ImportRound, j.round_id)
                    if r and r.status not in ("cancelled", "moved", "superseded", "published"):
                        r.status = "failed"
                        r.summary = {**(r.summary or {}), "error": failure}
                continue
            requeued.append(j.id)
    for jid in requeued:
        dispatch_pending(jid)
    return requeued


def retry_job(job_id: str) -> str:
    """Put a failed job back on the queue (same row, same payload; stages already done are skipped)."""
    with session_scope() as s:
        rid = s.scalar(select(Job.round_id).where(Job.id == job_id))
        if rid:
            s.scalar(select(ImportRound).where(ImportRound.id == rid).with_for_update())
        j = s.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if j is None:
            raise NotFound("job not found")
        if j.status not in ("failed", "cancelled"):
            raise Conflict(f"job is {j.status}; only failed or stopped jobs can be retried")
        r = s.get(ImportRound, j.round_id) if j.round_id else None
        if r and r.status in ("cancelled", "superseded", "moved"):
            raise Conflict("This file is no longer active; open the current file instead")
        j.status = "queued"
        j.error = None
        j.progress = 0
        j.message = "retry requested"
        j.finished_at = None
        j.dispatched_at = None
        j.lease_token = None
        j.lease_expires_at = None
    dispatch_pending(job_id)
    return job_id


def cancel_job(job_id: str, *, actor: str = "operator") -> str:
    from forma.db.models import AuditLog

    with session_scope() as s:
        # Commit revocation before touching the source: the worker may hold its round lock
        # while waiting on an external call. Cancellation must wake the parent immediately.
        j = s.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if j is None:
            raise NotFound("job not found")
        if j.status in TERMINAL:
            raise Conflict("This job has already finished")
        j.status = "cancelled"
        j.lease_token = None
        j.lease_expires_at = None
        j.finished_at = _utcnow()
        j.message = "หยุดการประมวลผลแล้ว · ขั้นตอนที่เสร็จยังเก็บไว้"
        s.add(AuditLog(actor=actor, action="job.cancel", entity="job", entity_id=job_id, details={}))
    _mark_stopped_source(job_id)
    return job_id


def _mark_stopped_source(job_id: str) -> None:
    """Best effort now; the ARQ parent repeats this after terminating the child and releasing locks."""
    from forma.db.models import BatchItem

    with session_scope() as s:
        j = s.get(Job, job_id)
        if j is None or j.status != "cancelled":
            return
        r = (
            s.scalar(
                select(ImportRound).where(ImportRound.id == j.round_id).with_for_update(skip_locked=True)
            )
            if j.round_id
            else None
        )
        # retry_job takes the round lock before changing the job state.
        s.refresh(j)
        if j.status != "cancelled":
            return
        if r and r.status in ("uploaded", "profiled", "auto_approved", "approved"):
            r.status = "failed"
            r.summary = {**(r.summary or {}), "error": "หยุดประมวลผลแล้ว · กดทำต่อได้โดยไม่ต้องอัปโหลดใหม่"}
        if j.kind == "process_batch_item" and not j.round_id:
            item = s.scalar(
                select(BatchItem)
                .where(BatchItem.id == (j.payload or {}).get("item_id"))
                .with_for_update(skip_locked=True)
            )
            if item and not item.round_id:
                item.status = "failed"
                item.error = "หยุดประมวลผลแล้ว · กดทำต่อได้โดยไม่ต้องอัปโหลดใหม่"


def cancel_round_jobs(round_id: str, *, actor: str = "operator") -> None:
    """Stop execution before an API lifecycle operation waits for the source transaction."""
    with session_scope() as s:
        ids = list(
            s.scalars(select(Job.id).where(Job.round_id == round_id, Job.status.in_(("queued", "running"))))
        )
    for jid in ids:
        # A job that finished concurrently needs no cancellation.
        with suppress(Conflict):
            cancel_job(jid, actor=actor)


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
        "heartbeat_at": j.heartbeat_at.isoformat() if j.heartbeat_at else None,
        "lease_expires_at": j.lease_expires_at.isoformat() if j.lease_expires_at else None,
        "can_retry": j.status == "failed"
        or (j.status == "cancelled" and not (j.message or "").startswith("File ")),
        "can_cancel": j.status in ("queued", "running"),
        "duration_ms": _duration_ms(j),
    }


def _duration_ms(j: Job) -> float | None:
    a, b = _aware(j.started_at), _aware(j.finished_at)
    if a and b:
        return round((b - a).total_seconds() * 1000, 1)
    return None


def _visible(s: Session, j: Job, customers: frozenset[str] | None) -> bool:
    """A principal limited to some customers sees a job only through a round of theirs (ADR-047)."""
    if customers is None:
        return True
    r = s.get(ImportRound, j.round_id) if j.round_id else None
    return r is not None and r.customer_id in customers


def job_status(
    job_id: str, *, customers: frozenset[str] | None = None, private: bool = False
) -> dict[str, Any]:
    with session_scope() as s:
        j = s.get(Job, job_id)
        if (
            j is None
            or (j.kind in ("create_document", "assistant_message") and not private)
            or not _visible(s, j, customers)
        ):
            raise KeyError(job_id)
        return _job_dict(j)


def list_jobs(
    *,
    status: str | None = None,
    kind: str | None = None,
    limit: int = 100,
    offset: int = 0,
    customers: frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    with session_scope() as s:
        q = (
            select(Job)
            .where(Job.kind.not_in(("create_document", "assistant_message")))
            .order_by(Job.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        if status:
            q = q.where(Job.status == status)
        if kind:
            q = q.where(Job.kind == kind)
        if customers is not None:
            q = q.join(ImportRound, ImportRound.id == Job.round_id).where(
                ImportRound.customer_id.in_(sorted(customers))
            )
        return [_job_dict(j) for j in s.scalars(q)]


def batch_status(batch_id: str, *, customers: frozenset[str] | None = None) -> dict[str, Any]:
    with session_scope() as s:
        found = s.scalars(select(Job).where(Job.batch_id == batch_id).order_by(Job.created_at))
        jobs = [
            j
            for j in found
            if j.kind not in ("create_document", "assistant_message") and _visible(s, j, customers)
        ]
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
async def arq_run_job(ctx: dict[str, Any], job_id: str, generation: int | None = None) -> bool:
    import asyncio
    import sys

    state = await asyncio.to_thread(job_status, job_id, private=True)
    if state["status"] != "queued" or (generation is not None and state["attempts"] != generation):
        return False

    # A process is killable even when an Excel reader/native extension/provider gets stuck.
    # No forked DB pools, threads, credentials in argv, or production-only timeout assumptions.
    lease_token = new_id("lease")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "forma.jobs.child",
        job_id,
        str(generation if generation is not None else -1),
        lease_token,
    )
    waiter = asyncio.create_task(process.wait())
    try:
        async with asyncio.timeout(get_settings().job_timeout_s + 10):
            while process.returncode is None:
                try:
                    await asyncio.wait_for(asyncio.shield(waiter), timeout=1)
                except TimeoutError:
                    try:
                        state = await asyncio.to_thread(job_status, job_id, private=True)
                        if state["status"] == "cancelled":
                            break
                    except Exception:
                        pass  # DB outage: commit fencing fails closed; deadline still applies.
            return process.returncode == 0
    finally:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except TimeoutError:
                process.kill()
                await process.wait()
        # Lost/terminated child: expire only this delivery, never the next worker's lease.
        try:
            await asyncio.to_thread(_expire_child, job_id, lease_token)
            await asyncio.to_thread(_mark_stopped_source, job_id)
        except Exception:
            log.warning("child stopped; lease will expire without renewal", job_id=job_id)
        await waiter


def _expire_child(job_id: str, lease_token: str) -> None:
    with session_scope() as s:
        s.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == "running", Job.lease_token == lease_token)
            .values(lease_expires_at=_utcnow())
        )


async def _arq_startup(ctx: dict[str, Any]) -> None:
    import asyncio

    log.info("arq worker start", **await asyncio.to_thread(start))


async def _arq_shutdown(ctx: dict[str, Any]) -> None:
    import asyncio

    await asyncio.to_thread(stop)


def arq_worker_settings() -> type:
    from arq.connections import RedisSettings

    s = get_settings()

    class WorkerSettings:
        functions = [arq_run_job]
        queue_name = {"reports": "arq:reports", "assistant": "arq:assistant"}.get(s.worker_queue, "arq:queue")
        redis_settings = RedisSettings.from_dsn(s.redis_url)
        max_jobs = {"reports": s.report_workers, "assistant": s.assistant_workers}.get(
            s.worker_queue, s.workers
        )
        job_timeout = s.job_timeout_s + 20
        on_startup = _arq_startup
        on_shutdown = _arq_shutdown
        keep_result = 3600

    return WorkerSettings
