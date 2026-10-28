"""Table detection and extraction inside raw sheet grids (profiling/tables.py)."""

from __future__ import annotations

from collections.abc import Callable

import polars as pl
import pytest

from forma.mapping_dsl.models import TableSpec, Unpivot
from forma.profiling.tables import (
    dedupe_names,
    detect_tables,
    extract_table,
    merge_header_rows,
    normalize_header,
)

Grid = Callable[[list[list[object]]], pl.DataFrame]
N = None
HDR = ["รหัสสินค้า", "ชื่อสินค้า", "ราคา", "หน่วย"]
DATA: list[list[object]] = [[f"A{i:03d}", f"สินค้า {i}", f"{i * 10}.50", "ชิ้น"] for i in range(1, 9)]


def test_header_after_title_rows(grid: Grid) -> None:
    rows: list[list[object]] = [
        ["รายงานสินค้า"],
        ["Generated 2026-10-01"],
        [N],
        HDR,
        *DATA,
        ["รวม", N, "999", N],
    ]
    cands = detect_tables(grid(rows))
    assert len(cands) == 1
    t = cands[0]
    assert t.header_row == 3
    assert t.headers == HDR
    assert t.title == "รายงานสินค้า"
    assert t.data_start == 4
    assert t.first_column == 0 and t.last_column == 4
    assert t.to_spec().header_row == 3
    assert t.to_dict()["headers"] == HDR


def test_header_offset_blank_rows(grid: Grid) -> None:
    rows: list[list[object]] = [[N], [N], [N], HDR, *DATA]
    cands = detect_tables(grid(rows))
    assert [c.header_row for c in cands] == [3]
    assert cands[0].title is None


def test_two_stacked_tables(grid: Grid) -> None:
    second: list[list[object]] = [
        ["ช่องทาง", "คำอธิบาย"],
        ["ONLINE", "ขายออนไลน์"],
        ["STORE", "หน้าร้าน"],
        ["WEB", "เว็บ"],
    ]
    rows: list[list[object]] = [HDR, *DATA, [N], [N], *second]
    cands = detect_tables(grid(rows))
    assert len(cands) == 2
    # biggest table first
    assert cands[0].headers == HDR and cands[0].n_rows == 8
    assert cands[1].headers == ["ช่องทาง", "คำอธิบาย"] and cands[1].header_row == 11
    assert [c.index for c in cands] == [0, 1]


def test_side_by_side_tables(grid: Grid) -> None:
    rows: list[list[object]] = [["SKU", "Name", "Price", N, N, "WH", "Qty"]]
    rows += [[f"A{i}", f"n{i}", f"{i}.0", N, N, "BKK", str(i)] for i in range(1, 8)]
    cands = detect_tables(grid(rows))
    assert len(cands) == 2
    assert cands[0].headers == ["SKU", "Name", "Price"] and (cands[0].first_column, cands[0].last_column) == (
        0,
        3,
    )
    assert cands[1].headers == ["WH", "Qty"] and (cands[1].first_column, cands[1].last_column) == (5, 7)


def test_single_blank_column_does_not_split(grid: Grid) -> None:
    rows: list[list[object]] = [["SKU", "Name", N, "Price", "Unit"]]
    rows += [[f"A{i}", f"n{i}", N, f"{i}.0", "PCS"] for i in range(1, 8)]
    g = grid(rows)
    cands = detect_tables(g)
    assert len(cands) == 1
    assert cands[0].headers == ["SKU", "Name", "col_2", "Price", "Unit"]
    # the empty spacer column is dropped on extraction
    assert extract_table(g, TableSpec()).columns == ["SKU", "Name", "Price", "Unit"]


def test_empty_grid() -> None:
    assert detect_tables(pl.DataFrame()) == []
    assert extract_table(pl.DataFrame(), TableSpec()).shape == (0, 0)


def test_no_table_detected_raises(grid: Grid) -> None:
    with pytest.raises(ValueError, match="no table detected"):
        extract_table(grid([["only one cell"], [N]]), TableSpec())


def test_extract_drops_total_rows_th_and_en(grid: Grid) -> None:
    g = grid([["รายงาน"], [N], HDR, *DATA, ["รวม", N, "999", N], ["Total", N, "1", N]])
    f = extract_table(g, TableSpec())
    assert f.columns == HDR
    assert f.height == 8
    assert f["รหัสสินค้า"].to_list()[-1] == "A008"
    kept = extract_table(g, TableSpec(drop_total_rows=False))
    assert kept.height == 10


def test_fill_down(grid: Grid) -> None:
    rows: list[list[object]] = [
        ["Cat", "SKU", "Qty"],
        ["A", "x1", "1"],
        [N, "x2", "2"],
        ["B", "x3", "3"],
        [N, "x4", "4"],
    ]
    f = extract_table(grid(rows), TableSpec(header_row=0, fill_down=["Cat"]))
    assert f["Cat"].to_list() == ["A", "A", "B", "B"]
    f2 = extract_table(grid(rows), TableSpec(header_row=0))
    assert f2["Cat"].null_count() == 2


def test_unpivot(grid: Grid) -> None:
    rows: list[list[object]] = [["SKU", "Jan", "Feb"], ["a", "1", "2"], ["b", "3", "4"]]
    spec = TableSpec(
        header_row=0, unpivot=Unpivot(id_columns=["SKU"], variable_name="period", value_name="value")
    )
    f = extract_table(grid(rows), spec)
    assert f.columns == ["SKU", "period", "value"]
    assert f.height == 4
    assert sorted(zip(f["SKU"], f["period"], f["value"], strict=True)) == [
        ("a", "Feb", "2"),
        ("a", "Jan", "1"),
        ("b", "Feb", "4"),
        ("b", "Jan", "3"),
    ]
    spec_rx = TableSpec(header_row=0, unpivot=Unpivot(id_columns=["SKU"], value_columns_regex="^J"))
    f2 = extract_table(grid(rows), spec_rx)
    assert f2["variable"].unique().to_list() == ["Jan"]


def test_wrong_explicit_header_row_falls_back_to_detection(grid: Grid) -> None:
    g = grid([["รายงานสินค้า"], [N], HDR, *DATA])
    f = extract_table(g, TableSpec(header_row=0))
    assert f.columns == HDR
    assert f.height == 8


def test_rename_and_stop_at_blank_rows(grid: Grid) -> None:
    rows: list[list[object]] = [["SKU", "Name"], ["a", "1"], ["b", "2"], [N, N], [N, N], ["c", "3"]]
    f = extract_table(grid(rows), TableSpec(header_row=0, data_end=6), rename={"SKU": "sku", "missing": "x"})
    assert f.columns == ["sku", "Name"]
    assert f.height == 2
    f_all = extract_table(grid(rows), TableSpec(header_row=0, data_end=6, stop_at_blank_rows=0))
    assert f_all.height == 3


def test_header_row_beyond_grid_returns_empty(grid: Grid) -> None:
    assert extract_table(grid([HDR, *DATA]), TableSpec(header_row=50)).shape == (0, 0)


def test_merge_header_rows_and_helpers() -> None:
    merged = merge_header_rows([["Price", None, "Stock", None], ["Retail", "Wholesale", "BKK", "CNX"]])
    assert merged == ["Price Retail", "Price Wholesale", "Stock BKK", "Stock CNX"]
    assert merge_header_rows([["a", None, "a"]]) == ["a", "col_1", "a_2"]
    assert dedupe_names(["x", "X", "y"]) == ["x", "X_2", "y"]
    assert normalize_header("  Item \n Code ") == "Item Code"
    assert normalize_header("") == "col"


def test_explicit_two_header_rows(grid: Grid) -> None:
    rows: list[list[object]] = [
        ["Item", "Price", N, "Stock", N],
        ["Code", "Retail", "Wholesale", "BKK", "CNX"],
        ["A1", "10", "9", "1", "2"],
        ["A2", "11", "8", "3", "4"],
        ["A3", "12", "7", "5", "6"],
    ]
    f = extract_table(grid(rows), TableSpec(header_row=0, header_rows=2))
    assert f.columns == ["Item Code", "Price Retail", "Price Wholesale", "Stock BKK", "Stock CNX"]
    assert f.height == 3
