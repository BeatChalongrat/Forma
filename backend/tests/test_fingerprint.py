"""Structural fingerprints + drift classification."""

from __future__ import annotations

from pathlib import Path

from forma.fingerprint.fingerprint import (
    Fingerprint,
    compare,
    fingerprint_profile,
    merge_fingerprints,
    norm_header,
    norm_sheet,
)
from forma.ingestion.readers import SheetMeta, WorkbookMeta
from forma.profiling.profile import ColumnProfile, TableProfile, WorkbookProfile
from forma.profiling.tables import TableCandidate


def _col(name: str, typ: str = "string", i: int = 0) -> ColumnProfile:
    return ColumnProfile(
        name=name,
        position=i,
        inferred_type=typ,
        null_ratio=0.0,
        unique_ratio=1.0,
        unique_count=10,
        samples=["a"],
    )


def _profile(
    sheets: dict[str, list[tuple[str, str]]], *, header_row: int = 0, n_rows: int = 120
) -> WorkbookProfile:
    metas = [SheetMeta(name=n, index=i) for i, n in enumerate(sheets)]
    tables = []
    for i, (name, cols) in enumerate(sheets.items()):
        cand = TableCandidate(
            0,
            header_row,
            1,
            header_row + 1,
            header_row + 1 + n_rows,
            0,
            len(cols),
            [c for c, _ in cols],
            n_rows,
            0.9,
        )
        tables.append(TableProfile(name, i, cand, [_col(c, t, j) for j, (c, t) in enumerate(cols)], n_rows))
    return WorkbookProfile(WorkbookMeta(Path("f.xlsx"), "excel", metas), tables)


PRODUCTS = [
    ("รหัสสินค้า", "string"),
    ("ชื่อสินค้า", "string"),
    ("ราคาขาย", "float"),
    ("หน่วย", "string"),
    ("สถานะ", "string"),
]
STOCK = [("รหัสสินค้า", "string"), ("คลัง", "string"), ("ยอดคงเหลือ", "int")]


def test_norm_helpers() -> None:
    assert norm_sheet("stock_20261001") == "stock"
    assert norm_sheet("สินค้า ๒๕๖๙") == "สินค้า"
    assert norm_sheet("2026") == "sheet"
    assert norm_header(" Item_Code (SKU) ") == "item code sku"


def test_same_profile_twice_identical() -> None:
    a = fingerprint_profile(_profile({"สินค้า_1001": PRODUCTS, "Stock": STOCK}))
    b = fingerprint_profile(_profile({"สินค้า_1015": PRODUCTS, "Stock": STOCK}, n_rows=400))
    assert a.strict == b.strict and a.loose == b.loose
    assert a.kind == "excel"
    assert [t.sheet for t in a.tables] == ["สินค้า", "stock"]
    rep = compare(a, b)
    assert rep.is_identical and rep.similarity == 1.0 and rep.to_dict()["classification"] == "identical"
    # round trip through dict
    assert Fingerprint.from_dict(a.to_dict()).strict == a.strict
    assert set(a.all_headers()) == {norm_header(c) for c, _ in PRODUCTS + STOCK}


def test_noise_tables_ignored() -> None:
    fp = fingerprint_profile(
        _profile({"สินค้า": PRODUCTS, "legend": [("a", "string"), ("b", "string")]}, n_rows=2)
    )
    assert fp.tables == []


def test_renamed_column() -> None:
    prev = fingerprint_profile(_profile({"สินค้า": PRODUCTS, "Stock": STOCK}))
    renamed = [(c if c != "ยอดคงเหลือ" else "จำนวนคงเหลือ", t) for c, t in STOCK]
    cur = fingerprint_profile(_profile({"สินค้า": PRODUCTS, "Stock": renamed}))
    rep = compare(prev, cur)
    assert rep.classification == "columns_renamed"
    assert rep.renamed == {"ยอดคงเหลือ": "จำนวนคงเหลือ"}
    assert rep.removed == [] and rep.added == []
    assert 0.5 < rep.similarity < 1.0
    assert rep.changes == [{"kind": "columns_renamed", "from": "ยอดคงเหลือ", "to": "จำนวนคงเหลือ"}]

    # English renames that share tokens → paired as renames
    prev_en = fingerprint_profile(
        _profile(
            {
                "Products": [
                    ("SKU", "string"),
                    ("Product Name", "string"),
                    ("Sales Price", "float"),
                    ("Quantity On Hand", "int"),
                ]
            }
        )
    )
    cur_en = fingerprint_profile(
        _profile(
            {
                "Products": [
                    ("SKU", "string"),
                    ("Product Name", "string"),
                    ("List Price", "float"),
                    ("Qty On Hand", "int"),
                ]
            }
        )
    )
    rep_en = compare(prev_en, cur_en)
    assert rep_en.classification == "columns_renamed"
    assert rep_en.renamed == {"quantity on hand": "qty on hand", "sales price": "list price"}
    assert {c["kind"] for c in rep_en.changes} == {"columns_renamed"}

    # a rename with nothing in common is reported as removed + added
    cur_far = fingerprint_profile(
        _profile(
            {"สินค้า": PRODUCTS, "Stock": [("รหัสสินค้า", "string"), ("คลัง", "string"), ("Available", "int")]}
        )
    )
    rep_far = compare(prev, cur_far)
    assert rep_far.classification == "columns_removed"
    assert rep_far.removed == ["ยอดคงเหลือ"] and rep_far.added == ["available"]


def test_columns_added_removed_and_header_moved() -> None:
    prev = fingerprint_profile(_profile({"สินค้า": PRODUCTS}))
    cur = fingerprint_profile(_profile({"สินค้า": [*PRODUCTS, ("Barcode", "string")]}))
    rep = compare(prev, cur)
    assert rep.classification == "columns_added" and rep.added == ["barcode"]

    cur2 = fingerprint_profile(_profile({"สินค้า": PRODUCTS[:-1]}))
    rep2 = compare(prev, cur2)
    assert rep2.classification == "columns_removed" and rep2.removed == ["สถานะ"]

    # the strict hash covers sheet names + headers only: a header that moves down is still identical
    moved = fingerprint_profile(_profile({"สินค้า": PRODUCTS}, header_row=3))
    assert compare(prev, moved).classification == "identical"
    # header moved + a new column → the move is reported alongside the addition
    moved_added = fingerprint_profile(_profile({"สินค้า": [*PRODUCTS, ("Barcode", "string")]}, header_row=3))
    rep3 = compare(prev, moved_added)
    assert rep3.classification == "columns_added"
    assert {"kind": "header_moved", "sheet": "สินค้า", "from": 0, "to": 3} in rep3.changes

    # same headers under a different sheet name → sheets_added/removed changes, still related
    other_sheet = fingerprint_profile(_profile({"Products": PRODUCTS}))
    rep4 = compare(prev, other_sheet)
    assert rep4.classification == "sheets_removed"
    assert rep4.similarity == 1.0


def test_types_changed_and_sheets() -> None:
    prev = fingerprint_profile(_profile({"สินค้า": PRODUCTS, "Stock": STOCK}))
    retyped = [(c, "string" if c == "ราคาขาย" else t) for c, t in PRODUCTS]
    # types are not part of the strict hash, but a retyped column is still drift: no verbatim reuse
    same_headers = fingerprint_profile(_profile({"สินค้า": retyped, "Stock": STOCK}))
    assert same_headers.strict == prev.strict
    rep0 = compare(prev, same_headers)
    assert rep0.classification == "types_changed" and not rep0.is_identical
    assert rep0.changes == [{"kind": "types_changed", "header": "ราคาขาย", "from": "float", "to": "string"}]
    # whole-baht prices one round and satang the next is the same data, as is a column left empty
    noise = [(c, "int" if c == "ราคาขาย" else "empty" if c == "สถานะ" else t) for c, t in PRODUCTS]
    assert compare(prev, fingerprint_profile(_profile({"สินค้า": noise, "Stock": STOCK}))).is_identical
    # once headers differ too the type change is reported and outranks columns_added
    cur = fingerprint_profile(_profile({"สินค้า": [*retyped, ("Barcode", "string")], "Stock": STOCK}))
    rep = compare(prev, cur)
    assert rep.classification == "types_changed"
    assert {"kind": "types_changed", "header": "ราคาขาย", "from": "float", "to": "string"} in rep.changes
    assert {"kind": "columns_added", "header": "barcode"} in rep.changes

    fewer = fingerprint_profile(_profile({"สินค้า": PRODUCTS}))
    rep2 = compare(prev, fewer)
    assert rep2.classification == "sheets_removed"
    rep3 = compare(fewer, prev)
    assert rep3.classification == "sheets_added"


def test_unrelated_file() -> None:
    prev = fingerprint_profile(_profile({"สินค้า": PRODUCTS, "Stock": STOCK}))
    cur = fingerprint_profile(
        _profile(
            {
                "MARA": [
                    ("MATNR", "string"),
                    ("MAKTX", "string"),
                    ("MATKL", "string"),
                    ("MEINS", "string"),
                    ("KBETR", "float"),
                ]
            }
        )
    )
    rep = compare(prev, cur)
    assert rep.classification == "unrelated_file"
    assert rep.similarity < 0.35
    # a shared satellite sheet must not make an unrelated main table look related
    cur2 = fingerprint_profile(
        _profile(
            {
                "MARA": [("MATNR", "string"), ("MAKTX", "string"), ("MATKL", "string"), ("MEINS", "string")],
                "Stock": STOCK,
            }
        )
    )
    assert compare(prev, cur2).classification == "unrelated_file"


def test_merge_fingerprints() -> None:
    a = fingerprint_profile(_profile({"สินค้า": PRODUCTS}))
    b = fingerprint_profile(_profile({"Stock": STOCK}))
    b.kind = "csv"
    merged = merge_fingerprints([a, b])
    both = fingerprint_profile(_profile({"สินค้า": PRODUCTS, "Stock": STOCK}))
    assert merged.strict == both.strict and merged.loose == both.loose
    assert merged.kind == "excel" and merge_fingerprints([b]).kind == "csv"
    assert len(merged.tables) == 2
    assert compare(merged, both).is_identical
