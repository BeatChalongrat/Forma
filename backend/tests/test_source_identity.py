"""Source-grain checks must work without ERP/header vocabulary or a paid model."""

from __future__ import annotations

import json

import polars as pl
import pytest

from forma.core.errors import ValidationFailed
from forma.engine.transform import ROW_COL, TransformEngine
from forma.mapping_dsl.models import (
    CollectionAggregate,
    CollectionMapping,
    FieldMapping,
    JoinKey,
    SourceIdentity,
    col,
)
from forma.services.decision_actions import apply, source_identity_questions


def setup_collection(rows, *, keys=("depot", "batch"), aggregate=True):
    cm = CollectionMapping(
        target="/inventory",
        source="s",
        link=[JoinKey(left="sku", right="sku")],
        fields=[
            FieldMapping(target="/inventory/*/warehouse_code", expr=col("depot")),
            FieldMapping(target="/inventory/*/qty", expr=col("amount")),
        ],
        aggregate=CollectionAggregate(group_by=["/warehouse_code"], sum=["/qty"]) if aggregate else None,
        source_identity=SourceIdentity(
            keys=list(keys), evidence=["ERP contract: one balance per item/depot/batch, optionally bin"]
        )
        if keys
        else None,
    )
    frame = pl.DataFrame(rows).rename({k: "s." + k for k in rows[0]})
    frame = frame.with_columns(pl.Series("s." + ROW_COL, range(len(rows))))
    return cm, frame, pl.DataFrame({"r.sku": list(dict.fromkeys(row["sku"] for row in rows))})


def row(batch="L1", amount=7, depot="A", **extra):
    return {"sku": "001", "depot": depot, "batch": batch, "amount": amount, **extra}


def run(schema, rows, **kw):
    cm, frame, root = setup_collection(rows, **kw)
    out, findings = TransformEngine(schema)._collection(frame, cm, root, "r")
    return cm, out, findings


def test_remove_real_repeat_keep_equal_lots_and_other_warehouses(schema):
    _, out, findings = run(schema, [row(), row(), row("L2"), row("L1", 3, "B")])
    assert dict(zip(out["warehouse_code"], out["qty"], strict=True)) == {"A": 14, "B": 3}
    assert not [f for f in findings if f.severity == "error"]
    receipt = next(f.details for f in findings if f.severity == "info")
    assert (receipt["input_rows"], receipt["retained_rows"], receipt["dropped_rows"]) == (4, 3, 1)
    assert receipt["dropped_row_sample"] == [1]
    assert out["__src_row__"].to_list() == [[0, 2], [3]]


@pytest.mark.parametrize(
    "keys,rows,reason",
    [
        (None, [row(), row("L2")], "missing_key"),
        (("absent",), [row()], "missing_columns"),
        (("depot", "batch"), [row(batch=None)], "blank_key"),
        (("depot", "batch"), [row(batch="  ")], "blank_key"),
        (("depot", "batch", "amount"), [row(), row(amount=9)], "incomplete_key"),
        (("depot", "batch"), [row(bin="A"), row(bin="B")], "incomplete_key"),
    ],
)
def test_uncertainty_blocks_and_asks_without_guessing(schema, keys, rows, reason):
    _, out, findings = run(schema, rows, keys=keys)
    assert out["qty"].to_list() == [None]
    questions = source_identity_questions(findings)
    assert questions and any(f.details.get("reason") == reason for f in findings)
    assert all(q["action"] in ("source_identity", "request_source") for q in questions)


def test_same_lot_two_bins_need_full_business_key(schema):
    _, out, findings = run(schema, [row(bin="a"), row(bin="b")], keys=("depot", "batch", "bin"))
    assert out["qty"].to_list() == [14] and not findings


def test_source_conflict_selects_all_duplicate_copies_of_rejected_value(schema):
    from tests.test_decision_actions import document

    rows = [row(), row(), row(amount=9), row("L2", 2)]
    cm, _, findings = run(schema, rows)
    (q,) = source_identity_questions(findings)
    assert q["action"] == "select_rows" and q["row_exclusions"]["2"] == [0, 1]
    doc = document()
    doc.collections = [cm]
    apply(doc, q, "2")
    _, frame, root = setup_collection(rows)
    out, findings = TransformEngine(schema)._collection(frame, cm, root, "r")
    assert out["qty"].to_list() == [11] and not findings


def test_silent_projection_loss_does_not_pass_with_or_without_key(schema):
    for keys in (None, ("depot", "batch")):
        _, _, findings = run(schema, [row(), row("L2")], keys=keys, aggregate=False)
        assert source_identity_questions(findings)
        assert any(f.severity == "error" for f in findings)


def test_renamed_columns_and_output_contract(schema):
    from forma.schema_registry import TargetSchema

    cm, frame, root = setup_collection([row(), row(), row("L2")])
    renamed = {"sku": "商品", "depot": "Zone-X", "batch": "Reference-42", "amount": "Balance-N"}
    frame = frame.rename({"s." + k: "s." + v for k, v in renamed.items()})
    cm.link[0].right = renamed["sku"]
    cm.target = "/positions"
    cm.fields[0].target = "/positions/*/location"
    cm.fields[0].expr = col(renamed["depot"])
    cm.fields[1].target = "/positions/*/balance"
    cm.fields[1].expr = col(renamed["amount"])
    cm.aggregate = CollectionAggregate(group_by=["/location"], sum=["/balance"])
    cm.source_identity.keys = [renamed["depot"], renamed["batch"]]
    custom = TargetSchema(
        "custom",
        1,
        {
            "type": "object",
            "properties": {
                "positions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"location": {"type": "string"}, "balance": {"type": "number"}},
                    },
                }
            },
        },
    )
    out, findings = TransformEngine(custom)._collection(frame, cm, root, "r")
    assert out["balance"].to_list() == [14] and not any(f.severity == "error" for f in findings)


def test_conflict_beyond_preview_and_bounded_questions(schema):
    rows = [row(str(i), 1) for i in range(1000)] + [row("999", 2)]
    _, out, findings = run(schema, rows)
    assert out["qty"].to_list() == [None]
    assert source_identity_questions(findings)[0]["source_identity_values"]["batch"] == "999"
    rows = [row(str(i), n) for i in range(20) for n in (1, 2)]
    _, _, findings = run(schema, rows)
    assert len(source_identity_questions(findings)) == 8
    assert any(f.details.get("conflicting_groups") == 20 for f in findings)


def test_invalid_key_answer_and_confirmation_cannot_waive_check(schema):
    from tests.test_decision_actions import document

    cm, _, findings = run(schema, [row(), row("L2")], keys=None)
    doc = document()
    doc.collections = [cm]
    (q,) = source_identity_questions(findings)
    for value in ("confirm", "[]", '["absent"]', '["batch", "batch"]', '["__row__"]'):
        with pytest.raises(ValidationFailed):
            apply(doc, q, value)
    apply(doc, q, json.dumps(["depot", "batch"]))
    assert cm.source_identity.keys == ["depot", "batch"]


def test_missing_quantity_and_zero_are_not_silently_summed(schema):
    _, out, findings = run(schema, [row(amount=None), row("L2", 0)])
    assert out["qty"].to_list() == [None]
    assert any(f.kind == "aggregate" and f.severity == "error" for f in findings)
    _, out, findings = run(schema, [row(amount=0), row("L2", 0)])
    assert out["qty"].to_list() == [0] and not findings


@pytest.mark.parametrize("action", ["approving", "exporting"])
def test_dataset_gate_rejects_unresolved_keys_even_if_schema_is_valid(action):
    from types import SimpleNamespace

    from forma.core.errors import Conflict
    from forma.services.datasets import _require_valid

    dv = SimpleNamespace(
        stats={"invalid_records": 0},
        findings=[{"kind": "source_identity", "severity": "error", "message": "unresolved"}],
    )
    with pytest.raises(Conflict, match="source record keys"):
        _require_valid(dv, action)


def test_source_key_is_part_of_reuse_signature_and_not_a_global_rule():
    from tests.test_decision_actions import document

    from forma.ai.evidence import reading_signature
    from forma.services.decision_memory import reusable

    doc = document()
    before = reading_signature(doc, "/prices/*/amount")
    doc.collections[0].source_identity = SourceIdentity(keys=["line"], evidence=["ERP line ID"])
    assert reading_signature(doc, "/prices/*/amount") != before
    assert not reusable({"action": "source_identity", "remember": True})


def test_quantity_repair_allows_contract_default_but_not_unrelated_status_change():
    from forma.engine.transform import TransformResult
    from forma.schema_registry import TargetSchema
    from forma.services.review_evidence import _validate_answer_results

    schema = TargetSchema(
        "repair",
        1,
        {
            "type": "object",
            "required": ["id", "qty"],
            "properties": {
                "id": {"type": "string"},
                "qty": {"type": "number"},
                "status": {"type": "string", "default": "active"},
            },
            "x-forma": {"identity_keys": ["/id"]},
        },
    )
    before = TransformResult([{"id": "x", "qty": None}], pl.DataFrame(), [], {"invalid_records": 1})
    after = TransformResult(
        [{"id": "x", "qty": 2, "status": "active"}], pl.DataFrame(), [], {"invalid_records": 0}
    )
    decisions = [{"action": "constant", "field": {"pointer": "/qty"}}]
    assert _validate_answer_results(schema, before, after, decisions)["passed"]
    after.records[0]["status"] = "invented"
    with pytest.raises(ValidationFailed, match="unrelated field"):
        _validate_answer_results(schema, before, after, decisions)
    before.records[0]["status"] = "archived"
    after.records[0]["status"] = "active"
    with pytest.raises(ValidationFailed, match="unrelated field"):
        _validate_answer_results(schema, before, after, decisions)


@pytest.mark.parametrize("rows", [[row(), row(amount=9), row("L2", 2)], [row(batch=None), row("L2", 2)]])
def test_fallback_without_grain_cannot_offer_warehouse_wide_row_choices(schema, rows):
    from tests.test_decision_actions import document

    from forma.mapping_dsl.models import call, const
    from forma.services.decision_actions import conflicting_items

    cm, frame, root = setup_collection(rows, keys=None, aggregate=False)
    _, findings = TransformEngine(schema)._collection(frame, cm, root, "r")
    assert source_identity_questions(findings)[0]["action"] == "source_identity"
    doc = document()
    doc.collections = [cm]
    records = [
        {"sku": "001", "inventory": [{"warehouse_code": "A", "qty": 7}, {"warehouse_code": "A", "qty": 2}]}
    ]
    assert not conflicting_items(doc, schema, records)
    with pytest.raises(ValidationFailed, match="older row choice"):
        apply(
            doc,
            {
                "action": "select_rows",
                "collection": cm.target,
                "target": "/inventory/*/qty",
                "field": {"semantic": "stock.qty"},
                "options": ["0", "1"],
                "row_options": {"0": 0, "1": 1},
            },
            "0",
        )
    # The old answer path retained one row, erasing the evidence of another lot.
    cm.filter = call("eq", col(ROW_COL), const(0))
    _, findings = TransformEngine(schema)._collection(frame, cm, root, "r")
    assert any(f.details.get("reason") == "missing_key" for f in findings)


def test_missing_key_cannot_be_filtered_away(schema):
    from forma.mapping_dsl.models import call, const

    cm, frame, root = setup_collection([row(batch=None), row("L2", 2)])
    cm.filter = call("eq", col("batch"), const("L2"))
    out, findings = TransformEngine(schema)._collection(frame, cm, root, "r")
    assert out["qty"].to_list() == [None]
    assert any(f.details.get("reason") == "blank_key" for f in findings)


def test_conflict_choice_cannot_delete_an_unrelated_lot(schema):
    from forma.mapping_dsl.models import call, const

    cm, frame, root = setup_collection([row(), row(), row(amount=9), row("L2", 2)])
    cm.filter = call("eq", col(ROW_COL), const(2))
    out, findings = TransformEngine(schema)._collection(frame, cm, root, "r")
    assert out["qty"].to_list() == [None]
    assert any(f.details.get("reason") == "invalid_selection" for f in findings)


def test_quantity_cannot_become_identity_by_removing_aggregation(schema):
    _, _, findings = run(schema, [row(), row(amount=9)], keys=("depot", "batch", "amount"), aggregate=False)
    assert any(f.details.get("reason") == "incomplete_key" for f in findings)


def test_request_source_control_normalization_preserves_business_meaning(schema):
    from tests.test_decision_actions import document

    from forma.ai.clarifications import normalize_controls, validate

    q = {
        "target": "/prices/*/amount",
        "kind": "enum",
        "action": "request_source",
        "question": "Please supply the missing source",
        "why": "Input is incomplete",
    }
    normalize_controls([q])
    assert q["kind"] == "confirm" and q["action"] == "request_source"
    assert not validate([q], document(), schema)
    q.update(kind="enum", options=["7", "9"])
    normalize_controls([q])
    assert q["kind"] == "enum" and validate([q], document(), schema)


@pytest.mark.parametrize("missing", [False, True])
def test_workbench_exposes_actual_preaggregation_checks(tmp_path, schema, missing):
    from types import SimpleNamespace

    from forma.ai.workbench import FileWorkbench
    from forma.engine.transform import BoundSource
    from forma.mapping_dsl.models import (
        MappingDocument,
        MappingMeta,
        RecordSpec,
        SourceSpec,
        TableSpec,
        const,
    )
    from forma.profiling.profile import profile_workbook

    path = tmp_path / "source.csv"
    path.write_text(
        "sku,depot,batch,amount\n001,A,,7\n001,A,L2,2\n"
        if missing
        else "sku,depot,batch,amount\n001,A,L1,7\n001,A,L1,7\n001,A,L1,9\n001,A,L2,2\n",
        encoding="utf-8",
    )
    cm, _, _ = setup_collection([row()])
    doc = MappingDocument(
        meta=MappingMeta(target_schema="product@1"),
        sources=[SourceSpec(id="s", table=TableSpec(header_row=0))],
        record=RecordSpec(root_source="s"),
        attributes_passthrough=False,
        fields=[
            FieldMapping(target="/sku", expr=col("sku")),
            FieldMapping(target="/product_name", expr=const("Tea")),
            FieldMapping(target="/currency", expr=const("THB")),
        ],
        collections=[cm],
    )
    f = SimpleNamespace(
        id="f1",
        original_name=path.name,
        storage_key=str(path),
        status="profiled",
        profile=None,
        fingerprint=None,
    )
    bench = FileWorkbench(
        schema,
        profile_workbook(path),
        [f],
        bind=lambda d: ({"s": BoundSource(path)}, []),
        resolve=lambda _: path,
    )
    result = bench.verify(doc)
    assert result.source_decision_pending and result.errors > 0
    checks = result.to_prompt()["source_identity_checks"]
    if missing:
        assert any(c.get("reason") == "blank_key" for c in checks)
        assert result.decision_questions[0]["action"] == "request_source"
    else:
        receipt = next(c for c in checks if c["severity"] == "info")
        assert receipt["input_rows"] == 4 and receipt["retained_rows"] == 3 and receipt["dropped_rows"] == 1
        assert result.decision_questions[0]["row_exclusions"]["2"] == [0, 1]
