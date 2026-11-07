"""Read-only investigation across receipts, with no paid model transport."""

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from forma.db.models import (
    AuditLog,
    Batch,
    BatchItem,
    ChangeSet,
    Correction,
    Customer,
    DatasetVersion,
    ExportArtifact,
    ImportRound,
    Job,
    MappingVersion,
    SourceFile,
)
from forma.db.session import session_scope
from forma.jobs import activity


@pytest.fixture
def client(isolated_env, monkeypatch):
    from forma.ai.llm import AnthropicProvider, OpenAICompatibleProvider
    from forma.api.app import create_app

    def forbidden(*args, **kwargs):
        raise AssertionError("Paid API forbidden")

    monkeypatch.setattr(OpenAICompatibleProvider, "complete_json", forbidden)
    monkeypatch.setattr(AnthropicProvider, "complete_json", forbidden)
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def work(client):
    with session_scope() as s:
        s.add_all([Customer(id="a", code="A", name="Alpha"), Customer(id="b", code="B", name="Beta")])
        s.flush()
        s.add_all(
            [
                ImportRound(id="ra", customer_id="a", label="November", status="review"),
                ImportRound(id="rb", customer_id="b"),
            ]
        )
        s.add(Batch(id="batch", label="Monthly", created_by="tester"))
        s.flush()
        s.add(
            SourceFile(
                id="file",
                round_id="ra",
                customer_id="a",
                original_name="sales_100%.xlsx",
                size=100,
                kind="excel",
                sha256="0" * 64,
                storage_key="private/key",
            )
        )
        s.add(
            BatchItem(
                id="item",
                batch_id="batch",
                customer_id="a",
                round_id="ra",
                file_name="sales.xlsx",
                size=100,
                kind="excel",
                sha256="0" * 64,
                storage_key="private/key",
            )
        )
        s.add(
            BatchItem(
                id="unknown",
                batch_id="batch",
                file_name="unknown.xlsx",
                size=100,
                kind="excel",
                sha256="0" * 64,
                storage_key="private/key",
            )
        )
        s.add(Job(id="job", kind="run_pipeline", round_id="ra", status="done", attempts=2))
        s.add(Job(id="unassigned", kind="identify", payload={"item_id": "unknown"}))
        s.add(
            MappingVersion(
                id="map",
                round_id="ra",
                customer_id="a",
                target_schema="product",
                document={},
                agent_log=[{"tool": "inspect", "result": i, "api_key": "secret-value"} for i in range(26)],
                policy={
                    "confirmed_decisions": [
                        {"target": "/vat", "answer": False, "question": "VAT?", "actor": "tester"}
                    ],
                    "verification": {"passed": False},
                },
            )
        )
        s.add(
            DatasetVersion(
                id="data",
                round_id="ra",
                customer_id="a",
                mapping_version_id="map",
                records_key="private/key",
                row_count=12,
            )
        )
        s.add(
            ChangeSet(
                id="change",
                dataset_version_id="data",
                customer_id="a",
                kind="one_time",
                operations=[{"before": 10, "after": 12}],
            )
        )
        s.add(
            ExportArtifact(
                id="export",
                dataset_version_id="data",
                customer_id="a",
                fmt="json",
                storage_key="private/key",
                row_count=12,
                sha256="0" * 64,
            )
        )
        s.add(
            Correction(
                id="knowledge",
                customer_id="a",
                round_id="ra",
                mapping_version_id="map",
                kind="decision",
                actor="tester",
                before=False,
                after=True,
            )
        )
        s.add(
            AuditLog(
                id="answer",
                actor="tester",
                action="review.answers",
                entity="mapping",
                entity_id="map",
                details={"round_id": "ra"},
            )
        )
        s.add(
            AuditLog(
                id="private-chat",
                actor="other",
                action="assistant.create",
                entity="conversation",
                entity_id="other-chat",
                details={"round_id": "ra", "private": "must-not-see"},
            )
        )
    token = activity.context.set(("job", 1))
    try:
        with activity.span("sandbox.python", agent="mapper") as receipt:
            receipt["output"] = {"rows": 12, "password": "secret-value", "reasoning_content": "hidden"}
    finally:
        activity.context.reset(token)
    return "ra"


def test_all_sources_and_read_only(client, work):
    with session_scope() as s:
        before = s.scalar(select(func.count()).select_from(AuditLog))
    res = client.get(f"/api/inspection/round/{work}/events")
    assert res.status_code == 200, res.text
    events = res.json()["events"]
    assert {
        "file",
        "item",
        "mapping",
        "dataset",
        "change",
        "export",
        "knowledge",
        "audit",
        "job",
        "activity",
        "round",
    } <= {e["kind"] for e in events}
    assert "audit:private-chat" not in {e["key"] for e in events}
    for e in events:
        detail = client.get(f"/api/inspection/round/{work}/events/{e['kind']}/{e['id']}")
        assert detail.status_code == 200, detail.text
        assert (
            "secret-value" not in detail.text
            and "private/key" not in detail.text
            and '"hidden"' not in detail.text
        )
    data = client.get("/api/inspection/round/ra/events/mapping/map").json()["data"]
    assert data["decisions"][0]["answer"] is False
    assert data["checks"]["verification"]["passed"] is False
    assert data["logs"]["next_offset"] == 20
    assert (
        len(
            client.get("/api/inspection/round/ra/events/mapping/map?offset=20").json()["data"]["logs"][
                "items"
            ]
        )
        == 6
    )
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(AuditLog)) == before


def test_permissions_apply_to_every_drilldown(client, work):
    denied = {"x-forma-customers": "b"}
    assert client.get("/api/inspection/round/ra/events", headers=denied).status_code == 404
    assert client.get("/api/inspection/round/ra/events/mapping/map", headers=denied).status_code == 404
    assert client.get("/api/inspection/round/rb/events/mapping/map").status_code == 404
    eid = activity.read("job", 1)[0]["id"]
    assert client.get(f"/api/inspection/round/rb/events/activity/{eid}").status_code == 404
    assert client.get("/api/inspection/item/unknown/events", headers=denied).status_code == 404
    listed = client.get("/api/inspection/items", headers=denied).json()["items"]
    assert [i["id"] for i in listed] == ["rb"]
    assert client.get("/api/inspection/item/item/events").status_code == 200


def test_filters_and_unassigned_job(client, work):
    for params in (
        {"search": "100%"},
        {"search": "job"},
        {"customer": "Alpha"},
        {"batch": "Monthly", "status": "review"},
    ):
        rows = client.get("/api/inspection/items", params=params).json()["items"]
        assert [r["id"] for r in rows] == ["ra"]
    assert client.get("/api/inspection/items?search=unassigned").json()["items"][0]["id"] == "unknown"
    assert client.get("/api/inspection/items?start=2026-11-08&end=2026-11-01").status_code == 422
    assert client.get("/api/inspection/items?offset=-1").status_code == 422
    assert client.get("/api/inspection/round/ra/events?cursor=broken").status_code == 422
    assert client.get("/api/inspection/round/ra/events?category=bad").status_code == 422


def test_mixed_keyset_pagination_with_ties_and_new_arrivals(client, work):
    at = dt.datetime(2026, 1, 2, tzinfo=dt.UTC)
    with session_scope() as s:
        for i in range(65):
            s.add(
                AuditLog(
                    id=f"tie-{i:03}",
                    actor="tester",
                    action="review.answers",
                    entity="mapping",
                    entity_id="map",
                    details={},
                    created_at=at,
                )
            )
    with activity.engine().begin() as c:
        for i in range(70):
            c.execute(
                activity.events.insert().values(
                    job_id="job",
                    attempt=2,
                    span_id=f"span{i}",
                    parent_id=None,
                    role="mapper",
                    action="inspect",
                    state="completed",
                    at=at.isoformat(),
                    detail="{}",
                )
            )
    seen = []
    cursor = ""
    for page in range(10):
        r = client.get("/api/inspection/round/ra/events", params={"cursor": cursor})
        assert r.status_code == 200, r.text
        payload = r.json()
        seen += [e["key"] for e in payload["events"]]
        if page == 0:
            with session_scope() as s:
                s.add(
                    AuditLog(
                        id="arrived-later",
                        actor="tester",
                        action="review.answers",
                        entity="mapping",
                        entity_id="map",
                        details={},
                    )
                )
        cursor = payload["next_cursor"]
        if not cursor:
            break
    assert len(seen) == len(set(seen))
    assert len([k for k in seen if k.startswith("audit:tie-")]) == 65
    assert len([k for k in seen if k.startswith("activity:")]) == 72
    assert "audit:arrived-later" not in seen


def test_journal_unavailable_does_not_claim_complete_history(client, work, monkeypatch):
    def broken():
        raise RuntimeError("offline")

    monkeypatch.setattr(activity, "engine", broken)
    data = client.get("/api/inspection/round/ra/events").json()
    assert data["journal_available"] is False
    assert any(e["kind"] == "mapping" for e in data["events"])


def test_private_reports_and_incognito(client, work):
    a = {"x-forma-user": "alice"}
    b = {"x-forma-user": "bob"}
    created = client.post("/api/assistant/conversations", headers=a, json={"title": "Private report"})
    assert created.status_code < 300, created.text
    chat = created.json()["id"]
    with session_scope() as s:
        s.add(
            Job(
                id="report",
                kind="create_document",
                round_id="ra",
                payload={"chat_id": chat, "snapshot": {"sources": [{"rows": 12}], "template": "summary"}},
            )
        )
    assert client.get("/api/inspection/items?kind=assistant", headers=b).json()["total"] == 0
    assert client.get(f"/api/inspection/chat/{chat}/events", headers=b).status_code == 404
    assert client.get(f"/api/inspection/chat/{chat}/events/job/report", headers=b).status_code == 404
    assert client.get("/api/inspection/round/ra/events/job/report", headers=a).status_code == 404
    detail = client.get(f"/api/inspection/chat/{chat}/events/job/report", headers=a)
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["sources"] == [{"rows": 12}]
    assert client.get("/api/inspection/items?kind=assistant&search=report", headers=a).json()["total"] == 1
    incognito = client.post(
        "/api/assistant/conversations", headers=a, json={"title": "Hidden", "incognito": True}
    )
    assert incognito.status_code < 300
    assert client.get("/api/inspection/items?kind=assistant", headers=a).json()["total"] == 1


def test_real_mock_pipeline_is_visible_in_center(client, isolated_env):
    from tests.test_jobs import _round_with_file

    from forma.jobs import runner

    rid = _round_with_file(client, isolated_env, "INSPECT")
    jid = runner.enqueue("run_pipeline", {"round_id": rid, "use_llm": False}, round_id=rid, sync=True)
    assert runner.job_status(jid)["status"] == "done"
    page = client.get(f"/api/inspection/round/{rid}/events").json()
    assert page["journal_available"]
    keys = {e["key"] for e in page["events"]}
    assert f"job:{jid}" in keys
    assert any(e["kind"] == "mapping" for e in page["events"])
    assert any(e["kind"] == "activity" for e in page["events"])
    for e in page["events"]:
        assert client.get(f"/api/inspection/round/{rid}/events/{e['kind']}/{e['id']}").status_code == 200


def test_list_date_boundaries_and_pagination(client, work):
    with session_scope() as s:
        for i in range(28):
            s.add(
                ImportRound(
                    id=f"dated-{i:02}",
                    customer_id="a",
                    created_at=dt.datetime(2025, 12, 31, 23, 59, tzinfo=dt.UTC),
                )
            )
        s.add(ImportRound(id="next-day", customer_id="a", created_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC)))
    params = {"start": "2025-12-31", "end": "2025-12-31"}
    first = client.get("/api/inspection/items", params=params).json()
    second = client.get("/api/inspection/items", params={**params, "offset": first["next_offset"]}).json()
    assert first["total"] == 28 and len(first["items"]) == 25 and len(second["items"]) == 3
    assert not {i["id"] for i in first["items"]} & {i["id"] for i in second["items"]}
