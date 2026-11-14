"""Evidence-based discovery and human decisions, independent of benchmark filenames."""

from __future__ import annotations

import copy
import csv

import pytest
from fastapi.testclient import TestClient
from tests.test_api import _run, _upload

from forma.ai.heuristics import propose_mapping
from forma.ai.source_evidence import candidates, currency_questions, validate_currency_contract
from forma.core.errors import ValidationFailed
from forma.mapping_dsl.models import FieldMapping, col, const
from forma.profiling.profile import merge_profiles, profile_workbook
from forma.schema_registry import TargetSchema


def contract():
    return {
        "title": "Evidence contract",
        "type": "object",
        "required": ["sku", "price"],
        "properties": {
            "sku": {"type": "string", "x-forma": {"semantic": "sku", "risk": "critical"}},
            "price": {
                "type": "number",
                "description": "Amount charged per item",
                "x-forma": {"risk": "critical"},
            },
            "currency": {"type": ["string", "null"], "x-forma": {"risk": "critical"}},
        },
        "x-forma": {"schema_id": "evidence", "version": 1, "identity_keys": ["/sku"]},
    }


def fixture(tmp_path, headers=None, rows=None, document=None):
    headers = headers or ["SKU", "Price", "Currency", "Invoice Currency"]
    rows = rows or [[f"A{i:03}", i * 10, "THB", "USD" if i % 2 else "EUR"] for i in range(1, 13)]
    path = tmp_path / "arbitrary.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)
    schema = TargetSchema("evidence", 1, document or contract(), None)
    profile = merge_profiles([("file", path.name, profile_workbook(path))])
    doc = propose_mapping(profile, schema, customer_id="test").mapping
    columns = {
        doc.record.root_source: [c.to_dict() | {"file_sha256": "original"} for c in profile.tables[0].columns]
    }
    return schema, profile, doc, columns, path


@pytest.mark.parametrize("header", ["Charge per item", "Montant", "新しい値"])
def test_unfamiliar_columns_are_candidates_not_automatic_answers(tmp_path, header):
    schema, profile, doc, _, _ = fixture(
        tmp_path, ["SKU", header, "other number"], [[f"A{i}", i * 10, i] for i in range(1, 13)]
    )
    doc.fields = [f for f in doc.fields if f.target != "/price"]
    before = doc.model_dump()
    result = candidates(profile, schema, doc)
    price = next(f for f in result["fields"] if f["target"] == "/price")
    assert not price["mapped"] and price["candidate_count"] == 2
    assert {c["column"] for c in price["candidates"]} == {header, "other number"}
    profile.tables[0].columns.reverse()
    assert candidates(profile, schema, doc) == result
    assert doc.model_dump() == before  # No guess just because a column is unclaimed.


def test_currency_conflict_is_independent_of_confidence_and_confirmation_is_scoped(tmp_path):
    schema, _, doc, columns, _ = fixture(tmp_path)
    fm = next(f for f in doc.fields if f.target == "/currency")
    fm.confidence = 1
    (q,) = currency_questions(doc, schema, columns)
    assert q["kind"] == "choice" and set(q["options"]) == {"Currency", "Invoice Currency"}
    fm.expr = col("Invoice Currency")
    receipt = {"evidence_key": q["evidence_key"], "expression": fm.expr.model_dump(mode="json")}
    assert currency_questions(doc, schema, columns, [receipt]) == []
    fm.expr = col("Currency")
    assert currency_questions(doc, schema, columns, [receipt])  # Changed choice must be rechecked.
    fm.expr = col("Invoice Currency")
    for c in columns[doc.record.root_source]:
        c["file_sha256"] = "changed-tail-outside-sample"
    assert currency_questions(doc, schema, columns, [receipt])


def test_numeric_looking_unknown_identity_keeps_raw_zeroes(tmp_path):
    schema, profile, doc, _, _ = fixture(
        tmp_path, ["Local reference", "Price"], [[f"{i:05}", i * 10] for i in range(1, 13)]
    )
    found = next(f for f in candidates(profile, schema, doc)["fields"] if f["target"] == "/sku")
    identity = next(c for c in found["candidates"] if c["column"] == "Local reference")
    assert identity["samples"][0] == "00001"


def test_currency_scopes_authority_and_defaults(tmp_path):
    schema, _, doc, columns, _ = fixture(tmp_path)
    cfg = schema.document["x-forma"]
    cfg["currency_sources"] = {
        "/currency": {"columns": ["Currency", "Invoice Currency"], "authoritative": "Currency"}
    }
    assert currency_questions(doc, schema, columns) == []
    fm = next(f for f in doc.fields if f.target == "/currency")
    fm.expr = col("Invoice Currency")
    assert currency_questions(doc, schema, columns)[0]["options"] == ["Currency"]
    fm.expr = col("Currency")
    cfg.pop("currency_sources")
    schema.document["properties"]["invoice_currency"] = {"type": "string"}
    doc.fields.append(FieldMapping(target="/invoice_currency", expr=col("Invoice Currency"), confidence=1))
    assert currency_questions(doc, schema, columns) == []  # Separate target meanings are legitimate.
    doc.fields.pop()
    fm = next(f for f in doc.fields if f.target == "/currency")
    fm.expr = const("THB")
    assert currency_questions(doc, schema, {})[0]["action"] == "constant"  # Model guess is not proof.
    schema.document["properties"]["currency"]["default"] = "THB"
    assert currency_questions(doc, schema, {}) == []
    assert currency_questions(doc, schema, columns)  # A default cannot hide competing evidence.


def test_source_aliases_and_changed_reading_context(tmp_path):
    schema, _, doc, columns, _ = fixture(tmp_path)
    source = doc.sources[0]
    source.rename = {"Currency": "unit_a", "Invoice Currency": "unit_b"}
    fm = next(f for f in doc.fields if f.target == "/currency")
    fm.expr = col("unit_a")
    (q,) = currency_questions(doc, schema, columns)
    assert q["options"] == ["unit_a", "unit_b"]
    receipt = {"evidence_key": q["evidence_key"], "expression": fm.expr.model_dump(mode="json")}
    assert currency_questions(doc, schema, columns, [receipt]) == []
    source.table.header_row = 2
    assert currency_questions(doc, schema, columns, [receipt])


def test_no_country_default_is_invented_without_contract(tmp_path):
    document = contract()
    document["properties"]["currency"]["x-forma"]["semantic"] = "currency"
    schema, _, doc, columns, _ = fixture(
        tmp_path, ["SKU", "Price"], [[f"A{i}", i] for i in range(1, 13)], document
    )
    fm = next(f for f in doc.fields if f.target == "/currency")
    assert fm.expr.op == "const" and fm.expr.value is None and fm.confidence == 0
    (q,) = currency_questions(doc, schema, columns)
    assert q["action"] == "constant"


def test_changed_collection_join_invalidates_a_confirmation(tmp_path):
    from forma.mapping_dsl.models import CollectionMapping, JoinKey

    schema, _, doc, columns, _ = fixture(tmp_path)
    schema.document["properties"]["entries"] = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "currency": schema.document["properties"].pop("currency"),
            },
        },
    }
    fm = next(f for f in doc.fields if f.target == "/currency")
    doc.fields.remove(fm)
    fm.target = "/entries/*/currency"
    doc.collections.append(
        CollectionMapping(
            target="/entries",
            source=doc.record.root_source,
            link=[JoinKey(left="SKU", right="SKU")],
            fields=[fm],
        )
    )
    (q,) = currency_questions(doc, schema, columns)
    receipt = {"evidence_key": q["evidence_key"], "expression": fm.expr.model_dump(mode="json")}
    assert currency_questions(doc, schema, columns, [receipt]) == []
    doc.collections[-1].link[0].right = "Price"
    assert currency_questions(doc, schema, columns, [receipt])


@pytest.mark.parametrize(
    "config",
    [
        [],
        {"/price": {"columns": ["Price"]}},
        {"/currency": {}},
        {"/currency": {"columns": ["A"], "authoritative": "B"}},
    ],
)
def test_invalid_scope_contract_fails_closed(config):
    document = contract()
    document["x-forma"]["currency_sources"] = config
    with pytest.raises(ValidationFailed):
        validate_currency_contract(TargetSchema("evidence", 1, document, None))


def test_unmapped_fields_are_grounded(tmp_path, schema):
    from tests.test_self_check import _bench

    bench, _ = _bench(tmp_path, schema)
    doc = propose_mapping(bench.profile, schema, customer_id="test").mapping
    doc.fields = []
    doc.collections = []
    answers = bench.ground(doc)
    assert any(a.get("source", {}).get("column") or a.get("request", {}).get("column") for a in answers), (
        answers
    )


@pytest.mark.parametrize("labels", [("Currency", "Invoice Currency"), ("X7", "Y9"), ("เงิน A", "เงิน B")])
def test_renamed_sources_use_contract_scope_and_values(tmp_path, labels):
    document = contract()
    document["properties"]["settlement"] = document["properties"].pop("currency")
    document["properties"]["settlement"]["description"] = "ISO 4217 currency code for the price"
    document["x-forma"]["currency_sources"] = {"/settlement": {"columns": list(labels)}}
    schema, _, doc, columns, _ = fixture(tmp_path, ["SKU", "Price", *labels], document=document)
    doc.fields = [f for f in doc.fields if f.target != "/settlement"]
    doc.fields.append(FieldMapping(target="/settlement", expr=const(None), confidence=1))
    (q,) = currency_questions(doc, schema, columns)
    assert q["kind"] == "choice" and q["options"] == list(labels)
    for c in columns[doc.record.root_source]:
        if c["name"] in labels:
            c["samples"] = ["JPY"]
    doc.fields[-1].expr = col(labels[0])
    assert currency_questions(doc, schema, columns) == []


def test_full_verifier_reports_business_doubt_separately_from_schema_success(tmp_path):
    from pathlib import Path
    from types import SimpleNamespace

    from forma.ai.workbench import FileWorkbench
    from forma.services.mapping import bind_sources

    schema, profile, doc, _, path = fixture(tmp_path)
    source = SimpleNamespace(
        id="file",
        original_name=path.name,
        storage_key=str(path),
        status="profiled",
        kind="csv",
        profile=profile.to_dict(),
        fingerprint=None,
    )
    bench = FileWorkbench(
        schema, profile, [source], bind=lambda d: bind_sources(d, [source], resolve=Path), resolve=Path
    )
    result = bench.verify(doc)
    assert result.rows_out == 12 and result.errors == 0
    assert result.decision_questions[0]["target"] == "/currency"
    assert result.to_prompt()["decision_questions"]


def test_mock_mapper_can_inspect_and_fill_a_completely_unmapped_field(tmp_path):
    import json
    from pathlib import Path
    from types import SimpleNamespace

    from forma.ai.agents import mapping_agent
    from forma.ai.llm import LLMClient, MockProvider
    from forma.ai.workbench import FileWorkbench
    from forma.mapping_dsl.models import call
    from forma.services.mapping import bind_sources

    schema, profile, _, _, path = fixture(
        tmp_path, ["SKU", "Montant", "other number"], [[f"A{i}", i * 10, i] for i in range(1, 13)]
    )
    draft = propose_mapping(profile, schema, customer_id="test")
    draft.mapping.fields = [f for f in draft.mapping.fields if f.target != "/price"]
    source = SimpleNamespace(
        id="file",
        original_name=path.name,
        storage_key=str(path),
        status="profiled",
        kind="csv",
        profile=profile.to_dict(),
        fingerprint=None,
    )
    bench = FileWorkbench(
        schema, profile, [source], bind=lambda d: bind_sources(d, [source], resolve=Path), resolve=Path
    )
    calls = []

    def respond(system, user, response_schema):
        payload = json.JSONDecoder().raw_decode(user)[0]
        calls.append(payload)
        field = next(f for f in payload["source_candidates"]["fields"] if f["target"] == "/price")
        assert not field["mapped"]
        assert "Montant" in {c["column"] for c in field["candidates"]}
        if len(calls) == 1:
            return {"mapping": None, "inspect": [{"table_index": 0, "what": "distinct", "column": "Montant"}]}
        answer = draft.mapping.model_copy(deep=True)
        answer.fields.append(
            FieldMapping(
                target="/price",
                expr=call("parse_number_th", col("Montant")),
                confidence=0.8,
                evidence=["Mocked reviewer decision after inspection, not live reasoning"],
            )
        )
        return {"mapping": answer.model_dump(mode="json"), "open_questions": []}

    mapped, result = mapping_agent(
        LLMClient(MockProvider(respond), model="offline"), profile, schema, draft, workbench=bench
    )
    assert len(calls) >= 2 and not any(n.startswith("llm_failed") for n in result.notes)
    assert bench.verify(mapped).rows_out == 12
    assert any(f.target == "/price" for f in mapped.fields)


def test_api_currency_answer_preserves_rows_and_cannot_bypass_review(isolated_env):
    from forma.api.app import create_app

    _, _, _, _, path = fixture(isolated_env)
    with TestClient(create_app()) as client:
        reg = client.post("/api/schemas", json={"document": contract()})
        assert reg.status_code == 201, reg.text
        customer = client.post("/api/customers", json={"code": "EVIDENCE", "name": "Evidence"}).json()
        result = client.post(
            "/api/rounds", json={"customer_id": customer["id"], "target_schema": "evidence@1"}
        )
        assert result.status_code == 201, result.text
        rid = result.json()["id"]
        _upload(client, rid, path, path.name)
        _run(client, rid)
        mapping = client.get(f"/api/rounds/{rid}").json()["mappings"][0]
        mid = mapping["id"]
        qs = client.get(f"/api/mappings/{mid}/questions").json()["questions"]
        found = [q for q in qs if q["field"]["pointer"] == "/currency"]
        assert len(found) == 1 and found[0]["kind"] == "choice", qs
        assert "THB" in found[0]["reason_th"] and "USD" in found[0]["reason_th"]
        blocked = client.post(
            f"/api/mappings/{mid}/approve", json={"override": True, "note": "not a substitute for an answer"}
        )
        assert blocked.status_code == 422, blocked.text
        payload = {"answers": {found[0]["id"]: "Invoice Currency"}}
        preview = client.post(f"/api/mappings/{mid}/answers/preview", json=payload)
        assert preview.status_code == 200, preview.text
        records = preview.json()["preview"]["records"]
        assert len(records) == 12
        assert [r["currency"] for r in records] == ["USD" if i % 2 else "EUR" for i in range(1, 13)]
        saved = client.post(
            f"/api/mappings/{mid}/answers/apply", json=payload | {"digest": preview.json()["digest"]}
        )
        assert saved.status_code == 201, saved.text
        new = saved.json()
        assert new["status"] == "proposed"
        assert not new["policy"]["review_decisions"]
        assert not [
            q
            for q in client.get(f"/api/mappings/{new['id']}/questions").json()["questions"]
            if q["field"]["pointer"] == "/currency"
        ]
        # Explicitly editing a different source creates a new question, even at confidence 1.
        edited = copy.deepcopy(new["document"])
        next(f for f in edited["fields"] if f["target"] == "/currency")["expr"] = {
            "op": "col",
            "name": "Currency",
        }
        changed = client.post(f"/api/mappings/{new['id']}/edit", json={"document": edited})
        assert changed.status_code == 201, changed.text
        assert changed.json()["policy"]["review_decisions"]


def test_missing_required_currency_can_be_answered_without_inventing_a_default(isolated_env):
    from forma.api.app import create_app

    document = contract()
    document["required"].append("currency")
    document["properties"]["currency"] = {
        "type": "string",
        "x-forma": {"semantic": "currency", "risk": "critical"},
    }
    _, _, _, _, path = fixture(
        isolated_env, ["SKU", "Price"], [[f"A{i}", i * 10] for i in range(1, 13)], document
    )
    with TestClient(create_app()) as client:
        reg = client.post("/api/schemas", json={"document": document})
        assert reg.status_code == 201, reg.text
        customer = client.post("/api/customers", json={"code": "NO-CCY", "name": "Missing currency"}).json()
        rnd = client.post(
            "/api/rounds", json={"customer_id": customer["id"], "target_schema": reg.json()["ref"]}
        ).json()
        _upload(client, rnd["id"], path, path.name)
        _run(client, rnd["id"])
        mid = client.get(f"/api/rounds/{rnd['id']}").json()["mappings"][0]["id"]
        questions = client.get(f"/api/mappings/{mid}/questions").json()["questions"]
        (q,) = [q for q in questions if q["field"]["pointer"] == "/currency"]
        assert q["action"] == "constant"
        payload = {"answers": {q["id"]: "USD"}}
        preview = client.post(f"/api/mappings/{mid}/answers/preview", json=payload)
        assert preview.status_code == 200, preview.text
        assert len(preview.json()["preview"]["records"]) == 12
        saved = client.post(
            f"/api/mappings/{mid}/answers/apply", json=payload | {"digest": preview.json()["digest"]}
        )
        assert saved.status_code == 201, saved.text
        assert not saved.json()["policy"]["review_decisions"]
