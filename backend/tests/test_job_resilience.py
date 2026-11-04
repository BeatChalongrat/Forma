"""Failure boundaries, not just the happy path: leases, outbox, cancellation and stale writes."""

import datetime as dt
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select, update

from forma.db.models import AuditLog, Job
from forma.db.session import init_db, session_scope
from forma.jobs import runner
from forma.jobs.execution import Execution, JobStopped, current


class Broker:
    def __init__(self, fail=False):
        self.fail, self.sent = fail, []

    def submit(self, jid):
        if self.fail:
            raise ConnectionError("simulated broker outage")
        self.sent.append(jid)


@pytest.fixture
def broker(isolated_env, monkeypatch):
    init_db()
    broker = Broker()
    monkeypatch.setattr(runner, "get_backend", lambda: broker)
    return broker


def job(**kwargs):
    with session_scope() as s:
        j = Job(kind="test", payload={}, **kwargs)
        s.add(j)
        s.flush()
        return j.id


def expired(jid):
    with session_scope() as s:
        j = s.get(Job, jid)
        j.lease_expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)


def test_live_heartbeat_protects_long_running_job(broker):
    jid = job()
    runner._claim(jid)
    with session_scope() as s:
        s.get(Job, jid).started_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=1)
    assert runner.recover_stale(stale_s=1) == []
    assert runner.job_status(jid)["status"] == "running"


def test_expired_owner_cannot_commit_business_output_or_progress(broker):
    jid = job()
    _, _, old = runner._claim(jid)
    expired(jid)
    assert runner.recover_stale() == [jid]
    _, _, new = runner._claim(jid)
    assert new != old
    scope = current.set(Execution(jid, old, time.monotonic() + 60))
    try:
        with pytest.raises(JobStopped), session_scope() as s:
            s.add(AuditLog(action="stale-output", entity="test", entity_id=jid))
        with pytest.raises(JobStopped):
            runner.JobProgress(jid).mark("propose", mapping_version_id="stale")
    finally:
        current.reset(scope)
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(AuditLog)) == 0
        assert s.get(Job, jid).result is None


def test_duplicate_delivery_does_not_expire_the_actual_owner(broker):
    jid = job()
    assert runner._claim(jid, 0, "owner")
    assert runner._claim(jid, 0, "duplicate") is None
    runner._expire_child(jid, "duplicate")
    assert runner.recover_stale() == []


@pytest.mark.parametrize("bulk", [False, True])
def test_flushed_and_bulk_writes_are_still_fenced(broker, bulk):
    jid = job()
    _, _, token = runner._claim(jid)
    with session_scope() as s:
        entry = AuditLog(action="original", entity="test", entity_id=jid)
        s.add(entry)
        s.flush()
        aid = entry.id
    runner.cancel_job(jid)
    scope = current.set(Execution(jid, token, time.monotonic() + 60))
    try:
        with pytest.raises(JobStopped), session_scope() as s:
            if bulk:
                s.execute(update(AuditLog).where(AuditLog.id == aid).values(action="late"))
            else:
                s.get(AuditLog, aid).action = "late"
                s.flush()
            assert not s.new and not s.dirty  # the write already left the ORM pending collection
            s.begin_nested().rollback()  # rolling back a savepoint must not forget outer writes
    finally:
        current.reset(scope)
    with session_scope() as s:
        assert s.get(AuditLog, aid).action == "original"


def test_stale_generation_cannot_claim_retry(broker):
    jid = job(attempts=2)
    assert runner._claim(jid, 0) is None
    assert runner._claim(jid, 2)


def test_accepted_job_survives_failed_dispatch_and_lost_message(broker, monkeypatch):
    monkeypatch.setitem(runner._HANDLERS, "test", lambda *a: {})
    broker.fail = True
    jid = runner.enqueue("test", {})
    assert runner.job_status(jid)["status"] == "queued"
    broker.fail = False
    with session_scope() as s:
        s.get(Job, jid).dispatched_at = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)
    assert runner.dispatch_pending() == [jid]
    assert runner.dispatch_pending() == []
    with session_scope() as s:
        s.get(Job, jid).dispatched_at = None  # accepted by broker, but delivery lost
    assert runner.dispatch_pending() == [jid]
    assert runner.run_job(jid)
    assert not runner.run_job(jid)


def test_parallel_recovery_and_claim_have_one_winner(broker):
    jid = job()
    runner._claim(jid)
    expired(jid)
    # SQLite has no row locks. CAS in recovery is needed as well as the PostgreSQL lock.
    with ThreadPoolExecutor(max_workers=2) as pool:
        recovered = list(pool.map(lambda _: runner.recover_stale(), range(2)))
    assert sum(jid in result for result in recovered) == 1
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda _: runner._claim(jid), range(8)))
    assert sum(x is not None for x in claims) == 1


def test_cancel_revokes_output_and_old_worker_cannot_mark_done(broker, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def work(ctx, payload, prog):
        entered.set()
        release.wait(5)
        with session_scope() as s:
            s.add(AuditLog(action="should-not-commit", entity="test", entity_id=prog.job_id))
        return {"wrong": "late result"}

    monkeypatch.setitem(runner._HANDLERS, "test", work)
    jid = job()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(runner.run_job, jid)
        assert entered.wait(5)
        runner.cancel_job(jid)
        release.set()
        future.result(5)
    assert runner.job_status(jid)["status"] == "cancelled"
    with session_scope() as s:
        assert not s.scalar(select(AuditLog.id).where(AuditLog.action == "should-not-commit"))


def test_recovery_stops_after_bounded_attempts(broker):
    jid = job(
        attempts=3,
        status="running",
        lease_token="dead",
        lease_expires_at=dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1),
    )
    assert runner.recover_stale() == []
    assert runner.job_status(jid)["status"] == "failed"
    assert runner.job_status(jid)["can_retry"]


def test_periodic_recovery_runs_without_service_restart(broker, monkeypatch):
    from forma.core.settings import get_settings

    monkeypatch.setattr(get_settings(), "job_recovery_interval_s", 0.02)
    jid = job(
        status="running",
        lease_token="dead",
        attempts=1,
        lease_expires_at=dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1),
    )
    runner._start_recovery_scheduler()
    try:
        until = time.monotonic() + 3
        while time.monotonic() < until and not broker.sent:
            time.sleep(0.02)
        assert broker.sent == [jid]
    finally:
        runner._stop_recovery_scheduler()


def test_stage_output_and_receipt_rollback_together(broker):
    jid = job()
    _, _, token = runner._claim(jid)
    scope = current.set(Execution(jid, token, time.monotonic() + 60))
    try:
        with pytest.raises(ValueError), session_scope() as s:
            s.add(AuditLog(action="not-durable", entity="test", entity_id=jid))
            runner.JobProgress(jid).mark_in(s, "propose", mapping_version_id="not-durable")
            raise ValueError("simulated commit failure")
    finally:
        current.reset(scope)
    with session_scope() as s:
        assert s.get(Job, jid).result is None
        assert not s.scalar(select(AuditLog.id).where(AuditLog.action == "not-durable"))


def test_resource_claim_serializes_two_jobs_for_same_round(broker):
    a, b = job(round_id="same-round"), job(round_id="same-round")
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(runner._claim, [a, b]))
    assert sum(x is not None for x in claims) == 1


def test_capacity_limit_is_atomic_under_parallel_uploads(broker, monkeypatch):
    from forma.core.errors import QueueFull
    from forma.core.settings import get_settings

    monkeypatch.setattr(get_settings(), "queue_max", 3)
    monkeypatch.setitem(runner._HANDLERS, "test", lambda *args: {})

    def submit(_):
        try:
            return runner.enqueue("test", {})
        except QueueFull:
            return None

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(submit, range(10)))
    assert sum(x is not None for x in results) == 3
    assert runner.queue_depth() == 3


def test_stopped_job_can_continue_but_closed_source_cannot(broker, monkeypatch):
    from forma.core.errors import Conflict
    from forma.db.models import Customer, ImportRound

    with session_scope() as s:
        c = Customer(code="STOP", name="Stop test")
        s.add(c)
        s.flush()
        r = ImportRound(customer_id=c.id, status="profiled")
        s.add(r)
        s.flush()
        rid = r.id
    jid = job(round_id=rid)
    runner._claim(jid)
    runner.cancel_job(jid)
    with session_scope() as s:
        assert s.get(ImportRound, rid).status == "failed"
    assert runner.retry_job(jid) == jid
    assert runner.job_status(jid)["status"] == "queued"
    runner.cancel_job(jid)
    with session_scope() as s:
        s.get(ImportRound, rid).status = "superseded"
    with pytest.raises(Conflict, match="no longer active"):
        runner.retry_job(jid)
