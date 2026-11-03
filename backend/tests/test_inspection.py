from __future__ import annotations

import copy
import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from tests.test_workspace import uncertain_mapping

from forma.db.models import AuditLog, Correction, MappingVersion
from forma.db.session import session_scope
from forma.services import inspection, mapping
from forma.services.rounds import audit


@pytest.fixture
def client(isolated_env):
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def test_answer_history_keeps_whole_file_check_and_version_boundaries(client, isolated_env):
    r, mid, vat = uncertain_mapping(client, isolated_env)
    payload = {"answers": {vat: False}, "remember": [vat]}
    preview = client.post(f"/api/mappings/{mid}/answers/preview", json=payload).json()
    response = client.post(
        f"/api/mappings/{mid}/answers/apply", json={**payload, "digest": preview["digest"]}
    )
    assert response.status_code == 201, response.text
    new_id = response.json()["id"]
    trace = client.get(f"/api/mappings/{new_id}/trace").json()
    assert trace["effect_check"]["passed"] is True
    assert trace["effect_check"]["records_checked"] == preview["comparison"]["validation"]["records_checked"]
    assert trace["effect_check"]["before_mapping_id"] == mid
    assert trace["decisions"][0]["answered_at"]
    assert trace["decisions"][0]["mapping_id"] == new_id
    assert trace["decisions"][0]["answer"] is False
    assert client.get(f"/api/mappings/{mid}/trace").json()["effect_check"] is None
    history = client.get(f"/api/rounds/{r['id']}/history?action=review.answers").json()
    assert history["total"] == 1 and history["items"][0]["entity_id"] == new_id

    with session_scope() as s:
        mapping.approve_mapping(s, new_id, "reviewer")
        s.flush()
        row = s.scalar(select(Correction).where(Correction.kind == "decision"))
        cid = row.id
        original = s.get(MappingVersion, new_id)
        clone = MappingVersion(
            customer_id=original.customer_id,
            round_id=r["id"],
            version=99,
            target_schema=original.target_schema,
            document=original.document,
            status="proposed",
            policy={"reused_from": new_id},
            agent_log=[{"agent": "test", "api_key": "not-for-display"}] * 23,
        )
        s.add(clone)
        s.flush()
        inspection.record_knowledge_use(s, clone)
        clone_id = clone.id
    detail = client.get(f"/api/learning/{cid}").json()
    assert detail["mapping_id"] == new_id and detail["reusable"]
    assert detail["usage"]["total"] == 1
    assert detail["usage"]["items"][0]["details"]["mapping_id"] == clone_id
    trace = client.get(f"/api/mappings/{clone_id}/trace").json()
    assert trace["knowledge"][0]["id"] == cid
    assert trace["logs"]["next_offset"] == 20
    assert trace["logs"]["items"][0]["api_key"] == "[redacted]"
    last = client.get(f"/api/mappings/{clone_id}/trace?offset=20").json()
    assert len(last["logs"]["items"]) == 3 and last["logs"]["next_offset"] is None

    with session_scope() as s:
        row = s.get(Correction, cid)
        conflicting = copy.deepcopy(row.after)
        conflicting["after"] = {"op": "const", "value": True}
        other = Correction(
            customer_id=row.customer_id,
            kind="decision",
            target=vat,
            after=conflicting,
            actor="second reviewer",
            mapping_version_id=new_id,
            round_id=r["id"],
        )
        s.add(other)
        s.flush()
        conflict_id = other.id
    assert client.get(f"/api/learning/{cid}").json()["conflicts"][0]["id"] == conflict_id
    assert client.delete(f"/api/learning/{cid}").status_code == 204
    retired = client.get("/api/learning?status=retired").json()["learned"]["items"]
    assert any(x["id"] == cid for x in retired)
    assert client.get(f"/api/learning/{cid}").json()["usage"]["total"] == 1
    assert client.get("/api/learning?search=second%20reviewer").json()["learned"]["total"] == 1

    denied = {"X-Forma-User": "reviewer", "X-Forma-Customers": "different-customer"}
    for url in [f"/api/learning/{cid}", f"/api/mappings/{new_id}/trace", f"/api/rounds/{r['id']}/history"]:
        assert client.get(url, headers=denied).status_code in (403, 404)
    assert client.get("/api/audit/events", headers={"X-Forma-User": "reviewer"}).status_code == 403
    assert client.get("/api/audit/events", headers={**denied, "X-Forma-User": "admin"}).status_code == 403


def test_audit_pagination_dates_filters_and_redaction(client):
    with session_scope() as s:
        for i in range(35):
            row = AuditLog(
                actor="audit-user",
                action="test.audit",
                entity="mapping",
                entity_id="map_target",
                details={"i": i, "nested": {"authorization": "Bearer private"}},
                created_at=dt.datetime(2026, 1, 2, 12, i, tzinfo=dt.UTC),
            )
            s.add(row)
        audit(s, "other.audit", "mapping", "map_other", actor="other")
    url = "/api/audit/events?action=test.audit&actor=audit-user&entity_id=map_target&start=2026-01-02&end=2026-01-02"
    first = client.get(url).json()
    assert first["total"] == 35 and len(first["items"]) == 30 and first["next_offset"] == 30
    assert first["items"][0]["details"]["i"] == 34
    assert first["items"][0]["details"]["nested"]["authorization"] == "[redacted]"
    last = client.get(url + "&offset=30").json()
    assert len(last["items"]) == 5 and last["next_offset"] is None
    assert not ({x["id"] for x in first["items"]} & {x["id"] for x in last["items"]})
    assert client.get("/api/audit/events?start=2026-01-03&end=2026-01-01").status_code == 422
    assert client.get("/api/audit/events?offset=-1").status_code == 422
    assert inspection.safe_detail("sk-" + "x" * 30) == "[redacted]"


def test_knowledge_pages_are_stable_when_timestamps_match(client):
    with session_scope() as s:
        for i in range(55):
            s.add(
                Correction(
                    customer_id="customer-a",
                    kind="alias",
                    target="/sku",
                    header=f"item {i}",
                    after={"column": f"item {i}"},
                    actor="teacher",
                    status="confirmed",
                    created_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
                )
            )
    a = client.get("/api/learning?search=item&offset=0").json()["learned"]
    b = client.get("/api/learning?search=item&offset=50").json()["learned"]
    assert a["total"] == 55 and len(a["items"]) == 50 and len(b["items"]) == 5
    assert not ({x["id"] for x in a["items"]} & {x["id"] for x in b["items"]})
    assert a == client.get("/api/learning?search=item&offset=0").json()["learned"]
