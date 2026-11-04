"""Failures found by relationships_v1; no provider/answer-key dependency."""

from __future__ import annotations

import asyncio
import datetime as dt
import time
from pathlib import Path

import httpx
import openpyxl
import polars as pl
import pytest

from forma.ai.llm import LLMError, OpenAICompatibleProvider
from forma.core.errors import UnsupportedFile
from forma.engine.ops import CompileContext, compile_expr
from forma.engine.transform import BoundSource, load_source_frame
from forma.mapping_dsl.models import SheetSelect, SourceSpec, TableSpec, call, col, const


@pytest.mark.parametrize(
    "op,expected",
    [
        ("gte", [False, True, True]),
        ("lte", [True, True, False]),
        ("gt", [False, False, True]),
        ("lt", [True, False, False]),
    ],
)
def test_ordered_date_comparison(op, expected):
    frame = pl.DataFrame({"s.date": ["2026-11-02", "2026-11-03", "2026-11-04"]})
    expr = call(op, call("parse_date_th", col("date")), call("parse_date_th", const("2026-11-03")))
    assert (
        frame.select(compile_expr(expr, CompileContext("s", set(frame.columns))))[:, 0].to_list() == expected
    )


def test_numeric_comparison_still_parses_localized_numbers_and_null():
    frame = pl.DataFrame({"s.value": ["1,200", "๒๐", None]})
    expr = call("gt", col("value"), const(100))
    assert frame.select(compile_expr(expr, CompileContext("s", set(frame.columns))))[:, 0].to_list() == [
        True,
        False,
        None,
    ]


def test_datetime_comparison_keeps_time():
    frame = pl.DataFrame({"s.t": [dt.datetime(2026, 11, 3, 12), dt.datetime(2026, 11, 3, 14)]})
    expr = call("gt", col("t"), col("cutoff"))
    frame = frame.with_columns(pl.lit(dt.datetime(2026, 11, 3, 13)).alias("s.cutoff"))
    assert frame.select(compile_expr(expr, CompileContext("s", set(frame.columns))))[:, 0].to_list() == [
        False,
        True,
    ]


def test_unused_uncached_formula_does_not_block_plain_columns_but_used_formula_does(tmp_path: Path):
    path = tmp_path / "input.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Export"
    ws.append(["Item", "Name", "Calculated"])
    ws.append(["0001", "Coffee", "=2*6"])
    wb.save(path)
    spec = SourceSpec(id="source", sheet=SheetSelect(name="Export"), table=TableSpec(header_row=0))
    frame = load_source_frame(spec, BoundSource(path), expected_columns={"Item", "Name"})
    assert frame["Item"].to_list() == ["0001"]
    with pytest.raises(UnsupportedFile, match="C2"):
        load_source_frame(spec, BoundSource(path), expected_columns={"Item", "Calculated"})
    with pytest.raises(UnsupportedFile, match="C2"):
        load_source_frame(spec, BoundSource(path), expected_columns={"Item"}, verify_all_columns=True)
    with pytest.raises(UnsupportedFile):
        load_source_frame(spec, BoundSource(path))


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {"choices": None},
        {"choices": []},
        {"choices": [None]},
        {"choices": [{"message": None}]},
        {"choices": [{"message": {"content": None}}]},
    ],
)
def test_malformed_provider_response_is_actionable_not_typeerror(monkeypatch, payload):
    actual = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: actual(
            **kw, transport=httpx.MockTransport(lambda req: httpx.Response(200, json=payload))
        ),
    )
    with pytest.raises(LLMError, match=r"response|choices|content"):
        OpenAICompatibleProvider("test").complete_json(
            system="s", user="u", schema={}, model="test", temperature=0
        )


def test_total_http_deadline_interrupts_continuous_keepalives(monkeypatch):
    class Keepalive(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            while True:
                await asyncio.sleep(0.01)
                yield b" "

        async def aclose(self):
            self.closed = True

    stream = Keepalive()
    actual = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: actual(
            **kw, transport=httpx.MockTransport(lambda req: httpx.Response(200, stream=stream))
        ),
    )
    start = time.monotonic()
    with pytest.raises(httpx.ReadTimeout, match="wall-clock"):
        OpenAICompatibleProvider("test", timeout=0.08).complete_json(
            system="s", user="u", schema={}, model="test", temperature=0
        )
    assert time.monotonic() - start < 1 and stream.closed


def test_aliases_do_not_invent_field_disagreements():
    from tests.test_agents import _doc

    from forma.services.mapping import _agreement

    original = _doc()
    changed = original.model_copy(deep=True)
    changed.sources[0].rename = {"ชื่อสินค้า": "description"}
    changed.fields[1].expr = col("description")
    assert "/product_name" not in _agreement(original, changed)["disagree"]
    changed.fields[1].expr = call("regex_replace", col("description"), pattern="x", value="y")
    assert "/product_name" in _agreement(original, changed)["disagree"]


def test_structural_questions_retire_only_for_the_verified_source(schema):
    from tests.test_agents import _doc

    from forma.services.mapping import _retire_structural_questions

    doc = _doc()
    same = "Pricing table 'สินค้า' has no recognisable amount column"
    other = "Pricing table 'Other' has no recognisable amount column"
    business = "Does the price include VAT?"
    kept, retired = _retire_structural_questions(
        [same, other, business], doc, schema, [{"sku": "001", "prices": [{"amount": 12}]}]
    )
    assert retired == [same] and kept == [other, business]
    kept, retired = _retire_structural_questions([same], doc, schema, [{"prices": [{"amount": None}]}])
    assert kept == [same] and retired == []


@pytest.mark.parametrize("report", [{"complete": True, "passed": False}, {"complete": False, "passed": True}])
@pytest.mark.parametrize("override", [False, True])
def test_human_confirmation_cannot_override_execution_failure(monkeypatch, report, override):
    from types import SimpleNamespace

    from forma.core.errors import ValidationFailed
    from forma.services import mapping

    version = SimpleNamespace(status="proposed", policy={"verification": report})
    monkeypatch.setattr(mapping, "get_mapping", lambda *a: version)
    with pytest.raises(ValidationFailed, match="verify all records"):
        mapping.approve_mapping(None, "test", "reviewer", "reviewed", override_block=override)
    assert version.status == "proposed"


def test_aggregate_converts_each_lot_then_sums_without_dropping_identical_lots(schema):
    from forma.engine.transform import ROW_COL, TransformEngine
    from forma.mapping_dsl.models import CollectionAggregate, CollectionMapping, FieldMapping, JoinKey

    cm = CollectionMapping(
        target="/inventory",
        source="lots",
        link=[JoinKey(left="id", right="id")],
        fields=[
            FieldMapping(target="/inventory/*/warehouse_code", expr=col("warehouse")),
            FieldMapping(target="/inventory/*/qty", expr=call("multiply", col("qty"), col("factor"))),
        ],
        aggregate=CollectionAggregate(group_by=["/warehouse_code"], sum=["/qty"]),
    )
    source = pl.DataFrame(
        {
            "lots.id": ["001"] * 3,
            "lots.warehouse": ["A"] * 3,
            "lots.qty": [2, 2, 3],
            "lots.factor": [12, 12, 1],
            f"lots.{ROW_COL}": [0, 1, 2],
        }
    )
    root = pl.DataFrame({"root.id": ["001"]})
    items, findings = TransformEngine(schema)._collection(source, cm, root, "root")
    assert items["qty"].to_list() == [51]
    assert items["__src_row__"].to_list() == [[0, 1, 2]]
    assert not findings
    source = source.with_columns(pl.Series("lots.qty", [2, None, 3]))
    items, findings = TransformEngine(schema)._collection(source, cm, root, "root")
    assert items["qty"].to_list() == [None]
    assert any(f.severity == "error" and f.kind == "aggregate" for f in findings)


def test_aggregate_rejects_unaccounted_fields_and_non_numeric_sum(schema):
    from tests.test_agents import _doc

    from forma.mapping_dsl.models import CollectionAggregate
    from forma.mapping_dsl.validate import validate_mapping

    doc = _doc()
    doc.collections[0].aggregate = CollectionAggregate(group_by=[], sum=["/price_list"])
    report = validate_mapping(doc, schema)
    assert {i.code for i in report.issues} >= {"aggregate_fields", "aggregate_type", "aggregate_identity"}


def test_aggregate_full_workbook_preserves_one_group_per_output_item(tmp_path, schema):
    from tests.test_agents import _doc

    from forma.engine.transform import TransformEngine
    from forma.mapping_dsl.models import CollectionAggregate, CollectionMapping, FieldMapping, JoinKey

    path = tmp_path / "lots.xlsx"
    wb = openpyxl.Workbook()
    wb.active.title = "สินค้า"
    wb.active.append(["รหัสสินค้า", "ชื่อสินค้า"])
    wb.active.append(["0001", "Tea"])
    ws = wb.create_sheet("Lots")
    ws.append(["id", "warehouse", "qty"])
    for row in [("0001", "A", 2), ("0001", "A", 2), ("0001", "B", 3)]:
        ws.append(row)
    wb.save(path)
    doc = _doc()
    doc.attributes_passthrough = False
    doc.sources.append(SourceSpec(id="lots", sheet=SheetSelect(name="Lots")))
    doc.collections = [
        CollectionMapping(
            target="/inventory",
            source="lots",
            link=[JoinKey(left="รหัสสินค้า", right="id")],
            fields=[
                FieldMapping(target="/inventory/*/warehouse_code", expr=col("warehouse")),
                FieldMapping(target="/inventory/*/qty", expr=call("parse_number_th", col("qty"))),
            ],
            aggregate=CollectionAggregate(group_by=["/warehouse_code"], sum=["/qty"]),
        )
    ]
    result = TransformEngine(schema).run(doc, {s.id: BoundSource(path) for s in doc.sources})
    assert not result.errors
    assert result.records[0]["inventory"] == [
        {"warehouse_code": "A", "qty": 4.0},
        {"warehouse_code": "B", "qty": 3.0},
    ]
    lineage = result.records[0]["source_meta"]["lineage"]
    assert lineage["items"]["/inventory"] == [0, 2]
    assert lineage["item_groups"]["/inventory"] == [[0, 1], [2]]


def test_task_budget_is_shared_across_requests_and_restored():
    from forma.ai.budget import deadline, remaining, task_budget

    assert remaining() is None
    with task_budget(0.1):
        before = deadline.get()
        with task_budget(60):
            assert deadline.get() == before
        time.sleep(0.11)
        with pytest.raises(LLMError, match="task deadline"):
            remaining()
    assert remaining() is None


def test_missing_operand_never_becomes_a_constant_final_quantity(schema):
    from tests.test_agents import _doc

    from forma.ai.clarifications import validate
    from forma.core.errors import ValidationFailed
    from forma.services import decision_actions

    doc = _doc()
    fm = doc.collections[0].fields[1]
    fm.expr = call("multiply", col("quantity"), col("factor"))
    question = {
        "target": fm.target,
        "kind": "number",
        "action": "constant",
        "question": "What factor belongs in the missing workbook?",
        "why": "External input is absent",
    }
    assert validate([question], doc, schema)
    question["field"] = schema.field(fm.target).to_dict()
    with pytest.raises(ValidationFailed, match="calculated field"):
        decision_actions.apply(doc, question, 12)
    request = {k: v for k, v in question.items() if k != "field"}
    request.update(action="request_source", kind="confirm")
    assert validate([request], doc, schema) == []
    with pytest.raises(ValidationFailed, match="repair the source"):
        decision_actions.apply(doc, {**request, "field": question["field"]}, "confirm")


def test_root_conflict_answer_filters_only_the_unselected_source_row(tmp_path, schema, monkeypatch):
    import csv
    from types import SimpleNamespace

    from tests.test_agents import _doc

    from forma.core.errors import ValidationFailed
    from forma.engine.transform import TransformEngine
    from forma.mapping_dsl.models import FieldMapping, JoinKey, JoinSpec
    from forma.services import mapping, review_evidence
    from forma.services.decision_actions import apply, conflicting_records

    master, prices = tmp_path / "master.csv", tmp_path / "prices.csv"
    for path, rows in [
        (master, [["id", "name"], ["0001", "A"], ["0002", "B"]]),
        (prices, [["id", "price"], ["0001", 9999], ["0002", 200], ["0001", 152]]),
    ]:
        with path.open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(rows)
    doc = _doc()
    doc.sources = [SourceSpec(id="master"), SourceSpec(id="prices")]
    doc.record.root_source = "master"
    doc.record.dedupe.keep = "review"
    doc.fields = [
        FieldMapping(target="/sku", expr=col("id")),
        FieldMapping(target="/product_name", expr=col("name")),
        FieldMapping(target="/unit/pack_size", expr=call("parse_number_th", col("price", "prices"))),
    ]
    doc.collections = []
    doc.attributes_passthrough = False
    doc.joins = [
        JoinSpec(left="master", right="prices", on=[JoinKey(left="id", right="id")], cardinality="1:N")
    ]
    bindings = {"master": BoundSource(master), "prices": BoundSource(prices)}
    engine = TransformEngine(schema)
    before = engine.run(doc, bindings)
    assert before.errors
    questions = conflicting_records(before.findings)
    assert len(questions) == 1 and questions[0]["identity"] == {"/sku": "0001"}
    q = questions[0]
    q["field"] = schema.field(q["target"]).to_dict()
    apply(doc, q, "2")
    after = engine.run(doc, bindings)
    assert not after.errors
    assert [r["unit"]["pack_size"] for r in after.records] == [152, 200]
    assert conflicting_records(after.findings) == []
    ctx = SimpleNamespace(registry=SimpleNamespace(get_ref=lambda _: schema))
    session = SimpleNamespace(get=lambda *a: SimpleNamespace(files=[]))
    version = SimpleNamespace(
        round_id="round", target_schema=schema.ref, document=doc.model_dump(mode="json")
    )
    results = iter([before, after])
    monkeypatch.setattr(mapping, "simulate", lambda *a, **kw: (next(results), []))
    checked = review_evidence.validate_answer_effects(ctx, session, version, version.document, [q])
    assert checked["passed"] and checked["records_checked"] == 2
    # Even the selected field cannot change on a different product.
    after.records[1]["unit"]["pack_size"] = 500
    results = iter([before, after])
    with pytest.raises(ValidationFailed, match="unrelated field"):
        review_evidence.validate_answer_effects(ctx, session, version, version.document, [q])
