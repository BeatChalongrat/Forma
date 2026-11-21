"""Row accounting: every source row has a named fate, and the file's own totals are a check on the read."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl
import pytest

from forma.ai.heuristics import propose_mapping
from forma.ai.policy import PolicyEngine
from forma.ai.reconciliation import row_accounting, verification
from forma.engine.accounting import check_totals, source_findings
from forma.engine.transform import BoundSource, TransformEngine, TransformResult
from forma.mapping_dsl.models import TableSpec, call, col, const
from forma.profiling.profile import profile_workbook
from forma.profiling.tables import AUDIT_ROW, extract_table
from forma.schema_registry import SchemaRegistry, TargetSchema
from forma.services.mapping import release_row_bounds
from forma.services.review_explanations import explain

CONTRACT = {
    "type": "object",
    "additionalProperties": False,
    "required": ["item_id", "label", "selling_price"],
    "x-forma": {"schema_id": "ledger", "identity_keys": ["/item_id"]},
    "properties": {
        "item_id": {"type": "string", "x-forma": {"semantic": "sku", "synonyms_en": ["SKU"]}},
        "label": {"type": "string", "x-forma": {"semantic": "product_name", "synonyms_en": ["Name"]}},
        "selling_price": {
            "type": "number",
            "x-forma": {"semantic": "price.amount", "synonyms_en": ["Sales Price"]},
        },
    },
}
HEADER = "SKU,Name,Sales Price"


def _grid(rows: list[list[Any]]) -> pl.DataFrame:
    width = max(len(r) for r in rows)
    padded = [[None if v is None else str(v) for v in r] + [None] * (width - len(r)) for r in rows]
    return pl.DataFrame(padded, schema={f"c{i}": pl.String for i in range(width)}, orient="row")


def _audit(rows: list[list[Any]], **spec: Any) -> tuple[pl.DataFrame, dict[str, Any]]:
    audit: dict[str, Any] = {}
    frame = extract_table(_grid(rows), TableSpec(header_row=0, **spec), audit=audit)
    return frame, audit


def _run(tmp_path: Path, body: str) -> tuple[TargetSchema, TransformResult]:
    schema = SchemaRegistry(tmp_path / "schemas").prepare(CONTRACT)
    path = tmp_path / "items.csv"
    path.write_text(f"{HEADER}\n{body}", encoding="utf-8")
    mapping = propose_mapping(profile_workbook(path), schema).mapping
    return schema, TransformEngine(schema).run(mapping, {s.id: BoundSource(path) for s in mapping.sources})


def test_audit_does_not_change_the_extracted_table() -> None:
    rows = [["SKU", "Qty"], ["A", "1"], [None, None], ["B", "2"], ["Total", "3"]]
    plain = extract_table(_grid(rows), TableSpec(header_row=0))
    audited, audit = _audit(rows)
    assert audited.equals(plain) and AUDIT_ROW not in audited.columns
    assert audit["grid_rows"] == 4 and audit["table_rows"] == 2
    assert audit["dropped"] == {"blank": 1, "total_row": 1}
    assert audit["grid_rows"] - sum(audit["dropped"].values()) == audit["table_rows"]


@pytest.mark.parametrize(
    ("rows", "confirmed"),
    [
        ([["A", "1,000.50"], ["B", "๒๕๐"], ["Total", "1,250.50"]], 1),  # grand total, Thai digits
        ([["A", "10"], ["Subtotal", "10"], ["B", "5"], ["C", "7"], ["Subtotal", "12"], ["Total", "22"]], 3),
        ([["A", "10.004"], ["B", "10.004"], ["รวม", "20.01"]], 1),  # printed rounded
        ([["A", "10"], ["A", "10"], ["B", "5"], ["Total", "15"]], 1),  # a repeated row is totalled once
        ([["A", "7"], ["B", "9"], ["รวม 2 รายการ", "2"]], 1),  # a count of rows
    ],
)
def test_totals_that_agree_are_confirmed(rows: list[list[str]], confirmed: int) -> None:
    _, audit = _audit([["SKU", "Qty"], *rows])
    assert audit["totals"]["confirmed"] == confirmed
    assert audit["totals"]["contradicted"] == []
    assert source_findings("items", audit) == []


def test_total_that_agrees_with_nothing_is_a_source_finding() -> None:
    _, audit = _audit([["SKU", "Qty", "Note"], ["A", "10", "x"], ["B", "5", "y"], ["Total", "40", None]])
    (item,) = audit["totals"]["contradicted"]
    assert item["sheet_row"] == 4 and item["columns"] == [{"column": "Qty", "total": 40.0, "rows_sum": 15.0}]
    (finding,) = source_findings("items", audit)
    assert "sheet row 4" in finding["message"] and "Qty = 40" in finding["message"]
    assert finding["details"]["check"] == "totals_row"


def test_one_agreeing_column_confirms_a_row_with_an_average_beside_it() -> None:
    rows = [["SKU", "Qty", "Unit price"], ["A", "2", "10"], ["B", "4", "20"], ["Total", "6", "15"]]
    _, audit = _audit(rows)
    assert audit["totals"]["confirmed"] == 1 and not audit["totals"]["contradicted"]


def test_total_row_written_one_column_off_still_confirms_the_rows() -> None:
    rows = [
        ["SKU", "Unit", "BKK", "CNX"],
        ["A", "PCS", "4", "1"],
        ["B", "BOX", "6", "2"],
        ["รวม", "10", "3", None],
    ]
    _, audit = _audit(rows)
    assert audit["totals"]["confirmed"] == 1 and not audit["totals"]["contradicted"]


def test_text_columns_and_empty_totals_are_not_checked() -> None:
    kept = pl.DataFrame({"SKU": ["A", "B"], "Unit": ["PCS", "BOX"], AUDIT_ROW: [1, 2]})
    totals = pl.DataFrame({"SKU": ["Total"], "Unit": ["9"], AUDIT_ROW: [3]})  # not a sum, not the row count
    report = check_totals(kept, totals, ["SKU", "Unit"], AUDIT_ROW)
    assert report == {"rows": 1, "confirmed": 0, "unchecked": 1, "contradicted": []}


def test_rows_that_look_like_data_after_the_blank_stop_are_reported() -> None:
    rows = [
        ["SKU", "Name", "Qty"],
        ["A", "tea", "1"],
        ["B", "milk", "2"],
        [None, None, None],
        [None, None, None],
        ["C", "rice", "3"],
        ["Prepared by: finance"],
        ["Total", None, "6"],
    ]
    frame, audit = _audit(rows)
    assert frame.height == 2
    assert audit["after_blank_stop"] == {"rows": 3, "data_like": 1, "first_sheet_row": 6}
    (finding,) = source_findings("items", audit)
    assert "1 rows that look like data follow a blank gap (from sheet row 6)" in finding["message"]


def test_a_note_or_a_total_after_the_blank_stop_is_not_data() -> None:
    rows = [
        ["SKU", "Name", "Qty"],
        ["A", "tea", "1"],
        [None],
        [None],
        ["Printed 2026-11-20"],
        ["Total", None, "1"],
    ]
    _, audit = _audit(rows)
    assert audit["after_blank_stop"]["data_like"] == 0 and source_findings("items", audit) == []


def test_rows_past_the_last_row_a_mapping_fixed_are_reported() -> None:
    rows = [
        ["SKU", "Name", "Qty"],
        ["A", "tea", "1"],
        ["B", "milk", "2"],
        ["C", "rice", "3"],
        ["D", "oil", "4"],
    ]
    frame, audit = _audit(rows, data_end=3)  # written when the file had two products
    assert frame.height == 2
    assert audit["after_data_end"] == {"rows": 2, "data_like": 2, "first_sheet_row": 4}
    (finding,) = source_findings("items", audit)
    assert "2 rows that look like data follow the last row this mapping reads" in finding["message"]
    assert finding["details"]["check"] == "rows_after_data_end"
    assert audit["grid_rows"] - sum(audit["dropped"].values()) == audit["table_rows"]


def test_a_total_or_a_note_past_the_fixed_last_row_is_not_data() -> None:
    rows = [["SKU", "Name", "Qty"], ["A", "tea", "1"], ["B", "milk", "2"], ["Total", None, "3"], ["Printed"]]
    _, audit = _audit(rows, data_end=3)
    assert audit["after_data_end"]["data_like"] == 0 and source_findings("items", audit) == []


def test_the_end_of_a_table_found_in_this_file_is_not_a_fixed_limit() -> None:
    rows = [["SKU", "Name", "Qty"], ["A", "tea", "1"], ["B", "milk", "2"], ["C", "rice", "3"]]
    audit: dict[str, Any] = {}
    extract_table(_grid(rows), TableSpec(header_row=0, data_end=3), audit=audit, detected=True)
    assert "after_data_end" not in audit
    _, free = _audit(rows)
    assert "after_data_end" not in free


def test_a_row_limit_that_changes_nothing_is_released_and_one_that_cuts_stays(tmp_path: Path) -> None:
    schema = SchemaRegistry(tmp_path / "schemas").prepare(CONTRACT)
    path = tmp_path / "items.csv"
    path.write_text(f"{HEADER}\n001,tea,10\n002,milk,20\n003,rice,30\n", encoding="utf-8")
    mapping = propose_mapping(profile_workbook(path), schema).mapping
    bindings = {s.id: BoundSource(path) for s in mapping.sources}
    (source,) = mapping.sources
    source.table = source.table.model_copy(update={"header_row": 0, "data_start": 1, "data_end": 4})
    assert release_row_bounds(mapping, bindings) == [f"{source.id}.data_end", f"{source.id}.data_start"]
    assert source.table.data_end is None and source.table.data_start is None

    # next month's longer file is read whole
    path.write_text(f"{HEADER}\n001,tea,10\n002,milk,20\n003,rice,30\n004,oil,40\n", encoding="utf-8")
    assert len(TransformEngine(schema).run(mapping, bindings).records) == 4

    source.table = source.table.model_copy(update={"data_end": 3})  # cuts two rows: a decision, kept
    assert release_row_bounds(mapping, bindings) == []
    held = TransformEngine(schema).run(mapping, bindings)
    assert len(held.records) == 2
    (notice,) = [f for f in held.findings if f.kind == "source_review"]
    assert notice.details["check"] == "rows_after_data_end" and notice.details["data_like"] == 2
    decision = PolicyEngine().evaluate(
        mapping=mapping,
        schema=schema,
        findings=[f.to_dict() for f in held.findings],
        rows_out=len(held.records),
        drift="identical",
        has_previous_approval=True,
        reused_approved=True,
    )
    assert decision.decision == "review" and decision.checks["source_review_required"] is True
    assert verification(held, schema, complete=True)["row_accounting"]["data_like_rows_not_read"] == 2


def test_engine_ledger_names_the_fate_of_every_root_row(tmp_path: Path) -> None:
    schema, result = _run(tmp_path, "001,tea,10\n001,tea,10\n002,milk,20\n,ghost,5\nTotal,,35\n")
    root = result.stats["accounting"]["root"]
    assert root["rows_in"] == 4 and root["rows_out"] == 2
    assert root["duplicate_identity"] == 1 and root["null_identity"] == 1 and root["balanced"]
    assert result.stats["accounting"]["complete"] is True
    source = result.stats["accounting"]["sources"][root["source"]]
    assert source["dropped"] == {"total_row": 1} and source["totals"]["confirmed"] == 1
    report = verification(result, schema, complete=True)
    assert report["row_accounting"]["totals"] == {
        "rows": 1,
        "confirmed": 1,
        "unchecked": 0,
        "contradicted": 0,
    }
    assert report["row_accounting"]["root"] == root


def test_record_filter_is_counted_and_a_sample_run_says_so(tmp_path: Path) -> None:
    schema = SchemaRegistry(tmp_path / "schemas").prepare(CONTRACT)
    path = tmp_path / "items.csv"
    path.write_text(f"{HEADER}\n001,tea,10\n002,milk,0\n003,rice,30\n", encoding="utf-8")
    mapping = propose_mapping(profile_workbook(path), schema).mapping
    mapping.record.filters = [call("gt", call("parse_number_th", col("Sales Price")), const(0))]
    bindings = {s.id: BoundSource(path) for s in mapping.sources}
    full = TransformEngine(schema).run(mapping, bindings)
    assert full.stats["accounting"]["root"]["filtered"] == 1 and full.stats["accounting"]["root"]["balanced"]
    sample = TransformEngine(schema).run(mapping, bindings, limit=2)
    assert sample.stats["accounting"]["complete"] is False
    summary = row_accounting(sample.stats)
    assert summary is not None and summary["complete"] is False


def test_contradicted_total_blocks_automatic_approval_even_for_a_reused_mapping(tmp_path: Path) -> None:
    schema, result = _run(tmp_path, "001,tea,10\n002,milk,20\nTotal,,99\n")
    notices = [f for f in result.findings if f.kind == "source_review"]
    assert len(notices) == 1 and notices[0].details["check"] == "totals_row"
    assert not result.errors  # the records are still produced; a person decides
    mapping = propose_mapping(profile_workbook(tmp_path / "items.csv"), schema).mapping
    for field in mapping.fields:
        field.confidence = 1.0
    mapping.meta.confidence = 1.0

    def decide(findings: list[dict[str, Any]]) -> Any:
        return PolicyEngine().evaluate(
            mapping=mapping,
            schema=schema,
            findings=findings,
            rows_out=len(result.records),
            drift="identical",
            has_previous_approval=True,
            reused_approved=True,
        )

    held = decide([f.to_dict() for f in result.findings])
    assert held.decision == "review" and held.checks["source_review_required"] is True
    assert any("total on sheet row 4" in r for r in held.reasons)
    # the total is the only thing holding this file back
    assert (
        decide([f.to_dict() for f in result.findings if f.kind != "source_review"]).decision == "auto_approve"
    )


def test_new_reasons_are_explained_in_thai() -> None:
    reasons = [
        "items: total on sheet row 4 says Qty = 40 but the rows read add up to 15; rows may be missing",
        "items: 2 rows that look like data follow a blank gap (from sheet row 9) and were not read; confirm",
        "items: 6 rows that look like data follow the last row this mapping reads (from sheet row 11) and",
    ]
    first, second, third = explain(None, reasons, None)  # type: ignore[arg-type]
    assert "แถวรวมที่แถว 4" in first["th"] and "40" in first["th"] and "15" in first["th"]
    assert "2 แถว" in second["th"] and "แถว 9" in second["th"]
    assert "6 แถว" in third["th"] and "แถว 11" in third["th"] and "แถวสุดท้าย" in third["th"]
