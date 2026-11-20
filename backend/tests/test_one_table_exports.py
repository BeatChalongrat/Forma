"""One table that holds more than one thing: rules drawn across it, cells merged down it, several rows per
product, two codes per row (ADR-075)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import openpyxl
import polars as pl
import pytest

from forma.ai.heuristics import propose_mapping
from forma.engine.transform import BoundSource, TransformEngine, TransformResult
from forma.ingestion.readers import read_raw, vertical_merges
from forma.mapping_dsl.models import MappingDocument, TableSpec
from forma.profiling.profile import profile_workbook
from forma.profiling.tables import extract_table
from forma.schema_registry import TargetSchema

HEADER = ["รหัสสินค้า", "ชื่อสินค้า", "ราคาขาย (รวม VAT)"]


def _book(path: Path, rows: list[list[Any]], merges: tuple[str, ...] = ()) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "สินค้า"
    for row in rows:
        ws.append(row)
    for cells in merges:
        ws.merge_cells(cells)
    wb.save(path)
    return path


def _propose(path: Path, schema: TargetSchema) -> tuple[MappingDocument, list[str], TransformResult]:
    proposal = propose_mapping(profile_workbook(path), schema)
    mapping = proposal.mapping
    result = TransformEngine(schema).run(mapping, {s.id: BoundSource(path) for s in mapping.sources})
    return mapping, proposal.open_questions, result


def _column(mapping: MappingDocument, target: str) -> str | None:
    fields = [*mapping.fields, *(f for c in mapping.collections for f in c.fields)]
    field = next((f for f in fields if f.target == target), None)
    node = field.expr if field else None
    while node is not None and getattr(node, "op", None) != "col":
        node = node.args[0] if getattr(node, "args", None) else None
    return node.name if node is not None else None


def _errors(result: TransformResult) -> list[str]:
    return [f.message for f in result.findings if f.severity == "error"]


# ---- rules of a printed list -------------------------------------------------------------------------
def test_a_line_of_dashes_keeps_its_place_and_holds_nothing(tmp_path: Path) -> None:
    text = "-----------\n|Code|Qty|\n|----+---|\n|A1  |  3|\n|A2  |---|\n===========\n"
    path = tmp_path / "list.txt"
    path.write_text(text, encoding="utf-8")
    assert read_raw(path).rows() == [
        (None, None, None, None),
        (None, "Code", "Qty", None),
        (None, None, None, None),
        (None, "A1", "3", None),
        (None, "A2", "---", None),  # a row with a code beside the dashes is data
        (None, None, None, None),
    ]


# ---- cells merged down the data ------------------------------------------------------------------------
def _merged_sheet(tmp_path: Path) -> Path:
    rows = [
        ["Price list", None, None, None],
        ["Code", "Name", "Brand", "Plant"],
        ["A1", "น้ำดื่ม", "Siam", "BKK"],
        [None, None, None, "CNX"],
        ["A2", "โซดา", None, "BKK"],  # no brand, and nothing merged: it stays empty
        [None, None, None, "CNX"],
    ]
    return _book(tmp_path / "merged.xlsx", rows, ("A1:A1", "A3:A4", "B3:B4", "C3:C4", "A5:A6", "B5:B6"))


def test_a_merged_value_is_read_on_each_row_it_covers_and_nowhere_else(tmp_path: Path) -> None:
    path = _merged_sheet(tmp_path)
    merges = vertical_merges(path, 0)
    assert sorted(merges) == [(0, 2, 3), (0, 4, 5), (1, 2, 3), (1, 4, 5), (2, 2, 3)]
    table = profile_workbook(path).tables[0]
    assert table.table.fill_merged is True and table.table.to_spec().fill_merged is True
    spec = TableSpec(header_row=1, fill_merged=True)
    assert extract_table(read_raw(path, 0), spec, merges=merges).rows() == [
        ("A1", "น้ำดื่ม", "Siam", "BKK"),
        ("A1", "น้ำดื่ม", "Siam", "CNX"),
        ("A2", "โซดา", None, "BKK"),
        ("A2", "โซดา", None, "CNX"),  # the plain forward fill of fill_down would have written "Siam" here
    ]
    assert extract_table(read_raw(path, 0), TableSpec(header_row=1)).rows()[1] == (None, None, None, "CNX")
    with pytest.raises(ValueError, match="needs the sheet's merged ranges"):
        extract_table(read_raw(path, 0), spec)


def test_a_merge_in_the_header_does_not_make_a_table_fill_its_data(tmp_path: Path) -> None:
    rows = [["Code", "Price", None], [None, "Retail", "Wholesale"], ["A1", 10, 8], ["A2", 20, 16]]
    path = _book(tmp_path / "header.xlsx", rows, ("A1:A2", "B1:C1"))
    assert vertical_merges(path, 0) == [(0, 0, 1)]
    assert [t.table.fill_merged for t in profile_workbook(path).tables] == [False]


def test_csv_and_legacy_workbooks_record_no_merges(tmp_path: Path) -> None:
    path = tmp_path / "plain.csv"
    path.write_text("a,b\n1,2\n", encoding="utf-8")
    assert vertical_merges(path, None) == []


# ---- the product code ------------------------------------------------------------------------------------
def _codes(tmp_path: Path, header: list[str], extra: Any) -> Path:
    rows: list[list[Any]] = [header]
    for i in range(1, 9):
        rows.append([extra(i)[0], f"A{i:03d}", f"สินค้า {i}", i * 107, *extra(i)[1:]])
    return _book(tmp_path / "codes.xlsx", rows)


def test_a_system_record_id_is_not_the_product_code_when_another_column_is(
    tmp_path: Path, schema: TargetSchema
) -> None:
    path = _codes(tmp_path, ["Internal ID", "Name", "Display Name", "Base Price"], lambda i: [1200 + i])
    mapping, questions, result = _propose(path, schema)
    assert _column(mapping, "/sku") == "Name"
    assert next(f for f in mapping.fields if f.target == "/sku").confidence == 0.6
    assert any("'Internal ID' is taken for a system record id" in q for q in questions)
    assert [r["sku"] for r in result.records] == [f"A{i:03d}" for i in range(1, 9)]


def test_a_system_record_id_with_two_rivals_is_kept_and_asked_about(
    tmp_path: Path, schema: TargetSchema
) -> None:
    header = ["Internal ID", "Name", "Display Name", "Base Price", "Legacy"]
    path = _codes(tmp_path, header, lambda i: [1200 + i, f"Z{i:04d}"])
    mapping, questions, _ = _propose(path, schema)
    assert _column(mapping, "/sku") == "Internal ID"
    assert next(f for f in mapping.fields if f.target == "/sku").confidence == 0.6
    assert any("'Name', 'Legacy' could be it too" in q for q in questions), questions


def test_a_header_that_names_the_code_is_not_doubted_for_a_rival(
    tmp_path: Path, schema: TargetSchema
) -> None:
    header = ["Old Code", "รหัสสินค้า", "ชื่อสินค้า", "ราคาขาย (รวม VAT)"]
    path = _codes(tmp_path, header, lambda i: [f"Z{i:04d}"])
    mapping, questions, _ = _propose(path, schema)
    assert _column(mapping, "/sku") == "รหัสสินค้า"
    assert next(f for f in mapping.fields if f.target == "/sku").confidence == 1.0
    assert not any("product code" in q for q in questions), questions


# ---- stock on the product rows -----------------------------------------------------------------------------
def _per_warehouse(tmp_path: Path, rows: list[list[Any]], header: list[str] | None = None) -> Path:
    return _book(tmp_path / "stock.xlsx", [header or [*HEADER, "คลัง", "ยอดคงเหลือ"], *rows])


def test_stock_on_the_product_rows_is_read_with_the_warehouse_beside_it(
    tmp_path: Path, schema: TargetSchema
) -> None:
    rows = [
        [f"A{i:03d}", f"สินค้า {i}", i * 107, wh, i * k]
        for i in range(1, 7)
        for k, wh in ((3, "BKK"), (1, "CNX"))
    ]
    mapping, questions, result = _propose(_per_warehouse(tmp_path, rows), schema)
    assert [c.source for c in mapping.collections] == [mapping.record.root_source] * 2  # prices, inventory
    assert not _errors(result), _errors(result)
    first = result.records[0]
    assert (first["sku"], [p["amount"] for p in first["prices"]]) == ("A001", [107.0])
    assert [(s["warehouse_code"], s["qty"]) for s in first["inventory"]] == [("BKK", 3.0), ("CNX", 1.0)]
    assert len(result.records) == 6 and "attributes" not in first
    assert not any("warehouse" in q for q in questions)  # the header says it is the warehouse


def test_the_column_that_tells_a_products_rows_apart_is_taken_for_the_warehouse_and_asked(
    tmp_path: Path, schema: TargetSchema
) -> None:
    header = [*HEADER, "Organization Code", "ยอดคงเหลือ"]
    rows = [
        [f"A{i:03d}", f"สินค้า {i}", i * 107, org, i * k]
        for i in range(1, 7)
        for k, org in ((3, "M1"), (1, "M2"))
    ]
    mapping, questions, result = _propose(_per_warehouse(tmp_path, rows, header), schema)
    assert _column(mapping, "/inventory/*/warehouse_code") == "Organization Code"
    assert any("is given per 'Organization Code'" in q for q in questions), questions
    assert {s["warehouse_code"] for r in result.records for s in r["inventory"]} == {"M1", "M2"}


def test_a_stock_sheet_stays_the_only_source_of_stock(tmp_path: Path, schema: TargetSchema) -> None:
    path = tmp_path / "two.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "สินค้า"
    ws.append([*HEADER, "ยอดคงเหลือ"])
    stock = wb.create_sheet("Stock")
    stock.append(["รหัสสินค้า", "คลัง", "ยอดคงเหลือ"])
    for i in range(1, 7):
        ws.append([f"A{i:03d}", f"สินค้า {i}", i * 107, 999])
        stock.append([f"A{i:03d}", "BKK", i * 3])
    wb.save(path)
    mapping, _, result = _propose(path, schema)
    inventory = next(c for c in mapping.collections if c.target == "/inventory")
    assert inventory.source != mapping.record.root_source
    assert [s["qty"] for s in result.records[0]["inventory"]] == [3.0]


# ---- what another collection exports is not lost here ----------------------------------------------------------
def test_a_price_that_differs_by_warehouse_is_still_a_conflict(tmp_path: Path, schema: TargetSchema) -> None:
    rows = [["A001", "สินค้า 1", 107, "BKK", 3], ["A001", "สินค้า 1", 110, "CNX", 1]]
    rows += [[f"A{i:03d}", f"สินค้า {i}", i * 107, wh, i] for i in range(2, 7) for wh in ("BKK", "CNX")]
    _, _, result = _propose(_per_warehouse(tmp_path, rows), schema)
    assert any("/prices" in m and "conflicting" in m for m in _errors(result)), _errors(result)


def test_a_column_no_collection_exports_still_needs_a_key(tmp_path: Path, schema: TargetSchema) -> None:
    header = [*HEADER, "คลัง", "Lot", "ยอดคงเหลือ"]
    rows = [[f"A{i:03d}", f"สินค้า {i}", i * 107, "BKK", lot, i] for i in range(1, 7) for lot in ("L1", "L2")]
    _, _, result = _propose(_per_warehouse(tmp_path, rows, header), schema)
    kinds = {f.kind for f in result.findings if f.severity == "error"}
    assert "source_identity" in kinds, [(f.kind, f.message) for f in result.findings]


def test_the_fill_survives_a_table_found_again_in_another_place(tmp_path: Path) -> None:
    path = _merged_sheet(tmp_path)
    spec = TableSpec(header_row=0, fill_merged=True)  # the header is not where the mapping says: found again
    frame = extract_table(read_raw(path, 0), spec, merges=vertical_merges(path, 0))
    assert isinstance(frame, pl.DataFrame) and frame.rows()[1][:2] == ("A1", "น้ำดื่ม")
