"""Pilot uses isolated state, authenticated review and immutable evidence; no paid calls."""

import pytest
from fastapi.testclient import TestClient
from tests.test_review_journey import prepare


@pytest.fixture
def client(isolated_env, monkeypatch):
    monkeypatch.setenv("FORMA_PILOT_MODE", "true")
    monkeypatch.setenv("FORMA_LLM_MOCK_ONLY", "true")
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def test_observations_validate_scope_preserve_history_and_do_not_answer(client, isolated_env):
    _, mid, body = prepare(client, isolated_env)
    qid = next(iter(body["answers"]))
    url = f"/api/pilot/mappings/{mid}/observations"
    inventory, offset = [], 0
    while True:
        page = client.get(f"/api/pilot/questions?limit=1&offset={offset}").json()
        inventory.extend(page["items"])
        offset = page["next_offset"]
        if offset is None:
            break
    assert any(q["mapping_id"] == mid and q["observation"] is None for q in inventory)
    assert client.get("/api/pilot/questions", headers={"X-Forma-Customers": ""}).json()["items"] == []
    data = {"question_id": qid, "category": "evidence_in_file", "note": "Header explicitly says VAT included"}
    assert client.post(url, json=data).status_code == 422
    data["evidence_location"] = "products.xlsx / Products / H1"
    assert client.post(url, json={**data, "question_id": "unknown"}).status_code == 422
    assert client.post(url, json=data, headers={"X-Forma-Customers": ""}).status_code in (403, 404)
    assert client.post(url, json=data, headers={"X-Forma-User": "importer"}).status_code == 403
    saved = client.post(url, json=data)
    assert saved.status_code == 200, saved.text
    assert saved.json()["latest"][qid]["synthetic"] is True
    assert (
        client.post(
            url,
            json={**data, "category": "missing_information", "note": "Correction: applies to another sheet"},
        ).status_code
        == 200
    )
    history = client.get(url).json()
    assert len(history["history"]) == 2
    assert history["latest"][qid]["category"] == "missing_information"
    assert client.get(f"/api/mappings/{mid}").json()["status"] == "proposed"
    assert client.get(f"/api/mappings/{mid}/review/draft").json()["revision"] == 0
    report = client.get("/api/pilot/observations?limit=1").json()
    assert len(report["items"]) == 1 and report["next_offset"] == 1
    assert client.get("/api/pilot/observations", headers={"X-Forma-Customers": ""}).json()["items"] == []


def test_pilot_requires_human_approval_of_exact_output_version(client, isolated_env, monkeypatch):
    from sqlalchemy import delete

    from forma.core.errors import Conflict
    from forma.db.models import AuditLog, DatasetVersion
    from forma.db.session import session_scope
    from forma.jobs import runner
    from forma.services import datasets, publishing
    from forma.services.context import get_context

    r, mid, body = prepare(client, isolated_env)
    deliver = runner.deliver_jobs
    monkeypatch.setattr(runner, "deliver_jobs", lambda ids: deliver(ids, sync=True))
    url = f"/api/mappings/{mid}/review"
    proof = client.post(url + "/preview", json=body).json()
    assert client.post(url + "/confirm", json={**body, "digest": proof["digest"]}).status_code == 200
    dsid = client.get(f"/api/rounds/{r['id']}").json()["summary"]["dataset_version_id"]
    assert client.post(f"/api/datasets/{dsid}/export?fmt=json").status_code == 409
    with session_scope() as s:
        ctx = get_context()
        assert publishing.auto_publish(ctx, s, r["id"])["decision"] == "review"
        with pytest.raises(Conflict):
            datasets.approve_dataset(s, dsid, "policy", ctx=ctx)
        s.get(DatasetVersion, dsid).status = "approved"
        s.execute(delete(AuditLog).where(AuditLog.action == "dataset.approve", AuditLog.entity_id == dsid))
    # A restored/stale status alone is insufficient.
    assert client.post(f"/api/datasets/{dsid}/export?fmt=json").status_code == 409
    assert (
        client.post(
            f"/api/datasets/{dsid}/approve", json={"comment": "Pilot operator checked output"}
        ).status_code
        == 200
    )
    exported = client.post(f"/api/datasets/{dsid}/export?fmt=json")
    assert exported.status_code == 201, exported.text
    assert client.get(f"/api/exports/{exported.json()['id']}/download").status_code == 200


def test_pilot_flag_defaults_off():
    from forma.core.settings import Settings

    assert not Settings(_env_file=None).pilot_mode


def test_bulk_client_cannot_enable_automatic_export_in_pilot(client, isolated_env):
    from tests.test_api import make_workbook
    from tests.test_batches import _batch

    assert client.get("/api/me").json()["pilot_mode"] is True
    book = make_workbook(isolated_env / "pilot.xlsx")
    result = _batch(client, [("pilot.xlsx", book.read_bytes())], auto_publish=True, use_llm=False)
    assert result["auto_publish"] is False
