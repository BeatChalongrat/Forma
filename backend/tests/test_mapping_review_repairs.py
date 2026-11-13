"""Regression cases for schema paths, composite identities and scoped human decisions."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from forma.ai import clarifications, plausibility
from forma.core.errors import ValidationFailed
from forma.db.models import MappingVersion
from forma.engine.transform import BoundSource, TransformEngine
from forma.mapping_dsl.models import MappingDocument, call, col, const
from forma.mapping_dsl.validate import validate_mapping
from forma.schema_registry import TargetSchema
from forma.services import decision_actions as actions
from forma.services.review_evidence import _validate_answer_results
from forma.services.review_questions import answer_document, questions


def contract():
    return TargetSchema(
        "repair",
        1,
        {
            "type": "object",
            "required": ["code", "amount"],
            "properties": {
                "code": {"type": "string"},
                "market": {"type": "string"},
                "amount": {"type": "number"},
                "tax": {"type": "boolean"},
            },
            "x-forma": {"identity_keys": ["/code"]},
        },
    )


def document(schema):
    return MappingDocument.model_validate(
        {
            "meta": {"target_schema": schema.ref},
            "sources": [{"id": "s"}],
            "record": {"root_source": "s", "dedupe": {"keep": "review"}},
            "fields": [
                {"target": "/code", "expr": {"op": "col", "name": "Code"}},
                {"target": "/amount", "expr": call("parse_number_th", col("Amount")).model_dump(mode="json")},
            ],
            "attributes_passthrough": False,
        }
    )


def execute(tmp_path, schema, doc, content):
    p = tmp_path / "input.csv"
    p.write_text(content, encoding="utf-8")
    return TransformEngine(schema).run(doc, {"s": BoundSource(p)})


def test_composite_identity_not_each_component_must_be_diverse():
    schema = contract()
    schema.document["x-forma"]["identity_keys"] = ["/code", "/market"]
    rows = [{"code": f"{i:04}", "market": m, "amount": 4} for i in range(12) for m in ["X", "Y"]]
    assert not plausibility.check(rows, schema)
    broken = [{"code": "0001", "market": "X", "amount": 4} for _ in range(24)]
    assert any("distinct" in p for p in plausibility.check(broken, schema))


def test_collection_local_paths_execute_full_aggregation(tmp_path):
    schema = TargetSchema(
        "nested",
        1,
        {
            "type": "object",
            "required": ["code", "balances"],
            "properties": {
                "code": {"type": "string"},
                "balances": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["depot", "available"],
                        "properties": {"depot": {"type": "string"}, "available": {"type": "number"}},
                    },
                },
            },
            "x-forma": {"identity_keys": ["/code"]},
        },
    )
    raw = {
        "meta": {"target_schema": schema.ref},
        "sources": [{"id": "s"}],
        "record": {"root_source": "s"},
        "fields": [{"target": "/code", "expr": {"op": "col", "name": "Code"}}],
        "collections": [
            {
                "target": "/balances",
                "source": "s",
                "link": [{"left": "Code", "right": "Code"}],
                "fields": [
                    {"target": "/depot", "expr": {"op": "col", "name": "Depot"}},
                    {
                        "target": "/available",
                        "expr": call(
                            "subtract",
                            call("parse_number_th", col("Qty")),
                            call("parse_number_th", col("Reserved")),
                        ).model_dump(mode="json"),
                    },
                ],
                "aggregate": {"group_by": ["/depot"], "sum": ["/available"]},
            }
        ],
        "attributes_passthrough": False,
    }
    unchanged = deepcopy(raw)
    doc = MappingDocument.model_validate(raw)
    assert raw == unchanged
    assert validate_mapping(doc, schema).ok
    out = execute(
        tmp_path, schema, doc, "Code,Depot,Qty,Reserved,Lot\nA,N,10,2,L1\nA,N,-2,0,L2\nA,S,7,1,L3\n"
    )
    assert not out.errors
    assert out.records[0]["balances"] == [{"depot": "N", "available": 6.0}, {"depot": "S", "available": 6.0}]
    assert doc.collections[0].fields[0].target == "/balances/*/depot"
    raw["collections"][0]["fields"][0]["target"] = "/invented"
    assert not validate_mapping(MappingDocument.model_validate(raw), schema).ok


def test_unexported_labels_do_not_hide_output_or_link_conflicts(tmp_path):
    from forma.mapping_dsl.models import CollectionMapping, FieldMapping, JoinKey

    schema = contract()
    doc = document(schema)
    # An intentionally discarded source label is not a conflicting output field.
    same = execute(tmp_path, schema, doc, "Code,Amount,Lot\nA,5,L1\nA,5,L2\n")
    assert not same.errors
    # Requested passthrough and genuinely conflicting output remain errors.
    doc.attributes_passthrough = True
    assert execute(tmp_path, schema, doc, "Code,Amount,Lot\nA,5,L1\nA,5,L2\n").errors
    doc.attributes_passthrough = False
    assert execute(tmp_path, schema, doc, "Code,Amount,Lot\nA,5,L1\nA,6,L2\n").errors
    schema.document["properties"]["children"] = {
        "type": "array",
        "items": {"type": "object", "properties": {"value": {"type": "number"}}},
    }
    doc.collections = [
        CollectionMapping(
            target="/children",
            source="s",
            link=[JoinKey(left="Lot", right="Lot")],
            fields=[FieldMapping(target="/children/*/value", expr=call("parse_number_th", col("Amount")))],
        )
    ]
    assert execute(tmp_path, schema, doc, "Code,Amount,Lot\nA,5,L1\nA,5,L2\n").errors


def test_boolean_confirm_contract_rejected_but_old_null_card_repaired():
    schema = contract()
    doc = document(schema)
    from forma.mapping_dsl.models import FieldMapping

    doc.fields.append(FieldMapping(target="/tax", expr=const(None)))
    q = {
        "target": "/tax",
        "kind": "boolean",
        "action": "confirm",
        "question": "Does price include tax?",
        "why": "Policy absent",
    }
    assert clarifications.validate([q], doc, schema)
    ctx = SimpleNamespace(registry=SimpleNamespace(get_ref=lambda _: schema))
    m = MappingVersion(
        id="old",
        target_schema=schema.ref,
        status="proposed",
        document=doc.model_dump(mode="json"),
        policy={"clarifications": [q]},
        open_questions=[q["question"]],
    )
    card = next(q for q in questions(ctx, m)["questions"] if q["field"]["pointer"] == "/tax")
    assert (card["kind"], card["action"]) == ("boolean", "constant")
    saved, remaining, _ = answer_document(ctx, m, {"/tax": False}, "reviewer")
    assert next(f for f in saved["fields"] if f["target"] == "/tax")["expr"]["value"] is False
    assert not remaining


def test_price_set_one_question_changes_only_conflicting_rows(tmp_path):
    schema = contract()
    doc = document(schema)
    csv = "Code,Amount,Tariff\nA,4,Retail\nA,7,Trade\nB,8,Trade\nB,6,Retail\nC,9,Retail\n"
    before = execute(tmp_path, schema, doc, csv)
    qs = actions.conflicting_records(before.findings)
    assert len(qs) == 1 and set(qs[0]["options"]) == {"Retail", "Trade"}
    q = {**qs[0], "field": schema.field("/amount").to_dict()}
    with pytest.raises(ValidationFailed):
        actions.apply(doc, q, "Other")
    actions.apply(doc, q, "Trade")
    after = execute(tmp_path, schema, doc, csv)
    assert {r["code"]: r["amount"] for r in after.records} == {"A": 7, "B": 8, "C": 9}
    assert _validate_answer_results(schema, before, after, [q])["passed"]


def test_no_group_for_partial_or_ambiguous_sets():
    choices = [
        {
            "source": "s",
            "target": "/amount",
            "identity": {"/code": key},
            "rows": [
                {"row": i * 2, "values": {"/amount": 4}, "context": {"List": "A", "Variant": "X"}},
                {"row": i * 2 + 1, "values": {"/amount": 8}, "context": {"List": "B", "Variant": "Y"}},
            ],
        }
        for i, key in enumerate(["a", "b"])
    ]
    f = SimpleNamespace(details={"source_choices": choices, "conflicting": 2})
    assert actions.grouped_records(f) is None
    for c in choices:
        for r in c["rows"]:
            del r["context"]["Variant"]
    grouped = actions.grouped_records(f)
    reordered = SimpleNamespace(
        details={
            "source_choices": [{**c, "rows": list(reversed(c["rows"]))} for c in reversed(choices)],
            "conflicting": 2,
        }
    )
    assert grouped == actions.grouped_records(reordered)
    assert choices[0]["rows"][0]["row"] == 0  # normalization does not mutate engine findings
    f.details["conflicting"] = 3
    assert actions.grouped_records(f) is None


def test_full_workbench_exposes_same_executable_conflict_question_as_review(tmp_path):
    from forma.ai.workbench import FileWorkbench

    schema = contract()
    doc = document(schema)
    csv = "Code,Amount,Tariff\nA,4,Retail\nA,7,Trade\nB,8,Trade\nB,6,Retail\n"
    result = execute(tmp_path, schema, doc, csv)
    bench = FileWorkbench(
        schema,
        None,
        [],
        bind=lambda _: ({"s": BoundSource(tmp_path / "input.csv")}, []),
        resolve=lambda _: tmp_path / "input.csv",
    )
    report = bench.verify(doc)
    assert report.complete and report.errors
    assert report.to_prompt()["decision_questions"] == actions.conflicting_records(result.findings)
    assert report.decision_questions[0]["options"] == ["Retail", "Trade"]
    assert not bench.run(doc).decision_questions


def test_missing_value_only_fills_named_record_and_cannot_be_remembered(tmp_path):
    schema = contract()
    doc = document(schema)
    csv = "Code,Amount\nA,5\nB,\nC,0\n"
    before = execute(tmp_path, schema, doc, csv)
    qs = actions.missing_record_values(doc, schema, before.records)
    assert len(qs) == 1 and qs[0]["identity"] == {"/code": "B"}
    q = {**qs[0], "field": schema.field("/amount").to_dict()}
    actions.apply(doc, q, 17.25)
    after = execute(tmp_path, schema, doc, csv)
    assert [r["amount"] for r in after.records] == [5, 17.25, 0]
    assert _validate_answer_results(schema, before, after, [q])["passed"]
    from forma.services.decision_memory import reusable

    assert not reusable({**q, "remember": True})
    after.records[0]["amount"] = 99
    with pytest.raises(ValidationFailed):
        _validate_answer_results(schema, before, after, [q])
    assert actions.missing_record_values(document(schema), schema, [{"code": "A", "amount": None}]) == []


@pytest.mark.parametrize("mode", ["missing", "group"])
def test_scoped_decision_api_preview_save_approve_download(isolated_env, mode):
    from fastapi.testclient import TestClient

    from forma.api.app import create_app
    from forma.db.session import session_scope

    schema = contract()
    schema.document["x-forma"].update(schema_id=schema.schema_id, version=1)
    doc = document(schema)
    doc.sources[0].file.name_pattern = r"^input\.csv$"
    doc.sources[0].table.header_row = 0
    content = (
        "Code,Amount\nA,5\nB,\nC,0\n"
        if mode == "missing"
        else "Code,Amount,Tariff\nA,4,Retail\nA,7,Trade\nB,8,Trade\nB,6,Retail\nC,9,Retail\n"
    )
    with TestClient(create_app()) as c:
        assert c.post("/api/schemas", json={"document": schema.document}).status_code == 201
        cust = c.post("/api/customers", json={"code": "REPAIR", "name": "Review fixture"}).json()
        r = c.post("/api/rounds", json={"customer_id": cust["id"], "target_schema": schema.ref}).json()
        uploaded = c.post(
            f"/api/rounds/{r['id']}/files", files=[("files", ("input.csv", content.encode(), "text/csv"))]
        )
        assert uploaded.status_code == 201, uploaded.text
        profiled = c.post(f"/api/rounds/{r['id']}/profile", params={"sync": True})
        assert profiled.status_code == 200, profiled.text
        with session_scope() as s:
            s.add(
                MappingVersion(
                    id="repair-seed",
                    customer_id=cust["id"],
                    round_id=r["id"],
                    version=1,
                    status="proposed",
                    target_schema=schema.ref,
                    document=doc.model_dump(mode="json"),
                    confidence=1,
                    policy={},
                    open_questions=[],
                )
            )
        edited = c.post("/api/mappings/repair-seed/edit", json={"document": doc.model_dump(mode="json")})
        assert edited.status_code == 201, edited.text
        mid = edited.json()["id"]
        catalogue = c.get(f"/api/mappings/{mid}/questions").json()
        actionable = [q for q in catalogue["questions"] if q.get("action") in ("record_value", "select_rows")]
        assert len(actionable) == 1, (catalogue, edited.json()["policy"])
        q = actionable[0]
        payload = {"answers": {q["id"]: 17.25 if mode == "missing" else "Trade"}}
        preview = c.post(f"/api/mappings/{mid}/answers/preview", json=payload)
        assert preview.status_code == 200, preview.text
        assert preview.json()["comparison"]["validation"]["passed"]
        saved = c.post(
            f"/api/mappings/{mid}/answers/apply", json={**payload, "digest": preview.json()["digest"]}
        )
        assert saved.status_code == 201, saved.text
        latest = saved.json()["id"]
        pending = c.get(f"/api/mappings/{latest}/questions").json()
        assert not any(q.get("action") in ("record_value", "select_rows") for q in pending["questions"])
        approved = c.post(f"/api/mappings/{latest}/approve", json={})
        assert approved.status_code == 200, approved.text
        job = c.post(f"/api/rounds/{r['id']}/transform", params={"sync": True}).json()
        assert c.get(f"/api/jobs/{job['job_id']}").json()["status"] == "done"
        delivery = c.get(f"/api/rounds/{r['id']}").json()
        did = delivery["datasets"][-1]["id"]
        assert c.post(f"/api/datasets/{did}/approve", json={"acknowledge_anomalies": True}).status_code == 200
        exported = c.post(f"/api/datasets/{did}/export?fmt=json")
        assert exported.status_code == 201, exported.text
        rows = c.get(f"/api/exports/{exported.json()['id']}/download").json()
        expected = {"A": 5, "B": 17.25, "C": 0} if mode == "missing" else {"A": 7, "B": 8, "C": 9}
        assert {row["code"]: row["amount"] for row in rows} == expected


def test_retire_explicit_identity_discovery_note_without_sku_semantic():
    from forma.services.mapping import _retire_structural_questions

    schema = contract()
    doc = document(schema)
    note = "No SKU-like header found in root table; using 'Amount' as identity — confirm"
    business = "Does price include VAT?"
    remaining, retired = _retire_structural_questions(
        [note, business, business],
        doc,
        schema,
        [{"code": "A", "amount": 12}, {"code": "B", "amount": 13}],
    )
    assert remaining == [business] and retired == [note]
    remaining, _ = _retire_structural_questions(
        [note],
        doc,
        schema,
        [{"code": "A"}, {"code": "A"}],
    )
    assert remaining == [note]


def test_question_presentation_short_labels_and_business_meaning():
    from forma.services.question_presentation import presentation

    q = {
        "field": {"pointer": "/currency", "semantic": "currency"},
        "action": "constant",
        "kind": "text",
        "question": "What currency?",
    }
    assert presentation(q)["question_th"] == "ราคาในไฟล์ใช้สกุลเงินอะไร?"
    q = {
        "field": {"pointer": "/x", "name": "หน่วยขาย", "description": "Long technical description"},
        "action": "value_map",
        "kind": "text",
        "source_value": "BT",
    }
    assert "Long technical" not in presentation(q)["question_th"]
    assert "BT" in presentation(q)["question_th"]


@pytest.mark.parametrize("as_of, expected", [("2026-01-01", 11), ("2026-12-31", 11)])
def test_effective_price_filter_before_dedupe_keeps_boundary_and_excludes_other_lists(
    tmp_path, as_of, expected
):
    schema = contract()
    doc = document(schema)
    doc.record.filters = [
        call("eq", col("List"), const("RETAIL")),
        call("lte", call("parse_date_th", col("Start")), call("parse_date_th", const(as_of))),
        call("gte", call("parse_date_th", col("End")), call("parse_date_th", const(as_of))),
    ]
    content = (
        "Code,Amount,List,Start,End\n"
        "001,999,WHOLESALE,2026-01-01,2026-12-31\n"
        "001,2,RETAIL,2025-01-01,2025-12-31\n"
        "001,11,RETAIL,2026-01-01,2026-12-31\n"
        "001,22,RETAIL,2027-01-01,2027-12-31\n"
    )
    result = execute(tmp_path, schema, doc, content)
    assert len(result.records) == 1
    assert result.records[0]["amount"] == expected
    assert result.records[0]["code"] == "001"
