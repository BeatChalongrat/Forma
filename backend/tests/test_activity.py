"""Activity is observable evidence, isolated by job/attempt and customer. No network model calls."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from forma.jobs import activity


@pytest.fixture
def client(isolated_env, monkeypatch):
    from forma.ai.llm import AnthropicProvider, OpenAICompatibleProvider
    from forma.api.app import create_app

    def forbidden(*args, **kwargs):
        raise AssertionError("Paid model transport is forbidden in activity tests")

    monkeypatch.setattr(OpenAICompatibleProvider, "complete_json", forbidden)
    monkeypatch.setattr(AnthropicProvider, "complete_json", forbidden)
    with TestClient(create_app()) as c:
        yield c


def record(job, attempt, n=1):
    token = activity.context.set((job, attempt))
    try:
        for i in range(n):
            with activity.span("inspect", agent="analyst", inputs={"row": i}) as result:
                result["output"] = {"job": job, "answer": i}
    finally:
        activity.context.reset(token)


def test_journal_survives_rollback_and_process_reopen(client):
    from forma.db.models import Job
    from forma.db.session import session_scope

    with pytest.raises(RuntimeError), session_scope() as s:
        s.add(Job(id="rollback", kind="test"))
        s.flush()  # hold the SQLite write lock, just as a real mapping transaction does
        record("rollback", 1)
        raise RuntimeError("business transaction rolled back")
    rows = activity.read("rollback", 1)
    assert [e["state"] for e in rows] == ["started", "completed"]
    assert rows[1]["parent_id"] is None  # terminal receipt is not its own parent
    activity.engine().dispose()
    assert activity.read("rollback", 1) == rows


def test_parallel_jobs_and_retries_never_mix(client):
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: record(f"parallel-{i}", 1, 3), range(24)))
    for i in range(24):
        rows = activity.read(f"parallel-{i}", 1)
        assert len(rows) == 6
        assert all(e["job_id"] == f"parallel-{i}" and e["attempt"] == 1 for e in rows)
    record("parallel-0", 2)
    assert len(activity.read("parallel-0", 1)) == 6
    assert len(activity.read("parallel-0", 2)) == 2
    assert activity.context.get() is None and activity.role.get() == "system"


def test_failure_is_a_receipt_not_a_success(client):
    token = activity.context.set(("failure", 1))
    try:
        with pytest.raises(ValueError), activity.span("sandbox.python", agent="mapper"):
            raise ValueError("invalid join")
    finally:
        activity.context.reset(token)
    rows = activity.read("failure", 1)
    assert rows[-1]["state"] == "failed"
    assert rows[-1]["detail"]["error"] == "invalid join"


def test_actual_graph_roles_and_model_receipts(client, isolated_env, schema):
    from tests.test_mapping_workflow import run

    token = activity.context.set(("graph", 1))
    try:
        model, _, result = run(isolated_env / "graph", schema)
    finally:
        activity.context.reset(token)
    rows = activity.read("graph", 1, limit=500)
    assert result.data["workflow"]["status"] == "verified_proposal"
    roles = [e["role"] for e in rows if e["action"].startswith("agent.") and e["state"] == "started"]
    assert roles == ["analyst", "mapper", "execute", "reviewer"]
    calls = [e for e in rows if e["action"] == "model.request" and e["state"] == "completed"]
    assert len(calls) == len(model.provider.calls) == 3
    assert all("usage" in c["detail"] for c in calls)
    assert not any(
        "system" in (e["detail"] or {}).get("input", {})
        for e in rows
        if isinstance((e["detail"] or {}).get("input"), dict)
    )


def test_api_scope_pagination_and_historical_attempt(client):
    from forma.db.models import Customer, ImportRound, Job
    from forma.db.session import session_scope

    with session_scope() as s:
        s.add(Customer(id="owner", code="OWNER", name="Owner"))
        s.flush()
        s.add(ImportRound(id="round", customer_id="owner"))
        s.flush()
        s.add(Job(id="scoped", round_id="round", kind="test", status="done", attempts=2))
    record("scoped", 1, 60)
    record("scoped", 2)
    denied = {"x-forma-customers": "someone-else"}
    assert client.get("/api/activity/jobs/scoped", headers=denied).status_code == 404
    assert client.get("/api/activity/jobs", headers=denied).json()["total"] == 0
    assert client.get("/api/activity/jobs?round_id=round").json()["total"] == 1
    page = client.get("/api/activity/jobs/scoped?attempt=1").json()
    assert len(page["events"]) == 100 and page["mapping"] is None
    previous = client.get(f"/api/activity/jobs/scoped?attempt=1&before={page['previous_cursor']}").json()
    assert len(previous["events"]) == 20 and previous["previous_cursor"] is None
    assert {e["id"] for e in previous["events"]}.isdisjoint(e["id"] for e in page["events"])
    assert client.get("/api/activity/jobs/scoped").json()["attempt"] == 2
    assert client.get("/api/activity/jobs?limit=0").status_code == 422


def test_real_job_pipeline_records_committed_result_without_paid_model(client, isolated_env):
    from tests.test_jobs import _round_with_file

    from forma.jobs import runner

    rid = _round_with_file(client, isolated_env, "ACTIVITY")
    jid = runner.enqueue("run_pipeline", {"round_id": rid, "use_llm": False}, round_id=rid, sync=True)
    job = runner.job_status(jid)
    assert job["status"] == "done", job["error"]
    data = client.get(f"/api/activity/jobs/{jid}").json()
    assert data["events"][-1]["detail"]["committed"] is True
    assert data["mapping"]["id"] == job["result"]["mapping_version_id"]
    assert any(e["action"] == "stage.profile" and e["state"] == "completed" for e in data["events"])
    assert not any(e["action"] == "model.request" for e in data["events"])
    from tests.test_api import FILE_NAME

    found = client.get("/api/activity/jobs", params={"search": FILE_NAME}).json()
    assert found["total"] == 1 and found["items"][0]["files"]


def test_credentials_redacted_and_large_evidence_explicitly_truncated(client):
    token = activity.context.set(("safe", 1))
    try:
        activity.emit(
            "test",
            detail={
                "api_key": "sensitive",
                "nested": {"authorization": "Bearer token"},
                "code": "x" * 25000,
                "rows": list(range(201)),
            },
        )
    finally:
        activity.context.reset(token)
    data = activity.read("safe", 1)[0]["detail"]
    assert data["api_key"] == data["nested"]["authorization"] == "[redacted]"
    assert data["code"].endswith("[truncated]")
    assert data["rows"][-1] == {"omitted_items": 1}


def test_backup_restores_activity_in_the_new_instance(client, isolated_env, monkeypatch):
    from tests.conftest import clear_forma_caches

    from forma.services.backup import dump_database, restore_database

    record("backup-job", 1)
    destination = isolated_env / "backup"
    destination.mkdir()
    dump = dump_database(destination)
    monkeypatch.setenv("FORMA_DATABASE_URL", f"sqlite:///{isolated_env}/restored.db")
    clear_forma_caches()
    assert activity.read("backup-job", 1) == []
    restore_database(dump)
    assert len(activity.read("backup-job", 1)) == 2


def test_retention_does_not_remove_receipts_on_rollback(client):
    from forma.db.models import Job
    from forma.db.session import session_scope

    with session_scope() as s:
        s.add(Job(id="retained", kind="test"))
    record("retained", 1)
    with pytest.raises(ValueError), session_scope() as s:
        s.delete(s.get(Job, "retained"))
        s.info["activity_retired_jobs"] = ["retained"]
        raise ValueError("rollback")
    assert len(activity.read("retained", 1)) == 2
    with session_scope() as s:
        s.delete(s.get(Job, "retained"))
        s.info["activity_retired_jobs"] = ["retained"]
    assert activity.read("retained", 1) == []
