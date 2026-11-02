from __future__ import annotations

import csv
from pathlib import Path

import pytest

from forma.core.errors import ValidationFailed
from forma.engine.transform import BoundSource, TransformEngine
from forma.mapping_dsl.models import (
    CollectionMapping,
    FieldMapping,
    JoinKey,
    MappingDocument,
    MappingMeta,
    RecordSpec,
    SourceSpec,
    call,
    col,
    const,
)
from forma.schema_registry import TargetSchema
from forma.services import decision_actions as actions


def document() -> MappingDocument:
    return MappingDocument(
        meta=MappingMeta(target_schema="product@1"),
        sources=[SourceSpec(id="src", role="products")],
        record=RecordSpec(root_source="src"),
        fields=[
            FieldMapping(target="/sku", expr=col("sku")),
            FieldMapping(target="/product_name", expr=col("name")),
            FieldMapping(target="/currency", expr=const("THB")),
            FieldMapping(target="/unit/sell_unit", expr=call("unit_normalize", col("unit"))),
        ],
        collections=[
            CollectionMapping(
                target="/prices",
                source="src",
                link=[JoinKey(left="sku", right="sku")],
                fields=[
                    FieldMapping(target="/prices/*/price_list", expr=const("standard")),
                    FieldMapping(target="/prices/*/amount", expr=call("parse_number_th", col("price"))),
                    FieldMapping(target="/prices/*/effective_from", expr=call("parse_date_th", col("date"))),
                    FieldMapping(target="/prices/*/vat_included", expr=const(False)),
                ],
            )
        ],
    )


def run(tmp_path: Path, schema: TargetSchema, doc: MappingDocument, rows: list[list[object]]):
    path = tmp_path / "source.csv"
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["sku", "name", "unit", "price", "date"])
        writer.writerows(rows)
    return TransformEngine(schema).run(doc, {"src": BoundSource(path)})


def test_date_answer_changes_parser_not_every_date(tmp_path, schema):
    doc = document()
    q = actions.normalize(
        {"target": "/prices/*/effective_from", "kind": "enum", "options": ["day first", "month first"]},
        schema,
    )
    q["field"] = schema.field(q["target"]).to_dict()
    actions.apply(doc, q, "MDY")
    result = run(
        tmp_path,
        schema,
        doc,
        [["0001", "A", "PCS", 10, "03/04/2026"], ["0002", "B", "PCS", 20, "04/05/2026"]],
    )
    assert [r["prices"][0]["effective_from"] for r in result.records] == ["2026-03-04", "2026-04-05"]
    assert not result.errors
    with pytest.raises(ValidationFailed):
        actions.apply(doc, q, "day first")


def test_currency_contract_accepts_sgd_not_only_model_suggestions(schema):
    q = actions.normalize({"target": "/currency", "kind": "enum", "options": ["THB", "USD"]}, schema)
    assert q["allow_custom"] and q["kind"] == "text"
    q["field"] = schema.field(q["target"]).to_dict()
    doc = document()
    actions.apply(doc, q, "SGD")
    assert actions.field_mapping(doc, "/currency").expr.value == "SGD"
    with pytest.raises(ValidationFailed):
        actions.apply(doc, q, 123)


def test_currency_symbol_question_on_constant_candidate_requires_explicit_file_answer(schema):
    from types import SimpleNamespace

    from forma.db.models import MappingVersion
    from forma.services.review_questions import answer_document, questions

    q = {
        "target": "/currency",
        "kind": "enum",
        "action": "value_map",
        "source_value": "$",
        "question": "Which currency does the symbol in these prices mean?",
        "why": "No currency column",
        "options": ["USD", "THB"],
    }
    m = MappingVersion(
        id="currency-symbol",
        status="proposed",
        target_schema=schema.ref,
        document=document().model_dump(mode="json"),
        policy={"clarifications": [q]},
        open_questions=[q["question"]],
    )
    ctx = SimpleNamespace(registry=SimpleNamespace(get_ref=lambda _: schema))
    card = next(q for q in questions(ctx, m)["questions"] if q["field"]["pointer"] == "/currency")
    assert card["action"] == "constant" and card["scope"] == "constant_for_this_file"
    assert card["allow_custom"]
    doc, remaining, _ = answer_document(ctx, m, {card["id"]: "SGD"}, "reviewer")
    assert next(f for f in doc["fields"] if f["target"] == "/currency")["expr"] == {
        "op": "const",
        "value": "SGD",
    }
    assert remaining == []


def test_unit_definition_changes_only_selected_token(tmp_path, schema):
    doc = document()
    q = {
        "target": "/unit/sell_unit",
        "field": schema.field("/unit/sell_unit").to_dict(),
        "action": "value_map",
        "source_value": "BT",
    }
    actions.apply(doc, q, "BOTTLE")
    result = run(
        tmp_path, schema, doc, [["0001", "A", "BT", 10, "2026-04-03"], ["0002", "B", "PCS", 20, "2026-04-03"]]
    )
    assert [r["unit"]["sell_unit"] for r in result.records] == ["BOTTLE", "PCS"]


def test_embedded_currency_symbol_changes_only_matching_rows_not_price_amounts(tmp_path, schema):
    doc = document()
    currency = actions.field_mapping(doc, "/currency")
    currency.expr = call(
        "conditional", call("contains", col("price"), pattern="$", literal=True), const("USD"), const("THB")
    )
    q = {
        "target": "/currency",
        "field": schema.field("/currency").to_dict(),
        "action": "value_map",
        "source_value": "$",
    }
    actions.apply(doc, q, "SGD")
    result = run(
        tmp_path,
        schema,
        doc,
        [["0001", "A", "PCS", "$10", "2026-04-03"], ["0002", "B", "PCS", "20", "2026-04-03"]],
    )
    assert [r["currency"] for r in result.records] == ["SGD", "THB"]
    assert [r["prices"][0]["amount"] for r in result.records] == [10, 20]
    assert not result.errors


def test_remembered_unit_rule_still_asks_about_a_new_unknown_token(schema):
    from forma.services.review_questions import unit_questions

    doc = document()
    q = {
        "target": "/unit/sell_unit",
        "field": schema.field("/unit/sell_unit").to_dict(),
        "action": "value_map",
        "source_value": "BT",
    }
    actions.apply(doc, q, "BOTTLE")
    qs = unit_questions(
        doc, schema, {"src": [{"name": "unit", "top_values": [["BT", 8], ["PCS", 2], ["XYZ", 2]]}]}
    )
    assert [q["source_value"] for q in qs] == ["XYZ"]


def test_conflicting_price_preserved_then_only_selected_source_row_removed(tmp_path, schema):
    doc = document()
    rows = [
        ["0001", "A", "PCS", 10, "2026-04-03"],
        ["0002", "B", "PCS", 20, "2026-04-03"],
        ["0001", "A", "PCS", 9999, "2026-04-03"],
    ]
    before = run(tmp_path, schema, doc, rows)
    assert len(before.records) == 2
    assert len(before.records[0]["prices"]) == 2
    assert any("conflicting values" in e.message for e in before.errors)
    qs = actions.conflicting_items(doc, schema, before.records)
    assert len(qs) == 1
    q = qs[0]
    q["field"] = schema.field(q["target"]).to_dict()
    actions.apply(doc, q, "0")
    after = run(tmp_path, schema, doc, rows)
    assert not after.errors
    assert [r["prices"][0]["amount"] for r in after.records] == [10, 20]
    assert actions.conflicting_items(doc, schema, after.records) == []


def test_model_and_server_unit_question_share_one_answer_without_losing_other_tokens(schema):
    from types import SimpleNamespace

    from forma.db.models import MappingVersion
    from forma.services.review_questions import answer_document, questions, unit_questions

    doc = document()
    server = unit_questions(doc, schema, {"src": [{"name": "unit", "top_values": [["BT", 2]]}]})
    clarifications = [
        {
            "target": "/unit/sell_unit",
            "kind": "text",
            "action": "value_map",
            "source_value": token,
            "question": f"What is {token}?",
            "why": "Not defined",
        }
        for token in ("BT", "XYZ")
    ]
    m = MappingVersion(
        id="dedup",
        status="proposed",
        target_schema=schema.ref,
        document=doc.model_dump(mode="json"),
        policy={"clarifications": clarifications, "review_decisions": server},
        open_questions=[q["question"] for q in [*clarifications, *server]],
    )
    ctx = SimpleNamespace(registry=SimpleNamespace(get_ref=lambda _: schema))
    qs = [q for q in questions(ctx, m)["questions"] if q.get("action") == "value_map"]
    assert len(qs) == 2 and {q["source_value"] for q in qs} == {"BT", "XYZ"}
    bt = next(q for q in qs if q["source_value"] == "BT")
    _, remaining, _ = answer_document(ctx, m, {bt["id"]: "BOTTLE"}, "reviewer")
    assert remaining == ["What is XYZ?"]
