"""Context-local fencing: expired/cancelled workers cannot commit business transactions."""

from __future__ import annotations

import datetime as dt
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from forma.db.models import ImportRound, Job


class JobStopped(BaseException):
    """Control flow, not an LLM error: broad model fallback handlers must not swallow it."""


@dataclass
class Execution:
    job_id: str
    token: str
    deadline: float
    stopped: threading.Event = field(default_factory=threading.Event)


current: ContextVar[Execution | None] = ContextVar("job_execution", default=None)


def check() -> None:
    execution = current.get()
    if execution and (execution.stopped.is_set() or time.monotonic() >= execution.deadline):
        raise JobStopped("Job stopped or execution deadline exceeded")


def guard_commit(session: Session) -> None:
    execution = current.get()
    if execution is None:
        return
    check()
    # Nested read-only service sessions must not lock rows held by their caller. Track flushed
    # and bulk writes as well as currently dirty ORM objects: an empty dirty set alone is unsafe.
    if not (session.info.get("job_writes") or session.new or session.dirty or session.deleted):
        return
    # Lock only at commit, not while the model is thinking. Recovery/cancel serialize on this row.
    with session.no_autoflush:
        round_id = session.scalar(select(Job.round_id).where(Job.id == execution.job_id))
        # Progress uses a separate connection while the business transaction can already hold
        # the round row. It must fence on the job, without waiting for its own outer transaction.
        if round_id and not session.info.get("job_bookkeeping"):
            status = session.scalar(
                select(ImportRound.status).where(ImportRound.id == round_id).with_for_update()
            )
            if status in ("cancelled", "moved", "superseded"):
                raise JobStopped("Source delivery is no longer active")
        row = session.execute(
            select(Job.status, Job.lease_token, Job.lease_expires_at, Job.round_id)
            .where(Job.id == execution.job_id)
            .with_for_update()
        ).first()
    expiry = row[2] if row else None
    if expiry is not None and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=dt.UTC)
    owned = row is not None and row[0] == "running" and row[1] == execution.token
    if owned and (not expiry or expiry <= dt.datetime.now(dt.UTC)):
        owned = _renew_as_only_writer(session, execution)
    if not owned:
        execution.stopped.set()
        raise JobStopped("Worker lease lost; discarded uncommitted output")


def _renew_as_only_writer(session: Session, execution: Execution) -> bool:
    """SQLite has one write lock for the whole file. A transaction that has written holds it until it
    commits, so for as long as a model thinks the heartbeat cannot renew the lease, and no other worker can
    take the job either: taking it is a write. The lease then runs out with nobody else able to own the
    job, and the finished mapping used to be thrown away. This update takes or already holds that lock; if
    the row still carries this worker's token nobody took the job, and the lease is renewed in the same
    transaction as the output. A database with row locks (Postgres) keeps the strict rule: there the
    heartbeat is never blocked by the business transaction, so an expired lease means a dead heartbeat."""
    from sqlalchemy import update

    from forma.core.settings import get_settings

    if session.get_bind().dialect.name != "sqlite":
        return False
    now = dt.datetime.now(dt.UTC)
    renewed = session.execute(
        update(Job)
        .where(Job.id == execution.job_id, Job.status == "running", Job.lease_token == execution.token)
        .values(heartbeat_at=now, lease_expires_at=now + dt.timedelta(seconds=get_settings().job_lease_s))
    )
    return getattr(renewed, "rowcount", 0) == 1


def interruptible_sleep(seconds: float) -> None:
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        check()
        time.sleep(min(0.2, max(0, until - time.monotonic())))
    check()
