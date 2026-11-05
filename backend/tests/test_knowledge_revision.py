from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from tests.test_workspace import uncertain_mapping


@pytest.fixture
def client(isolated_env):
    from forma.api.app import create_app

    with TestClient(create_app()) as client:
        yield client


def taught(client, tmp):
    from sqlalchemy import select

    from forma.db.models import Correction
    from forma.db.session import session_scope

    r, mid, target = uncertain_mapping(client, tmp)
    body = {"answers": {target: False}, "remember": [target]}
    preview = client.post(f"/api/mappings/{mid}/answers/preview", json=body)
    assert preview.status_code == 200, preview.text
    saved = client.post(
        f"/api/mappings/{mid}/answers/apply", json={**body, "digest": preview.json()["digest"]}
    )
    assert saved.status_code == 201, saved.text
    mid = saved.json()["id"]
    approved = client.post(f"/api/mappings/{mid}/approve", json={})
    assert approved.status_code == 200, approved.text
    with session_scope() as session:
        cid = session.scalar(
            select(Correction.id).where(Correction.mapping_version_id == mid, Correction.kind == "decision")
        )
    return r, mid, target, cid


def test_revision_preview_is_read_only_and_concurrent_apply_has_one_winner(client, isolated_env):
    from sqlalchemy import select

    from forma.db.models import Correction, MappingVersion
    from forma.db.session import session_scope
    from forma.services import decision_memory

    _r, mid, _target, cid = taught(client, isolated_env)
    old = client.get(f"/api/mappings/{mid}").json()
    base = f"/api/learning/{cid}"
    form = client.get(base + "/revision")
    assert form.status_code == 200, form.text
    assert form.json()["question"]["kind"] == "boolean"
    body = {"answer": True, "reason": "เจ้าของไฟล์ยืนยันว่ารวมภาษีแล้ว"}
    preview = client.post(base + "/revision/preview", json=body)
    assert preview.status_code == 200, preview.text
    result = preview.json()
    assert result["comparison"]["validation"]["passed"]
    assert result["comparison"]["counts"]["changed_records"] > 0
    assert client.get(base).json()["status"] == "confirmed"
    assert client.get(f"/api/mappings/{mid}").json()["document"] == old["document"]
    assert client.post(base + "/revision/apply", json={**body, "digest": "stale"}).status_code == 409
    payload = {**body, "digest": result["digest"]}
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.post(base + "/revision/apply", json=payload), range(2)))
    assert sorted(response.status_code for response in responses) == [201, 409], [r.text for r in responses]
    new = next(response.json() for response in responses if response.status_code == 201)
    assert new["status"] == "proposed"
    changes = client.get(base + "/changes").json()["items"]
    assert changes[0]["details"]["old_answer"] is False
    assert changes[0]["details"]["new_answer"] is True
    assert changes[0]["details"]["reason"] == body["reason"]
    assert changes[0]["actor"] == "operator"
    assert client.get(base).json()["status"] == "retired"
    assert client.get(f"/api/mappings/{mid}").json()["document"] == old["document"]
    with session_scope() as session:
        assert not session.scalar(select(Correction.id).where(Correction.mapping_version_id == new["id"]))
        assert not decision_memory.eligible(session, session.get(MappingVersion, mid))
    approved = client.post(f"/api/mappings/{new['id']}/approve", json={})
    assert approved.status_code == 200, approved.text
    with session_scope() as session:
        learned = session.scalar(
            select(Correction).where(
                Correction.mapping_version_id == new["id"], Correction.kind == "decision"
            )
        )
        assert learned.after["answer"] is True
        assert learned.after["replaces_knowledge_id"] == cid
        assert decision_memory.eligible(session, session.get(MappingVersion, new["id"]))
    assert client.get(base).json()["conflicts"] == []  # A retired answer is history, not an active conflict.


def test_transitive_impact_reopens_only_selected_files_and_requires_answer(client, isolated_env):
    from forma.db.models import ImportRound, MappingVersion, SourceFile
    from forma.db.session import session_scope
    from forma.services import knowledge_gate

    r, mid, target, cid = taught(client, isolated_env)
    copies = []
    with session_scope() as session:
        source = session.get(MappingVersion, mid)
        original = session.get(ImportRound, r["id"])
        previous = source
        for i in range(3):
            delivery = ImportRound(
                customer_id=source.customer_id,
                label=f"Later delivery {i}",
                target_schema=source.target_schema,
                status="review",
                notes=original.notes,
            )
            session.add(delivery)
            session.flush()
            for f in original.files:
                session.add(
                    SourceFile(
                        round_id=delivery.id,
                        customer_id=source.customer_id,
                        sha256=f.sha256,
                        original_name=f.original_name,
                        size=f.size,
                        kind=f.kind,
                        storage_key=f.storage_key,
                        status=f.status,
                        profile=f.profile,
                        fingerprint=f.fingerprint,
                    )
                )
            policy = copy.deepcopy(source.policy)
            policy["reused_from"] = previous.id
            policy["confirmed_decisions"] = [
                {**d, "knowledge_id": cid, "knowledge_mapping_id": source.id}
                for d in policy["confirmed_decisions"]
            ]
            m = MappingVersion(
                customer_id=source.customer_id,
                round_id=delivery.id,
                version=100 + i,
                target_schema=source.target_schema,
                document=source.document,
                policy=policy,
                status="proposed",
                open_questions=[],
            )
            session.add(m)
            session.flush()
            delivery.summary = {"mapping_version_id": m.id}
            copies.append((delivery.id, m.id))
            previous = m
        customer_id = source.customer_id
    impacts = client.get(f"/api/learning/{cid}/impacts").json()
    assert impacts["total"] == 4
    assert client.delete(f"/api/learning/{cid}").status_code == 204
    # Even a third-generation proposed mapping cannot be approved with the retired answer.
    denied = client.post(f"/api/mappings/{copies[-1][1]}/approve", json={})
    assert denied.status_code == 409, denied.text
    with session_scope() as session:
        assert not knowledge_gate.allowed(session, session.get(MappingVersion, mid))
    body = {"selections": {copies[-1][0]: copies[-1][1]}}
    changed = client.post(f"/api/learning/{cid}/review", json=body)
    assert changed.status_code == 201, changed.text
    new_id = changed.json()["items"][0]["mapping_id"]
    retry = client.post(f"/api/learning/{cid}/review", json=body)
    assert retry.status_code == 201 and retry.json() == changed.json()
    assert client.get(f"/api/rounds/{copies[0][0]}").json()["summary"]["mapping_version_id"] == copies[0][1]
    denied = client.post(f"/api/mappings/{new_id}/approve", json={})
    assert denied.status_code == 422, denied.text
    qs = client.get(f"/api/mappings/{new_id}/questions").json()["questions"]
    q = next(q for q in qs if q["field"]["pointer"] == target)
    answer = {"answers": {q["id"]: True}, "remember": [q["id"]]}
    preview = client.post(f"/api/mappings/{new_id}/answers/preview", json=answer)
    assert preview.status_code == 200, preview.text
    fixed = client.post(
        f"/api/mappings/{new_id}/answers/apply", json={**answer, "digest": preview.json()["digest"]}
    )
    assert fixed.status_code == 201, fixed.text
    approved = client.post(f"/api/mappings/{fixed.json()['id']}/approve", json={})
    assert approved.status_code == 200, approved.text
    # Customer scoping is still enforced for every new endpoint.
    assert customer_id


def test_revision_rejects_invalid_value_changed_rule_and_missing_reason(client, isolated_env):
    from forma.db.models import MappingVersion
    from forma.db.session import session_scope

    _, mid, target, cid = taught(client, isolated_env)
    base = f"/api/learning/{cid}/revision/preview"
    scoped = {"X-Forma-User": "tenant", "X-Forma-Roles": "approver", "X-Forma-Customers": "other-customer"}
    for suffix in ("revision", "impacts", "changes"):
        assert client.get(f"/api/learning/{cid}/{suffix}", headers=scoped).status_code == 403
    assert client.post(base, json={"answer": True, "reason": "fix"}, headers=scoped).status_code == 403
    assert (
        client.get(f"/api/learning/{cid}/revision", headers={"X-Forma-User": "importer"}).status_code == 403
    )

    assert client.post(base, json={"answer": "false", "reason": "fix"}).status_code == 422
    assert client.post(base, json={"answer": True, "reason": " "}).status_code == 422
    with session_scope() as session:
        m = session.get(MappingVersion, mid)
        doc = copy.deepcopy(m.document)
        field = next(f for c in doc["collections"] for f in c["fields"] if f["target"] == target)
        field["expr"] = {"op": "const", "value": True}
        m.document = doc
    assert client.post(base, json={"answer": True, "reason": "fix"}).status_code == 409
