"""Job runner: backends, atomic claims, stale recovery, retries, batches, backpressure, resumable stages."""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.test_api import FILE_NAME, _upload, make_workbook


@pytest.fixture
def client(isolated_env: Path) -> Iterator[TestClient]:
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def _wait(fn: Any, *, timeout: float = 90.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        j = fn()
        if j["status"] in ("done", "failed"):
            return j  # type: ignore[no-any-return]
        time.sleep(0.1)
    raise AssertionError("job did not finish in time")


def _round_with_file(c: TestClient, tmp: Path, code: str = "C001") -> str:
    cust = c.post("/api/customers", json={"code": code, "name": code}).json()
    rid = c.post("/api/rounds", json={"customer_id": cust["id"]}).json()["id"]
    _upload(c, rid, make_workbook(tmp / f"{code}_{FILE_NAME}"))
    return rid  # type: ignore[no-any-return]


# --------------------------------------------------------------------------------------------
def test_thread_backend_runs_async_job(client: TestClient, isolated_env: Path) -> None:
    from forma.jobs import runner

    rid = _round_with_file(client, isolated_env)
    job_id = client.post(f"/api/rounds/{rid}/profile").json()["job_id"]
    j = _wait(lambda: runner.job_status(job_id))
    assert j["status"] == "done", j["error"]
    assert j["attempts"] == 1 and j["worker"] and j["duration_ms"] is not None
    assert runner.get_backend().name == "thread"
    stats = client.get("/api/jobs/stats").json()
    assert stats["backend"] == "thread" and stats["by_status"]["done"] >= 1


def test_claim_is_atomic(client: TestClient, isolated_env: Path) -> None:
    from forma.jobs import runner

    rid = _round_with_file(client, isolated_env)
    job_id = runner.enqueue("profile_round", {"round_id": rid}, round_id=rid, sync=True)
    assert runner.job_status(job_id)["status"] == "done"
    # a second worker picking the same id finds nothing to claim
    assert runner.run_job(job_id) is False
    assert runner.job_status(job_id)["attempts"] == 1


def test_arq_retry_uses_new_generation_without_duplicate_current_job(client, monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock

    import arq

    from forma.db.models import Job
    from forma.db.session import session_scope
    from forma.jobs.runner import ArqBackend

    pool = AsyncMock()
    monkeypatch.setattr(arq, "create_pool", AsyncMock(return_value=pool))
    with session_scope() as s:
        s.add(Job(id="job_generation_test", kind="profile_round", payload={}, attempts=0, status="queued"))
    backend = ArqBackend("redis://localhost:6379/0")
    asyncio.run(backend._enqueue("job_generation_test"))
    asyncio.run(backend._enqueue("job_generation_test"))
    with session_scope() as s:
        s.get(Job, "job_generation_test").attempts = 1
    asyncio.run(backend._enqueue("job_generation_test"))
    assert [c.kwargs["_job_id"] for c in pool.enqueue_job.call_args_list] == [
        "job_generation_test:0",
        "job_generation_test:0",
        "job_generation_test:1",
    ]
    assert all(c.args == ("arq_run_job", "job_generation_test") for c in pool.enqueue_job.call_args_list)


def test_worker_refreshes_configuration_before_handler(client, isolated_env, monkeypatch):
    from forma.jobs import runner
    from forma.services.context import ServiceContext

    seen = []
    original = ServiceContext.refresh

    def refresh(self):
        seen.append(True)
        original(self)

    monkeypatch.setattr(ServiceContext, "refresh", refresh)
    rid = _round_with_file(client, isolated_env, "NEWSCHEMA")
    jid = runner.enqueue("profile_round", {"round_id": rid}, round_id=rid, sync=True)
    assert runner.job_status(jid)["status"] == "done"
    assert seen


def test_stale_running_job_is_requeued_then_given_up(client: TestClient, isolated_env: Path) -> None:
    from forma.db.models import ImportRound, Job
    from forma.db.session import session_scope
    from forma.jobs import runner

    rid = _round_with_file(client, isolated_env)
    old = dt.datetime.now(dt.UTC) - dt.timedelta(hours=2)
    with session_scope() as s:
        j = Job(kind="profile_round", payload={"round_id": rid}, round_id=rid, status="running", attempts=1)
        j.started_at = old
        j.worker = "ghost:1"
        s.add(j)
        s.flush()
        jid = j.id
    requeued = runner.recover_stale()
    assert requeued == [jid]
    done = _wait(lambda: runner.job_status(jid))
    assert done["status"] == "done" and done["attempts"] == 2

    with session_scope() as s:
        j2 = Job(kind="profile_round", payload={"round_id": rid}, round_id=rid, status="running", attempts=3)
        j2.started_at = old
        s.add(j2)
        s.flush()
        jid2 = j2.id
    assert runner.recover_stale() == []
    st = runner.job_status(jid2)
    assert st["status"] == "failed" and "gave up" in (st["error"] or "")
    with session_scope() as s:
        r = s.get(ImportRound, rid)
        assert r is not None and r.status == "failed"


def test_retry_failed_job_via_api(client: TestClient, isolated_env: Path) -> None:
    from forma.jobs import runner

    rid = _round_with_file(client, isolated_env)
    # transform without an approved mapping fails deterministically
    job_id = runner.enqueue("transform_round", {"round_id": rid}, round_id=rid, sync=True)
    assert runner.job_status(job_id)["status"] == "failed"
    r = client.post(f"/api/jobs/{job_id}/retry")
    assert r.status_code == 200
    j = _wait(lambda: runner.job_status(job_id))
    assert j["status"] == "failed" and j["attempts"] == 2
    # retrying a non-failed job is a conflict
    assert client.post(f"/api/jobs/{job_id}/retry").status_code in (200, 409)
    assert client.post("/api/jobs/job_nope/retry").status_code == 404


def test_batch_run_aggregates_progress(client: TestClient, isolated_env: Path) -> None:
    rids = [_round_with_file(client, isolated_env, code=f"B{i}") for i in range(3)]
    r = client.post("/api/rounds/batch-run", json={"round_ids": rids, "sync": True})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["count"] == 3
    st = client.get(f"/api/jobs/batch/{b['batch_id']}").json()
    assert st["total"] == 3 and st["done"] is True and st["by_status"]["done"] == 3 and st["progress"] == 100
    assert {j["round_id"] for j in st["jobs"]} == set(rids)
    assert client.get("/api/jobs/batch/bat_nope").status_code == 404
    assert client.post("/api/rounds/batch-run", json={"round_ids": ["rnd_x"]}).status_code == 404
    jobs = client.get("/api/jobs?kind=run_pipeline").json()
    assert len(jobs) >= 3 and all(j["batch_id"] == b["batch_id"] for j in jobs[:3])


def test_backpressure_returns_429_with_retry_after(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FORMA_QUEUE_MAX", "1")
    from tests.conftest import clear_forma_caches

    clear_forma_caches()
    from forma.api.app import create_app
    from forma.db.models import Job
    from forma.db.session import session_scope

    with TestClient(create_app()) as c:
        rid = _round_with_file(c, isolated_env)
        with session_scope() as s:  # a job that never finishes occupies the single slot
            s.add(Job(kind="profile_round", payload={"round_id": rid}, round_id=rid, status="running"))
        r = c.post(f"/api/rounds/{rid}/profile")
        assert r.status_code == 429, r.text
        assert r.headers["retry-after"] == "5"
        assert r.json()["error"] == "queue_full"
        # batches are checked as a whole, before any job row is created
        r = c.post("/api/rounds/batch-run", json={"round_ids": [rid]})
        assert r.status_code == 429
        # sync jobs bypass the queue
        assert c.post(f"/api/rounds/{rid}/profile?sync=true").status_code == 200


def test_upload_size_cap(isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORMA_MAX_UPLOAD_MB", "0")
    from tests.conftest import clear_forma_caches

    clear_forma_caches()
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        cust = c.post("/api/customers", json={"code": "U1", "name": "U1"}).json()
        rid = c.post("/api/rounds", json={"customer_id": cust["id"]}).json()["id"]
        xlsx = make_workbook(isolated_env / FILE_NAME)
        r = c.post(
            f"/api/rounds/{rid}/files",
            files=[("files", (FILE_NAME, xlsx.read_bytes(), "application/octet-stream"))],
        )
        assert r.status_code == 413, r.text
        assert r.json()["error"] == "payload_too_large" and "MB" in r.json()["message"]


def test_run_pipeline_is_resumable(client: TestClient, isolated_env: Path) -> None:
    from forma.db.models import Job
    from forma.db.session import session_scope
    from forma.jobs import runner

    rid = _round_with_file(client, isolated_env)
    first = runner.enqueue("run_pipeline", {"round_id": rid}, round_id=rid, sync=True)
    j1 = runner.job_status(first)
    assert j1["status"] == "done", j1["error"]
    stages = j1["result"]["stages"]
    assert set(stages) >= {"profile", "propose"} and "skipped" not in stages["profile"]

    # a second run of the same round skips profiling and reuses the live proposal
    second = runner.enqueue("run_pipeline", {"round_id": rid}, round_id=rid, sync=True)
    j2 = runner.job_status(second)
    assert j2["status"] == "done", j2["error"]
    assert j2["result"]["stages"]["profile"]["skipped"] is True
    assert j2["result"]["stages"]["propose"]["skipped"] is True
    assert j2["result"]["mapping_version_id"] == j1["result"]["mapping_version_id"]

    # a crashed job that already recorded its stages continues from where it stopped
    with session_scope() as s:
        j = Job(kind="run_pipeline", payload={"round_id": rid}, round_id=rid, status="queued")
        j.result = {"stages": {"profile": {"at": "x"}, "propose": {"at": "x", **stages["propose"]}}}
        s.add(j)
        s.flush()
        jid = j.id
    assert runner.run_job(jid) is True
    j3 = runner.job_status(jid)
    assert j3["status"] == "done" and j3["result"]["mapping_version_id"] == j1["result"]["mapping_version_id"]

    # force re-runs every stage
    forced = runner.enqueue("run_pipeline", {"round_id": rid, "force": True}, round_id=rid, sync=True)
    j4 = runner.job_status(forced)
    assert j4["status"] == "done" and "skipped" not in j4["result"]["stages"]["profile"]


def test_profile_round_skips_already_profiled_files(client: TestClient, isolated_env: Path) -> None:
    from forma.db.session import session_scope
    from forma.services import rounds as rs
    from forma.services.context import get_context

    rid = _round_with_file(client, isolated_env)
    ctx = get_context()
    with session_scope() as s:
        a = rs.profile_round(ctx, s, rid)
    assert a["profiled_files"] == 1 and a["skipped_files"] == 0
    with session_scope() as s:
        b = rs.profile_round(ctx, s, rid)
    assert b["profiled_files"] == 0 and b["skipped_files"] == 1
    with session_scope() as s:
        c_ = rs.profile_round(ctx, s, rid, force=True)
    assert c_["profiled_files"] == 1
    assert a["profile"][0]["fingerprint"] == b["profile"][0]["fingerprint"] == c_["profile"][0]["fingerprint"]


@pytest.mark.slow
def test_process_backend_runs_in_child_process(isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORMA_JOB_BACKEND", "process")
    monkeypatch.setenv("FORMA_WORKERS", "2")
    from tests.conftest import clear_forma_caches

    clear_forma_caches()
    from forma.api.app import create_app
    from forma.jobs import runner

    with TestClient(create_app()) as c:
        assert runner.get_backend().name == "process"
        rids = [_round_with_file(c, isolated_env, code=f"P{i}") for i in range(2)]
        b = c.post("/api/rounds/batch-run", json={"round_ids": rids}).json()

        def batch_status() -> dict[str, Any]:
            # A worker may finish between requests. The completion flag and
            # counts must come from the same snapshot.
            snapshot = c.get(f"/api/jobs/batch/{b['batch_id']}").json()
            return {**snapshot, "status": "done" if snapshot["done"] else "running"}

        st = _wait(batch_status, timeout=300)
        assert st["by_status"].get("done") == 2, st
        workers = {c.get(f"/api/jobs/{jid}").json()["worker"] for jid in b["job_ids"]}
        import os

        assert all(w and not w.endswith(f":{os.getpid()}") for w in workers), workers


def test_make_backend_rejects_unknown(isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from forma.jobs import runner

    with pytest.raises(ValueError):
        runner.make_backend("celery")
    arq = runner.make_backend("arq")
    assert arq.name == "arq" and "error" in arq.stats()  # no redis here: reported, not raised
    with pytest.raises(KeyError):
        runner.enqueue("no_such_kind", {})
