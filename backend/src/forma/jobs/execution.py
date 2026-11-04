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
    if (
        not row
        or row[0] != "running"
        or row[1] != execution.token
        or not expiry
        or expiry <= dt.datetime.now(dt.UTC)
    ):
        execution.stopped.set()
        raise JobStopped("Worker lease lost; discarded uncommitted output")


def interruptible_sleep(seconds: float) -> None:
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        check()
        time.sleep(min(0.2, max(0, until - time.monotonic())))
    check()
