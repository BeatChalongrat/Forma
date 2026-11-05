"""Failure injection (ADR-015): a dead worker, a failing storage write, a broken DB commit and an LLM
timeout must never leave a partial dataset visible, and the round ends `failed` with a readable error
(or in review for the LLM case)."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from tests.test_api import FILE_NAME, _run, _upload, make_workbook


@pytest.fixture
def client(isolated_env: Path) -> Iterator[TestClient]:
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def _approved_round(c: TestClient, tmp: Path, code: str = "F1") -> tuple[str, str]:
    cust = c.post("/api/customers", json={"code": code, "name": code}).json()
    rid = c.post("/api/rounds", json={"customer_id": cust["id"]}).json()["id"]
    _upload(c, rid, make_workbook(tmp / f"{code}_{FILE_NAME}"))
    job = _run(c, rid)
    mv = job["result"]["mapping_version_id"]
    c.post(f"/api/mappings/{mv}/approve", json={})
    return rid, cust["id"]


def _dataset_rows(round_id: str) -> list[Any]:
    from forma.db.models import DatasetVersion
    from forma.db.session import session_scope

    with session_scope() as s:
        return list(s.scalars(select(DatasetVersion).where(DatasetVersion.round_id == round_id)))


def _dataset_objects(customer_id: str) -> list[str]:
    from forma.services.context import get_context

    return [o.key for o in get_context().storage.list(f"datasets/{customer_id}/")]


# --------------------------------------------------------------------------------------------
def test_storage_write_failure_leaves_no_partial_dataset(
    client: TestClient, isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from forma.services.context import get_context

    rid, cid = _approved_round(client, isolated_env)
    storage = get_context().storage

    def boom(key: str, src: Any) -> Any:
        raise OSError("disk full")

    monkeypatch.setattr(storage, "put", boom)
    jid = client.post(f"/api/rounds/{rid}/transform?sync=true").json()["job_id"]
    j = client.get(f"/api/jobs/{jid}").json()
    assert j["status"] == "failed" and "disk full" in j["error"]
    r = client.get(f"/api/rounds/{rid}").json()
    assert r["status"] == "failed" and "disk full" in r["summary"]["error"]
    assert _dataset_rows(rid) == [] and _dataset_objects(cid) == []
    assert not list((get_context().tmp_dir).glob("*.tmp"))  # temp file cleaned up
    # the failure is recoverable: fix storage, retry the same job
    monkeypatch.undo()
    assert client.post(f"/api/jobs/{jid}/retry").status_code == 200
    for _ in range(100):
        j = client.get(f"/api/jobs/{jid}").json()
        if j["status"] in ("done", "failed"):
            break
        time.sleep(0.1)
    assert j["status"] == "done", j["error"]
    assert len(_dataset_rows(rid)) == 1 and len(_dataset_objects(cid)) == 1


def test_db_failure_after_object_write_cleans_the_object(
    client: TestClient, isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy.exc import OperationalError

    from forma.services import datasets as ds

    rid, cid = _approved_round(client, isolated_env, code="F2")
    real_write = ds.write_version_object
    written: list[str] = []

    def write_then_db_dies(ctx: Any, key: str, records: Any) -> int:
        n = real_write(ctx, key, records)
        written.append(key)
        return n

    monkeypatch.setattr(ds, "write_version_object", write_then_db_dies)

    from forma.db.models import DatasetVersion

    orig_init = DatasetVersion.__init__

    def failing_init(self: Any, *a: Any, **kw: Any) -> None:
        orig_init(self, *a, **kw)
        raise OperationalError("INSERT", {}, Exception("server closed the connection unexpectedly"))

    monkeypatch.setattr(DatasetVersion, "__init__", failing_init)
    jid = client.post(f"/api/rounds/{rid}/transform?sync=true").json()["job_id"]
    j = client.get(f"/api/jobs/{jid}").json()
    assert j["status"] == "failed" and "server closed the connection" in j["error"]
    assert written and _dataset_objects(cid) == [] and _dataset_rows(rid) == []
    assert client.get(f"/api/rounds/{rid}").json()["status"] == "failed"


def _pause_first_transform(marker: str) -> None:
    """Spawn-safe initializer: stop at handler entry, only on the first delivery."""
    from forma.jobs import handlers as _handlers  # noqa: F401
    from forma.jobs import runner

    original = runner._HANDLERS["transform_round"]

    def paused(ctx: Any, payload: Any, progress: Any) -> Any:
        path = Path(marker)
        if not path.exists():
            path.write_text("entered", encoding="utf-8")
            time.sleep(30)
            raise AssertionError("Test did not terminate the paused transform worker")
        return original(ctx, payload, progress)

    runner._HANDLERS["transform_round"] = paused


@pytest.mark.slow
def test_killed_worker_process_is_recovered(isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A real worker process is killed mid-transform; nothing partial survives; recovery re-runs it."""
    psutil = pytest.importorskip("psutil")
    monkeypatch.setenv("FORMA_JOB_BACKEND", "process")
    monkeypatch.setenv("FORMA_WORKERS", "1")
    from tests.conftest import clear_forma_caches

    clear_forma_caches()
    import multiprocessing as mp
    from concurrent.futures import ProcessPoolExecutor

    from forma.api.app import create_app
    from forma.jobs import runner

    marker = isolated_env / "transform-entered.txt"
    monkeypatch.setattr(
        runner.ProcessBackend,
        "_make_pool",
        lambda self: ProcessPoolExecutor(
            max_workers=1,
            mp_context=mp.get_context("spawn"),
            initializer=_pause_first_transform,
            initargs=(str(marker),),
        ),
    )

    with TestClient(create_app()) as c:
        # Workbook size no longer determines whether the worker can be killed in time.
        cust = c.post("/api/customers", json={"code": "F3", "name": "F3"}).json()
        cid = cust["id"]
        rid = c.post("/api/rounds", json={"customer_id": cid}).json()["id"]
        _upload(c, rid, make_workbook(isolated_env / "recovery.xlsx"), name="recovery.xlsx")
        job = _run(c, rid)
        c.post(f"/api/mappings/{job['result']['mapping_version_id']}/approve", json={})
        assert _dataset_rows(rid) == []
        jid = c.post(f"/api/rounds/{rid}/transform").json()["job_id"]
        # wait until a child is actually running the job, then kill every worker child
        me = psutil.Process()
        deadline = time.time() + 60
        killed = False
        while time.time() < deadline:
            st = runner.job_status(jid)
            if st["status"] == "running" and marker.exists():
                children = me.children(recursive=True)
                if children:
                    for ch in children:
                        ch.kill()
                    killed = True
                    break
            if st["status"] in ("done", "failed"):
                break
            time.sleep(0.05)
        assert killed, "could not observe a running worker to kill"
        time.sleep(0.5)
        assert runner.job_status(jid)["status"] == "running"  # the dead worker never reported back
        assert _dataset_rows(rid) == []
        # operator/worker restart: stale recovery re-queues; the broken pool is rebuilt
        assert runner.recover_stale(stale_s=0) == []  # never steal a still-valid lease
        import datetime as dt

        from forma.db.models import Job
        from forma.db.session import session_scope

        with session_scope() as session:
            dead = session.get(Job, jid)
            assert dead is not None
            dead.lease_expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)
        requeued = runner.recover_stale(stale_s=0)
        assert jid in requeued
        for _ in range(1200):
            st = runner.job_status(jid)
            if st["status"] in ("done", "failed"):
                break
            time.sleep(0.1)
        assert st["status"] == "done", st["error"]
        assert st["attempts"] == 2 and len(_dataset_rows(rid)) == 1
        assert len(_dataset_objects(cid)) == 1
        assert st["result"]["rows"] == 12


# --------------------------------------------------------------------------------------------
class _TimeoutProvider:
    name = "openai"

    def __init__(self) -> None:
        self.calls = 0

    def complete_json(self, **kw: Any) -> Any:
        self.calls += 1
        raise httpx.ReadTimeout("read timed out")


def test_llm_timeout_falls_back_and_never_auto_approves(
    client: TestClient, isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from forma.ai import breaker
    from forma.ai.llm import LLMClient
    from forma.core.settings import get_settings
    from forma.services.context import get_context

    monkeypatch.setenv("FORMA_LLM_BACKOFF_S", "0")
    get_settings.cache_clear()
    breaker.reset_breaker()
    prov = _TimeoutProvider()
    get_context().llm = LLMClient(prov, model="gpt-4.1-mini", max_retries=0)  # type: ignore[arg-type]
    # returning customer with an approved mapping, so a *healthy* run would auto-approve (identical structure)
    cust = client.post("/api/customers", json={"code": "L1", "name": "l"}).json()
    r1 = client.post("/api/rounds", json={"customer_id": cust["id"]}).json()["id"]
    _upload(client, r1, make_workbook(isolated_env / "a.xlsx"))
    job = client.post(f"/api/rounds/{r1}/run", json={"sync": True, "use_llm": False}).json()
    mv = client.get(f"/api/jobs/{job['job_id']}").json()["result"]["mapping_version_id"]
    client.post(f"/api/mappings/{mv}/approve", json={})
    # drifted second round (renamed column) with the LLM on: adaptation agent → timeout → heuristic draft
    r2 = client.post("/api/rounds", json={"customer_id": cust["id"]}).json()["id"]
    _upload(client, r2, make_workbook(isolated_env / "b.xlsx", rename_qty=True))
    job = client.post(f"/api/rounds/{r2}/run", json={"sync": True, "use_llm": True}).json()
    j = client.get(f"/api/jobs/{job['job_id']}").json()
    assert j["status"] == "done", j["error"]
    assert prov.calls >= 3  # transport retries happened
    rnd = client.get(f"/api/rounds/{r2}").json()
    assert rnd["status"] == "review"
    m = client.get(f"/api/mappings/{j['result']['mapping_version_id']}").json()
    assert m["policy"]["decision"] == "review" and m["policy"]["checks"]["llm_fallback"] is True
    assert any("AI mapping unavailable" in r for r in m["policy"]["reasons"])
    assert any("llm" in str(e).lower() for e in m["agent_log"])
    # breaker: after the configured failures the circuit is open and calls fail fast
    st = breaker.state()
    assert st["failures"] >= 1
    get_settings.cache_clear()


def test_breaker_opens_and_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    from forma.ai.breaker import CircuitBreaker, CircuitOpen, guarded_call

    clock = [0.0]
    b = CircuitBreaker(failures=2, reset_s=10, clock=lambda: clock[0])
    calls = {"n": 0}

    def flaky() -> str:
        calls["n"] += 1
        raise httpx.ConnectError("down")

    sleeps: list[float] = []
    for _ in range(2):
        with pytest.raises(httpx.ConnectError):
            guarded_call(flaky, attempts=2, base_s=1, sleep=sleeps.append, breaker=b)
    assert b.is_open and calls["n"] == 4 and len(sleeps) == 2
    with pytest.raises(CircuitOpen):
        guarded_call(flaky, attempts=1, sleep=sleeps.append, breaker=b)
    assert calls["n"] == 4  # fail-fast: no provider call while open
    clock[0] = 11  # window elapsed → one probe
    assert not b.is_open and b.state()["half_open"] is True
    assert guarded_call(lambda: "ok", attempts=1, sleep=sleeps.append, breaker=b) == "ok"
    assert not b.is_open and b.state()["failures"] == 0 and b.state()["times_opened"] == 1
    # non-transient errors are not retried but do count
    with pytest.raises(ValueError):
        guarded_call(
            lambda: (_ for _ in ()).throw(ValueError("bad request")),
            attempts=3,
            sleep=sleeps.append,
            breaker=b,
        )
    assert b.state()["failures"] == 1


def test_retry_helper() -> None:
    from forma.core.retry import retry

    n = {"i": 0}
    sleeps: list[float] = []

    def fn() -> int:
        n["i"] += 1
        if n["i"] < 3:
            raise TimeoutError("x")
        return n["i"]

    assert retry(fn, attempts=5, base_s=1, sleep=sleeps.append, retry_on=(TimeoutError,)) == 3
    assert len(sleeps) == 2 and 0.7 <= sleeps[0] <= 1.3 and 1.4 <= sleeps[1] <= 2.6
    with pytest.raises(TimeoutError):
        retry(lambda: (_ for _ in ()).throw(TimeoutError("y")), attempts=2, base_s=0.01, sleep=sleeps.append)
