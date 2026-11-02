from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.test_workspace import uncertain_mapping


@pytest.fixture
def client(isolated_env: Path) -> Iterator[TestClient]:
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def test_real_cells_constants_and_scoped_evidence(client: TestClient, isolated_env: Path) -> None:
    r, mid, vat = uncertain_mapping(client, isolated_env)
    q = client.get(f"/api/mappings/{mid}/questions").json()
    pack = next(f["pointer"] for f in q["fields"] if f["semantic"] == "unit.pack_size")
    url = f"/api/mappings/{mid}/questions/evidence"
    response = client.get(url, params={"field": pack})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["sample_limit"] == 50 and len(data["examples"]) == 5
    first, second = data["examples"][:2]
    assert first["identity"] == "A001" and first["value"] is None
    assert second["identity"] == "A002" and second["value"] == 12
    assert second["source_complete"]
    cell = second["cells"][0]
    assert cell["raw"] == "ลัง(12)" and cell["row"] == 5 and cell["column"] == "F"
    assert cell["sheet"] == "สินค้า" and cell["verified"] is True
    assert q["context"]["files"][0]["name"] == r["files"][0]["name"]
    fixed = client.get(url, params={"field": vat}).json()
    assert fixed["constant"] and fixed["context_only"]
    assert fixed["examples"][0]["value"] is True
    assert "107" in fixed["examples"][0]["cells"][0]["raw"]
    assert client.get(url, params={"field": pack}, headers={"X-Forma-Customers": ""}).status_code == 403
    assert client.get(url, params={"field": "/not-a-field"}).status_code == 422


def test_preview_compares_actual_versions_without_mutation(client: TestClient, isolated_env: Path) -> None:
    from forma.db.models import MappingVersion
    from forma.db.session import session_scope

    _, mid, vat = uncertain_mapping(client, isolated_env)
    before = client.get(f"/api/mappings/{mid}").json()["document"]
    # The old cached sample is not a reliable basis for before/after counts.
    with session_scope() as session:
        m = session.get(MappingVersion, mid)
        assert m
        m.policy = {**m.policy, "sample": []}
    result = client.post(f"/api/mappings/{mid}/answers/preview", json={"answers": {vat: False}})
    assert result.status_code == 200, result.text
    comparison = result.json()["comparison"]
    assert comparison["reliable"], comparison
    assert comparison["before_records"] == comparison["after_records"] == 12
    assert comparison["counts"]["changed_records"] == 12
    assert comparison["counts"]["lost"] == comparison["counts"]["gained"] == 0
    assert all(row["changes"][0]["before"] is True for row in comparison["rows"])
    assert all(row["changes"][0]["after"] is False for row in comparison["rows"])
    assert client.get(f"/api/mappings/{mid}").json()["document"] == before


def test_collection_evidence_follows_item_rows(client: TestClient, isolated_env: Path) -> None:
    _, mid, _ = uncertain_mapping(client, isolated_env)
    q = client.get(f"/api/mappings/{mid}/questions").json()
    pointer = next(f["pointer"] for f in q["fields"] if f["semantic"] == "stock.qty")
    data = client.get(f"/api/mappings/{mid}/questions/evidence", params={"field": pointer}).json()
    rows = data["examples"]
    assert rows[0]["identity"] == rows[1]["identity"] == "A001"
    assert [r["value"] for r in rows[:2]] == [3, 1]
    assert [r["cells"][0]["row"] for r in rows[:2]] == [2, 3]
    assert all(r["cells"][0]["sheet"] == "Stock" for r in rows)


def test_missing_sources_never_fabricate_examples(client: TestClient, isolated_env: Path) -> None:
    from forma.db.models import SourceFile
    from forma.db.session import session_scope

    r, mid, vat = uncertain_mapping(client, isolated_env)
    with session_scope() as s:
        file = s.get(SourceFile, r["files"][0]["id"])
        assert file
        file.status = "ignored"
    data = client.get(f"/api/mappings/{mid}/questions/evidence", params={"field": vat}).json()
    assert data["unavailable"] and data["examples"] == []


def test_answer_gate_detects_unrelated_change_beyond_ui_sample(schema, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from tests.test_decision_actions import document

    from forma.core.errors import ValidationFailed
    from forma.services import mapping, review_evidence

    records = [{"sku": f"{i:04d}", "product_name": "unchanged", "currency": "THB"} for i in range(70)]
    changed = [dict(r) for r in records]
    changed[65]["product_name"] = "unexpected overwrite"
    results = iter(
        [
            SimpleNamespace(records=records, errors=[], stats={}),
            SimpleNamespace(records=changed, errors=[], stats={}),
        ]
    )

    def simulate(*args, limit):
        assert limit is None  # The UI's first 50 rows would miss record 65.
        return next(results), []

    monkeypatch.setattr(mapping, "simulate", simulate)
    session = Mock()
    session.get.return_value = SimpleNamespace(files=[])
    ctx = SimpleNamespace(registry=SimpleNamespace(get_ref=lambda _: schema))
    doc = document().model_dump(mode="json")
    m = SimpleNamespace(round_id="round", target_schema=schema.ref, document=doc)
    with pytest.raises(ValidationFailed, match="unrelated field"):
        review_evidence.validate_answer_effects(ctx, session, m, doc, [{"field": {"pointer": "/currency"}}])


def test_answer_gate_rejects_moving_an_existing_error_to_another_record(schema, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from tests.test_decision_actions import document

    from forma.core.errors import ValidationFailed
    from forma.services import mapping, review_evidence

    before = [
        {"sku": "A", "product_name": "A", "currency": "bad"},
        {"sku": "B", "product_name": "B", "currency": "THB"},
    ]
    after = [{**before[0], "currency": "THB"}, {**before[1], "currency": "bad"}]
    results = iter(
        [
            SimpleNamespace(records=before, errors=[], stats={"invalid_records": 1}),
            SimpleNamespace(records=after, errors=[], stats={"invalid_records": 1}),
        ]
    )
    monkeypatch.setattr(mapping, "simulate", lambda *args, **kwargs: (next(results), []))
    session = Mock()
    session.get.return_value = SimpleNamespace(files=[])
    ctx = SimpleNamespace(registry=SimpleNamespace(get_ref=lambda _: schema))
    doc = document().model_dump(mode="json")
    m = SimpleNamespace(round_id="round", target_schema=schema.ref, document=doc)
    with pytest.raises(ValidationFailed, match="previously valid record"):
        review_evidence.validate_answer_effects(ctx, session, m, doc, [{"field": {"pointer": "/currency"}}])


def test_additional_note_acknowledgement_is_explicit_audited_and_cannot_skip_typed_question(
    client, isolated_env
):
    from forma.db.models import MappingVersion
    from forma.db.session import session_scope

    _, mid, vat = uncertain_mapping(client, isolated_env)
    with session_scope() as s:
        m = s.get(MappingVersion, mid)
        m.open_questions = ["Is VAT included?", "Optional missing fields reviewed"]
        m.policy = {
            **m.policy,
            "clarifications": [
                {
                    "target": vat,
                    "kind": "boolean",
                    "question": "Is VAT included?",
                    "why": "Missing business fact",
                }
            ],
        }
    assert (
        client.post(
            f"/api/mappings/{mid}/answers/preview", json={"answers": {}, "acknowledged": ["Is VAT included?"]}
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/mappings/{mid}/answers/preview",
            json={"answers": {}, "acknowledged": ["not an existing note"]},
        ).status_code
        == 422
    )
    body = {"answers": {}, "acknowledged": ["Optional missing fields reviewed"]}
    preview = client.post(f"/api/mappings/{mid}/answers/preview", json=body)
    assert preview.status_code == 200, preview.text
    assert preview.json()["remaining_questions"] == ["Is VAT included?"]
    assert preview.json()["comparison"]["counts"]["changed_records"] == 0
    saved = client.post(
        f"/api/mappings/{mid}/answers/apply", json={**body, "digest": preview.json()["digest"]}
    )
    assert saved.status_code == 201, saved.text
    assert (
        saved.json()["policy"]["acknowledged_questions"][0]["question"] == "Optional missing fields reviewed"
    )
    assert saved.json()["open_questions"] == ["Is VAT included?"]
