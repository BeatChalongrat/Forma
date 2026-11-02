from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient
from tests.test_workspace import uncertain_mapping


@pytest.fixture
def client(isolated_env):
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def test_approved_answers_are_scoped_retirable_and_conflicts_abstain(client, isolated_env):
    from sqlalchemy import select

    from forma.db.models import Correction, ImportRound, MappingVersion
    from forma.db.session import session_scope
    from forma.mapping_dsl.models import MappingDocument
    from forma.services import decision_memory, mapping
    from forma.services.context import get_context

    r, mid, vat = uncertain_mapping(client, isolated_env)
    payload = {"answers": {vat: False}, "remember": [vat]}
    preview = client.post(f"/api/mappings/{mid}/answers/preview", json=payload).json()
    response = client.post(
        f"/api/mappings/{mid}/answers/apply", json={**payload, "digest": preview["digest"]}
    )
    assert response.status_code == 201, response.text
    new_id = response.json()["id"]
    with session_scope() as s:
        ctx = get_context()
        old, new = s.get(MappingVersion, mid), s.get(MappingVersion, new_id)
        round_ = s.get(ImportRound, r["id"])
        schema = ctx.registry.get_ref(new.target_schema)
        assert s.scalars(select(Correction).where(Correction.kind == "decision")).all() == []
        assert decision_memory.recall(s, round_, schema, MappingDocument.model_validate(old.document)) == []
        mapping.approve_mapping(s, new_id, "reviewer")
        s.flush()
        rows = s.scalars(select(Correction).where(Correction.kind == "decision")).all()
        assert len(rows) == 1 and rows[0].actor == "reviewer"
        doc = MappingDocument.model_validate(old.document)
        used = decision_memory.recall(s, round_, schema, doc)
        assert len(used) == 1 and used[0]["answer"] is False
        original_notes = round_.notes
        round_.notes = "Use new business rules"
        assert decision_memory.recall(s, round_, schema, MappingDocument.model_validate(old.document)) == []
        round_.notes = original_notes
        original_customer = round_.customer_id
        round_.customer_id = "other"
        with s.no_autoflush:
            assert (
                decision_memory.recall(s, round_, schema, MappingDocument.model_validate(old.document)) == []
            )
        round_.customer_id = original_customer
        rows[0].status = "retired"
        s.flush()
        assert not decision_memory.eligible(s, new)
        assert decision_memory.recall(s, round_, schema, MappingDocument.model_validate(old.document)) == []
        rows[0].status = "confirmed"
        conflicting = copy.deepcopy(rows[0].after)
        conflicting["after"] = {"op": "const", "value": True}
        s.add(
            Correction(
                customer_id=new.customer_id,
                kind="decision",
                target=vat,
                after=conflicting,
                actor="another-reviewer",
                mapping_version_id=new.id,
                round_id=round_.id,
            )
        )
        s.flush()
        assert not decision_memory.eligible(s, new)
        assert decision_memory.recall(s, round_, schema, MappingDocument.model_validate(old.document)) == []


def test_new_round_reuses_unit_rule_but_new_unit_token_forces_question(client, isolated_env):
    import csv

    from tests.test_learning import _round

    customer = client.post("/api/customers", json={"code": "UNITMEM", "name": "Unit memory"}).json()["id"]

    def book(name, token):
        path = isolated_env / name
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["SKU", "Product name", "Unit", "Price incl VAT", "Currency"])
            w.writerows(
                [[f"A{i:03d}", f"Item {i}", token if i < 3 else "PCS", i * 10, "THB"] for i in range(1, 7)]
            )
        return path

    r1 = _round(client, customer, book("first.csv", "BT"))
    mid = r1["summary"]["mapping_version_id"]
    qs = client.get(f"/api/mappings/{mid}/questions").json()["questions"]
    q = next(q for q in qs if q.get("action") == "value_map" and q["source_value"] == "BT")
    body = {"answers": {q["id"]: "BOTTLE"}}
    preview = client.post(f"/api/mappings/{mid}/answers/preview", json=body).json()
    saved = client.post(f"/api/mappings/{mid}/answers/apply", json={**body, "digest": preview["digest"]})
    assert saved.status_code == 201, saved.text
    approved = client.post(f"/api/mappings/{saved.json()['id']}/approve", json={})
    assert approved.status_code == 200, approved.text
    r2 = _round(client, customer, book("second.csv", "XYZ"))
    mid2 = r2["summary"]["mapping_version_id"]
    mapping2 = client.get(f"/api/mappings/{mid2}").json()
    assert mapping2["policy"]["source"] == "memory", {
        "drift": r2["summary"].get("drift"),
        "memory": r2["summary"].get("format_memory"),
        "history": [(d["action"], d["scope"]) for d in saved.json()["policy"].get("confirmed_decisions", [])],
    }
    assert mapping2["status"] == "proposed" and mapping2["policy"]["decision"] != "auto_approve"
    qs2 = client.get(f"/api/mappings/{mid2}/questions").json()["questions"]
    assert [q["source_value"] for q in qs2 if q.get("action") == "value_map"] == ["XYZ"]


def test_file_constant_is_not_learned_as_customer_convention(client, isolated_env):
    from sqlalchemy import select

    from forma.db.models import Correction, MappingVersion
    from forma.db.session import session_scope
    from forma.services import decision_memory, mapping

    _, mid, vat = uncertain_mapping(client, isolated_env)
    payload = {"answers": {vat: False}}
    preview = client.post(f"/api/mappings/{mid}/answers/preview", json=payload).json()
    response = client.post(
        f"/api/mappings/{mid}/answers/apply", json={**payload, "digest": preview["digest"]}
    )
    assert response.status_code == 201, response.text
    with session_scope() as s:
        new = s.get(MappingVersion, response.json()["id"])
        mapping.approve_mapping(s, new.id, "reviewer")
        s.flush()
        assert not decision_memory.eligible(s, new)
        assert not s.scalars(
            select(Correction).where(Correction.kind == "constant", Correction.target == vat)
        ).all()
