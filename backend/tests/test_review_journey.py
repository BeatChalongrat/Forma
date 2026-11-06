from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from tests.test_workspace import uncertain_mapping


@pytest.fixture
def client(isolated_env):
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def prepare(c, path):
    r, mid, ptr = uncertain_mapping(c, path)
    q = c.get(f"/api/mappings/{mid}/questions").json()
    return r, mid, {"answers": {ptr: False}, "acknowledged": q["open_questions"]}


def test_drafts_survive_sessions_and_cannot_overwrite_another_tab(client, isolated_env):
    _, mid, body = prepare(client, isolated_env)
    url = f"/api/mappings/{mid}/review/draft"
    payload = {"answers": body["answers"], "comment": "Keep false and zero", "section": "finish"}
    assert client.get(url).json()["revision"] == 0
    response = client.put(url, json={"revision": 0, "payload": payload})
    assert response.status_code == 200, response.text
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: client.put(url, json={"revision": 1, "payload": payload}), range(2))
        )
    assert sorted(r.status_code for r in results) == [200, 409]
    assert client.get(url).json()["revision"] == 2
    queue = client.get("/api/review").json()
    task = next(t for t in queue if t["mapping_version_id"] == mid)
    assert task["draft"]["answers"] == 1
    other_queue = client.get("/api/review", headers={"X-Forma-User": "reviewer"}).json()
    assert next(t for t in other_queue if t["mapping_version_id"] == mid)["draft"] is None
    assert client.get(url, headers={"X-Forma-User": "reviewer"}).json()["revision"] == 0
    assert client.get(url, headers={"X-Forma-Customers": ""}).status_code in (403, 404)
    from forma.api.app import create_app

    with TestClient(create_app()) as reopened:
        assert reopened.get(url).json()["payload"]["answers"] == body["answers"]


def test_preview_is_stable_and_confirmation_is_idempotent(client, isolated_env, monkeypatch):
    from forma.jobs import runner

    _, mid, body = prepare(client, isolated_env)
    monkeypatch.setattr(runner, "deliver_jobs", lambda ids: None)
    url = f"/api/mappings/{mid}/review"
    first = client.post(url + "/preview", json=body)
    assert first.status_code == 200, first.text
    second = client.post(url + "/preview", json=body)
    assert first.json()["digest"] == second.json()["digest"]
    data = {**body, "digest": first.json()["digest"]}
    bad = client.post(url + "/confirm", json={**data, "digest": "stale"})
    assert bad.status_code == 409
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: client.post(url + "/confirm", json=data), range(2)))
    assert [r.status_code for r in results] == [200, 200], [r.text for r in results]
    assert results[0].json() == results[1].json()
    assert results[0].json()["state"] == "processing"
    assert results[0].json()["job_id"]
    assert client.put(url + "/draft", json={"revision": 0, "payload": {}}).status_code == 409


def test_partial_answers_do_not_approve_and_drafts_are_not_decisions(client, isolated_env):
    _, mid, body = prepare(client, isolated_env)
    url = f"/api/mappings/{mid}/review"
    assert client.put(url + "/draft", json={"revision": 0, "payload": body}).status_code == 200
    assert client.get(f"/api/mappings/{mid}").json()["status"] == "proposed"
    body["acknowledged"] = []
    proof = client.post(url + "/preview", json=body)
    assert proof.status_code == 200, proof.text
    done = client.post(url + "/confirm", json={**body, "digest": proof.json()["digest"]})
    assert done.status_code == 200, done.text
    assert done.json()["state"] == "needs_review"
    assert done.json()["job_id"] is None
    assert client.get(f"/api/mappings/{done.json()['mapping_id']}").json()["status"] == "proposed"


def test_review_through_download_without_remapping(client, isolated_env, monkeypatch):
    from forma.jobs import runner
    from forma.services import mapping

    r, mid, body = prepare(client, isolated_env)
    deliver = runner.deliver_jobs
    monkeypatch.setattr(runner, "deliver_jobs", lambda ids: deliver(ids, sync=True))

    def forbidden(*args, **kwargs):
        raise AssertionError("Review must not invoke the mapper again")

    monkeypatch.setattr(mapping, "propose_mapping", forbidden)
    url = f"/api/mappings/{mid}/review"
    proof = client.post(url + "/preview", json=body)
    assert proof.status_code == 200, proof.text
    confirmed = client.post(url + "/confirm", json={**body, "digest": proof.json()["digest"]})
    assert confirmed.status_code == 200, confirmed.text
    job = client.get(f"/api/jobs/{confirmed.json()['job_id']}").json()
    assert job["status"] == "done", job
    result = client.get(f"/api/rounds/{r['id']}").json()
    dsid = result["summary"]["dataset_version_id"]
    ds = client.get(f"/api/datasets/{dsid}").json()
    assert ds["mapping_version_id"] == confirmed.json()["mapping_id"]
    assert ds["row_count"] > 0 and ds["invalid_records"] == 0
    approved = client.post(f"/api/datasets/{dsid}/approve", json={"actor": "operator"})
    assert approved.status_code == 200, approved.text
    exported = client.post(f"/api/datasets/{dsid}/export?fmt=json")
    assert exported.status_code == 201, exported.text
    downloaded = client.get(f"/api/exports/{exported.json()['id']}/download")
    assert downloaded.status_code == 200
    assert len(downloaded.json()) == ds["row_count"]


def test_new_version_or_changed_policy_requires_a_fresh_preview(client, isolated_env):
    from forma.db.models import MappingVersion
    from forma.db.session import session_scope

    _, mid, body = prepare(client, isolated_env)
    url = f"/api/mappings/{mid}/review"
    proof = client.post(url + "/preview", json=body).json()
    with session_scope() as s:
        m = s.get(MappingVersion, mid)
        m.policy = {**m.policy, "reasons": [*m.policy.get("reasons", []), "A new check needs review"]}
    response = client.post(url + "/confirm", json={**body, "digest": proof["digest"]})
    assert response.status_code == 409
    original = client.get(f"/api/mappings/{mid}").json()
    edited = client.post(f"/api/mappings/{mid}/edit", json={"document": original["document"]})
    assert edited.status_code == 201, edited.text
    assert client.post(url + "/preview", json=body).status_code == 409
    assert client.put(url + "/draft", json={"revision": 0, "payload": body}).status_code == 409


def test_draft_is_not_editable_without_reviewer_permission(client, isolated_env):
    _, mid, body = prepare(client, isolated_env)
    headers = {"X-Forma-User": "importer", "X-Forma-Roles": "importer"}
    url = f"/api/mappings/{mid}/review"
    assert (
        client.put(url + "/draft", json={"revision": 0, "payload": body}, headers=headers).status_code == 403
    )
    assert client.post(url + "/confirm", json=body, headers=headers).status_code == 403


def test_finish_job_refuses_to_transform_an_outdated_review(client, isolated_env, monkeypatch):
    from forma.jobs import runner

    r, mid, body = prepare(client, isolated_env)
    monkeypatch.setattr(runner, "deliver_jobs", lambda ids: None)
    url = f"/api/mappings/{mid}/review"
    proof = client.post(url + "/preview", json=body).json()
    confirmed = client.post(url + "/confirm", json={**body, "digest": proof["digest"]}).json()
    new = client.get(f"/api/mappings/{confirmed['mapping_id']}").json()
    edited = client.post(f"/api/mappings/{new['id']}/edit", json={"document": new["document"]})
    assert edited.status_code == 201, edited.text
    runner.run_job(confirmed["job_id"])
    job = client.get(f"/api/jobs/{confirmed['job_id']}").json()
    assert job["status"] == "failed"
    latest = client.get(f"/api/rounds/{r['id']}").json()
    assert latest["summary"]["mapping_version_id"] == edited.json()["id"]
    assert not any(d["mapping_version_id"] == new["id"] for d in latest["datasets"])


def test_persistent_corrections_require_checking_actual_output(client, isolated_env, monkeypatch):
    from forma.db.models import ImportRound
    from forma.db.session import session_scope
    from forma.jobs import runner
    from forma.services import datasets

    r, mid, body = prepare(client, isolated_env)
    with session_scope() as s:
        delivery = s.get(ImportRound, r["id"])
        delivery.summary = {**delivery.summary, "auto_publish": True}

    def correct_records(session, customer_id, schema, records):
        records[0]["product_name"] = "Previously saved correction"
        return records, {"rows_affected": 1, "rows_deleted": 0}

    monkeypatch.setattr(datasets, "apply_persistent_overrides", correct_records)
    deliver = runner.deliver_jobs
    monkeypatch.setattr(runner, "deliver_jobs", lambda ids: deliver(ids, sync=True))
    url = f"/api/mappings/{mid}/review"
    proof = client.post(url + "/preview", json=body)
    assert proof.status_code == 200, proof.text
    result = client.post(url + "/confirm", json={**body, "digest": proof.json()["digest"]})
    assert result.status_code == 200, result.text
    job = client.get(f"/api/jobs/{result.json()['job_id']}").json()
    assert job["status"] == "done", job
    delivery = client.get(f"/api/rounds/{r['id']}").json()
    output = client.get(f"/api/datasets/{delivery['summary']['dataset_version_id']}").json()
    assert output["status"] == "draft"
    assert output["stats"]["overrides"]["rows_affected"] == 1
    assert output["exports"] == []
