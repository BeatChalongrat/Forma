"""Regression cases for the release audit: transactions, stale writers and persisted selections."""

from __future__ import annotations

import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.orm import Session
from tests.test_api import _run, _upload, make_workbook


@pytest.fixture
def client(isolated_env: Path) -> Iterator[TestClient]:
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def dataset(client: TestClient, isolated_env: Path) -> tuple[str, str, str]:
    customer = client.post("/api/customers", json={"code": "INTEGRITY", "name": "Integrity"}).json()
    rid = client.post("/api/rounds", json={"customer_id": customer["id"]}).json()["id"]
    _upload(client, rid, make_workbook(isolated_env / "integrity.xlsx"))
    mid = _run(client, rid)["result"]["mapping_version_id"]
    assert client.post(f"/api/mappings/{mid}/approve", json={}).is_success
    jid = client.post(f"/api/rounds/{rid}/transform?sync=true").json()["job_id"]
    job = client.get(f"/api/jobs/{jid}").json()
    assert job["status"] == "done", job
    return rid, job["result"]["dataset_version_id"], customer["id"]


def edit(identity: str, value: str) -> dict[str, Any]:
    return {
        "operations": [{"kind": "set_cell", "identity": identity, "target": "/product_name", "value": value}]
    }


def test_dataset_mutation_cannot_cross_customer_or_role_boundary(
    client: TestClient, dataset: tuple[str, str, str]
) -> None:
    _, did, _ = dataset
    before = client.get(f"/api/datasets/{did}").json()
    rows_before = client.get(f"/api/datasets/{did}/rows").json()
    for headers in (
        {"X-Forma-User": "importer"},
        {"X-Forma-User": "reviewer", "X-Forma-Customers": "different-customer"},
    ):
        response = client.post(
            f"/api/datasets/{did}/changes", json=edit("A001", "Unauthorized"), headers=headers
        )
        assert response.status_code == 403, response.text
    after = client.get(f"/api/datasets/{did}").json()
    assert after["history"] == before["history"]
    assert client.get(f"/api/datasets/{did}/rows").json() == rows_before


def test_invalid_second_edit_does_not_partially_apply_first(
    client: TestClient, dataset: tuple[str, str, str]
) -> None:
    _, did, _ = dataset
    before = client.get(f"/api/datasets/{did}").json()
    rows_before = client.get(f"/api/datasets/{did}/rows").json()
    operations = edit("A001", "Must not persist")["operations"]
    operations.append({"kind": "set_cell", "identity": "A002", "target": "/sku", "value": None})
    response = client.post(f"/api/datasets/{did}/changes", json={"operations": operations})
    assert response.status_code in (409, 422), response.text
    after = client.get(f"/api/datasets/{did}").json()
    assert after["history"] == before["history"]
    assert client.get(f"/api/datasets/{did}/rows").json() == rows_before


def test_commit_failure_never_reports_success(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from forma.db.models import Customer
    from forma.db.session import session_scope

    original = Session.commit
    injected = []

    def mark(session: Session, *_args: Any) -> None:
        if any(isinstance(row, Customer) and row.code == "COMMIT-FAULT" for row in session.new):
            session.info["commit_fault"] = True

    def fail(session: Session) -> None:
        if session.info.pop("commit_fault", False):
            injected.append(True)
            raise RuntimeError("private failure detail must not reach the client")
        original(session)

    event.listen(Session, "after_flush", mark)
    monkeypatch.setattr(Session, "commit", fail)
    try:
        response = client.post("/api/customers", json={"code": "COMMIT-FAULT", "name": "Test"})
    finally:
        event.remove(Session, "after_flush", mark)
    assert injected and response.status_code == 503
    assert "private failure" not in response.text
    assert response.headers.get("X-Request-Id")
    with session_scope() as session:
        assert session.scalar(select(Customer).where(Customer.code == "COMMIT-FAULT")) is None
    assert client.post("/api/customers", json={"code": "COMMIT-FAULT", "name": "Retry"}).status_code == 201


@pytest.mark.parametrize("parallel", [False, True])
def test_stale_editor_conflicts_without_duplicate_versions(
    client: TestClient, dataset: tuple[str, str, str], parallel: bool
) -> None:
    _, did, _ = dataset
    gate = Barrier(2)

    def save(n: int) -> Any:
        if parallel:
            gate.wait(timeout=10)
        return client.post(f"/api/datasets/{did}/changes", json=edit(f"A00{n}", f"Edit {n}"))

    if parallel:
        with ThreadPoolExecutor(2) as pool:
            responses = list(pool.map(save, [1, 2]))
    else:
        responses = [save(1), save(2)]
    assert sorted(r.status_code for r in responses) == [201, 409], [r.text for r in responses]
    latest = next(r.json()["dataset"]["id"] for r in responses if r.status_code == 201)
    conflict = next(r.json() for r in responses if r.status_code == 409)
    assert conflict["details"]["latest_dataset_id"] == latest
    history = client.get(f"/api/datasets/{latest}").json()["history"]
    assert [row["version"] for row in history] == [1, 2]
    saved = client.post(f"/api/datasets/{latest}/changes", json=edit("A003", "Rebased"))
    assert saved.status_code == 201 and saved.json()["dataset"]["version"] == 3


def test_persistent_rule_replays_the_same_selection(
    client: TestClient, dataset: tuple[str, str, str]
) -> None:
    from forma.db.session import session_scope
    from forma.services import datasets, mapping
    from forma.services.context import get_context

    _, did, cid = dataset
    op = {
        "kind": "set_field",
        "target": "/product_name",
        "value": "Remembered",
        "identities": ["A001", "A005"],
        "where": {"op": "eq", "args": [{"op": "col", "name": "/status"}, {"op": "const", "value": "active"}]},
    }
    preview = client.post(f"/api/datasets/{did}/preview-changes", json={"operations": [op]}).json()
    saved = client.post(f"/api/datasets/{did}/changes", json={"operations": [op], "kind": "persistent"})
    assert saved.status_code == 201, saved.text
    ctx = get_context()
    with session_scope() as session:
        dv = datasets.get_dataset(session, did)
        schema = ctx.registry.get_ref(mapping.get_mapping(session, dv.mapping_version_id).target_schema)
        original = datasets.records_of(ctx, dv)
        rows, impact = datasets.apply_persistent_overrides(session, cid, schema, original)
    assert preview["impact"]["rows_affected"] == impact["rows_affected"] == 1
    assert next(r for r in rows if r["sku"] == "A005")["product_name"] != "Remembered"


def test_cancel_racing_edit_cannot_reopen_round(client: TestClient, dataset: tuple[str, str, str]) -> None:
    rid, did, _ = dataset
    gate = Barrier(2)

    def request(action: str) -> Any:
        gate.wait(timeout=10)
        if action == "cancel":
            return client.post(f"/api/rounds/{rid}/cancel", json={"reason": "Wrong file"})
        return client.post(f"/api/datasets/{did}/changes", json=edit("A001", "Race edit"))

    with ThreadPoolExecutor(2) as pool:
        cancelled, changed = list(pool.map(request, ["cancel", "edit"]))
    assert cancelled.is_success, cancelled.text
    assert changed.status_code in (201, 409), changed.text
    assert client.get(f"/api/rounds/{rid}").json()["status"] == "cancelled"
    latest = changed.json()["dataset"]["id"] if changed.status_code == 201 else did
    assert client.post(f"/api/datasets/{latest}/approve", json={}).status_code == 409


def test_dataset_commit_failure_removes_only_its_new_object(
    client: TestClient,
    dataset: tuple[str, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from forma.db.models import DatasetVersion
    from forma.services.context import get_context

    _, did, cid = dataset
    storage = get_context().storage
    before = {obj.key for obj in storage.list(f"datasets/{cid}/")}
    original = Session.commit

    def mark(session: Session, *_: Any) -> None:
        if any(isinstance(row, DatasetVersion) for row in session.new):
            session.info["fail_dataset_commit"] = True

    def fail(session: Session) -> None:
        if session.info.pop("fail_dataset_commit", False):
            raise RuntimeError("Commit fault after writing object")
        original(session)

    event.listen(Session, "after_flush", mark)
    monkeypatch.setattr(Session, "commit", fail)
    try:
        assert client.post(f"/api/datasets/{did}/changes", json=edit("A001", "No commit")).status_code == 503
    finally:
        event.remove(Session, "after_flush", mark)
    assert {obj.key for obj in storage.list(f"datasets/{cid}/")} == before
    assert len(client.get(f"/api/datasets/{did}").json()["history"]) == 1


def test_lost_commit_acknowledgement_preserves_committed_object(
    client: TestClient, dataset: tuple[str, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from forma.db.models import DatasetVersion
    from forma.db.session import get_engine, session_scope
    from forma.services.context import get_context

    _, did, _ = dataset
    dialect = get_engine().dialect
    original = dialect.do_commit
    armed: list[bool] = []

    def mark(session: Session, *_: Any) -> None:
        if any(isinstance(row, DatasetVersion) for row in session.new):
            armed.append(True)

    def disconnected(connection: Any) -> None:
        original(connection)  # The database has committed, but the client does not get its acknowledgement.
        if armed:
            armed.pop()
            raise RuntimeError("Connection lost after COMMIT")

    event.listen(Session, "after_flush", mark)
    monkeypatch.setattr(dialect, "do_commit", disconnected)
    try:
        response = client.post(f"/api/datasets/{did}/changes", json=edit("A001", "Durably committed"))
        assert response.status_code == 503, response.text
    finally:
        event.remove(Session, "after_flush", mark)
    with session_scope() as session:
        original_row = session.get(DatasetVersion, did)
        assert original_row
        latest = session.scalar(
            select(DatasetVersion).where(
                DatasetVersion.round_id == original_row.round_id, DatasetVersion.version == 2
            )
        )
        assert latest and get_context().storage.exists(latest.records_key)


def test_cancelled_round_cannot_be_edited_or_published(
    client: TestClient, dataset: tuple[str, str, str]
) -> None:
    rid, did, _ = dataset
    assert client.post(f"/api/datasets/{did}/approve", json={"acknowledge_anomalies": True}).is_success
    assert client.post(f"/api/rounds/{rid}/cancel", json={"reason": "Wrong file"}).is_success
    for action, body in [("approve", {}), ("changes", edit("A001", "Wrong")), ("rollback", {})]:
        response = client.post(f"/api/datasets/{did}/{action}", json=body)
        assert response.status_code == 409, response.text
    assert client.post(f"/api/datasets/{did}/export?fmt=jsonl").status_code == 409
    assert client.get(f"/api/rounds/{rid}").json()["status"] == "cancelled"


@pytest.mark.parametrize("damage", ["object", "database", "missing", "path"])
def test_restore_validates_everything_before_database_write(
    client: TestClient,
    dataset: tuple[str, str, str],
    isolated_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    damage: str,
) -> None:
    from tests.conftest import clear_forma_caches

    from forma.services import backup
    from forma.services.context import get_context

    info = backup.create_backup(get_context(), isolated_env / "backup")
    source = Path(info["path"])
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    obj = source / "objects" / manifest["objects"][0]["key"]
    if damage == "object":
        obj.write_bytes(obj.read_bytes() + b"corruption")
    elif damage == "database":
        dump = source / manifest["database"]
        dump.write_bytes(dump.read_bytes() + b"corruption")
    elif damage == "missing":
        obj.unlink()
    else:
        manifest["objects"][0]["key"] = "../../outside"
        (source / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    clear_forma_caches()
    monkeypatch.setenv("FORMA_DATABASE_URL", f"sqlite:///{isolated_env}/restore.db")
    monkeypatch.setenv("FORMA_DATA_DIR", str(isolated_env / "restore-data"))
    writes = []
    monkeypatch.setattr(backup, "restore_database", lambda path: writes.append(path))
    with pytest.raises(ValueError):
        backup.restore_backup(get_context(), source)
    assert writes == []


def test_retention_rechecks_knowledge_approved_after_preview(
    client: TestClient,
    dataset: tuple[str, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import datetime as dt

    from forma.db.models import ImportRound, KnowledgeLabel
    from forma.db.session import session_scope
    from forma.services import retention
    from forma.services.context import get_context

    rid, did, _ = dataset
    ctx = get_context()
    mid = client.get(f"/api/datasets/{did}").json()["mapping_version_id"]
    monkeypatch.setattr(retention, "load_policy", lambda _: {"source_files": {"keep_days_after_publish": 1}})
    with session_scope() as session:
        rnd = session.get(ImportRound, rid)
        assert rnd
        rnd.status = "published"
        rnd.updated_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=60)
    with session_scope() as session:
        stale = retention.plan(ctx, session)
        assert len(stale["source_files"]) == 1
        session.add(
            KnowledgeLabel(
                subject="mapping:" + mid,
                origin="real_trial",
                state="allowed",
                actor="reviewer",
                reason="Keep verified evidence",
            )
        )
    with session_scope() as session:
        fresh = retention.plan(ctx, session)
        assert fresh["source_files"] == [] and rid in fresh["protected_knowledge_rounds"]
        assert retention.apply(ctx, session, stale)["source_files"] == 0
    assert ctx.storage.exists(stale["source_files"][0]["key"])


def test_large_dataset_query_is_bounded_and_sorts_numbers(
    client: TestClient,
    dataset: tuple[str, str, str],
    isolated_env: Path,
) -> None:
    import tracemalloc

    from forma.db.models import DatasetVersion
    from forma.db.session import session_scope
    from forma.services.context import get_context

    rid, base, cid = dataset
    source = isolated_env / "large.jsonl"
    with source.open("w", encoding="utf-8") as stream:
        for n in range(100_000):
            stream.write(
                json.dumps(
                    {"sku": str(n), "quantity": n, "description": "รายละเอียด " + "x" * 120},
                    ensure_ascii=False,
                )
                + "\n"
            )
    ctx = get_context()
    ctx.storage.put("datasets/query-test.jsonl", source)
    with session_scope() as session:
        original = session.get(DatasetVersion, base)
        assert original
        row = DatasetVersion(
            round_id=rid,
            customer_id=cid,
            mapping_version_id=original.mapping_version_id,
            version=2,
            records_key="datasets/query-test.jsonl",
            row_count=100_000,
        )
        session.add(row)
        session.flush()
        did = row.id
    tracemalloc.start()
    try:
        result = client.get(
            f"/api/datasets/{did}/rows", params={"limit": 3, "sort": "/quantity", "desc": True}
        )
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert result.is_success, result.text
    assert result.json()["total"] == 100_000
    assert [r["quantity"] for r in result.json()["rows"]] == [99_999, 99_998, 99_997]
    assert peak < 25 * 1024 * 1024, peak
    assert (
        client.get(f"/api/datasets/{did}/rows", params={"q": "รายละเอียด", "offset": 99_999}).json()["rows"][
            0
        ]["sku"]
        == "99999"
    )
    assert client.get(f"/api/datasets/{did}/rows?offset=-1").status_code == 422


def test_attention_count_is_not_limited_to_the_latest_hundred(client: TestClient) -> None:
    from forma.db.models import ImportRound
    from forma.db.session import session_scope

    cid = client.post("/api/customers", json={"code": "COUNT", "name": "Count"}).json()["id"]
    with session_scope() as session:
        session.add_all(
            [ImportRound(customer_id=cid, status="review", target_schema="product@1") for _ in range(125)]
        )
    attention = client.get("/api/dashboard").json()["attention"]
    assert attention["total"] == 125 and len(attention["items"]) == 6
    assert attention["unit"] == "import_rounds"


def test_alerts_report_failures_and_recover_when_state_changes(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import datetime as dt

    from forma.db.models import Job
    from forma.db.session import session_scope
    from forma.jobs import runner

    monkeypatch.setattr(runner, "queue_stats", lambda: {"oldest_queued_age_s": 1800})
    with session_scope() as session:
        row = Job(kind="test", payload={}, status="failed", finished_at=dt.datetime.now(dt.UTC))
        session.add(row)
        session.flush()
        jid = row.id
    codes = {a["code"] for a in client.get("/api/ops").json()["alerts"]}
    assert {"queue_age", "recent_failures"} <= codes
    with session_scope() as session:
        row = session.get(Job, jid)
        assert row
        row.status = "done"
    assert "recent_failures" not in {a["code"] for a in client.get("/api/ops").json()["alerts"]}
