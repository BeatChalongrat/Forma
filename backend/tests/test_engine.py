"""TransformEngine end to end: messy Thai workbook + hand-written MappingDocument → nested records."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import openpyxl
import polars as pl
import pytest

from forma.engine.ops import CompileContext, CompileError, compile_expr, python_value
from forma.engine.transform import (
    BoundSource,
    TransformEngine,
    _get_pointer,
    _set_pointer,
    iter_jsonl,
    load_source_frame,
)
from forma.export.exporter import export_bytes, export_file, flatten_record
from forma.mapping_dsl.models import (
    CollectionMapping,
    FieldMapping,
    JoinKey,
    JoinSpec,
    MappingDocument,
    MappingMeta,
    QualityRule,
    RecordSpec,
    SheetSelect,
    SourceSpec,
    TableSpec,
    call,
    col,
    const,
)
from forma.mapping_dsl.validate import validate_mapping
from forma.schema_registry import TargetSchema

PRODUCTS_HDR = ["รหัสสินค้า", "ชื่อสินค้า", "หมวด", "ราคาขาย (รวม VAT)", "หน่วย", "สถานะ", "ยี่ห้อ", "หมายเหตุ"]


@pytest.fixture
def workbook(tmp_path: Path) -> Path:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)  # type: ignore[arg-type]
    ws = wb.create_sheet("สินค้า")
    ws.append(["รายงานสินค้า ประจำเดือน ต.ค. 2569"])
    ws.merge_cells("A1:D1")
    ws.append([None])
    ws.append(PRODUCTS_HDR)
    ws.append(["00123", "น้ำดื่ม  500ml", "เครื่องดื่ม", "฿ 1,070.00", "ลัง(12)", "ขาย", "Siam", "x"])
    ws.append(["A002", "ชาเขียว 1L", "เครื่องดื่ม", "๕๓.๕๐", "ชิ้น", "เลิกขาย", None, None])
    ws.append(["A003", "มันฝรั่งทอด", "ขนมขบเคี้ยว", "214", "แพ็ค 6", "ไม่ขาย", "Nara", None])
    ws.append(["A003", "มันฝรั่งทอด (ซ้ำ)", "ขนมขบเคี้ยว", "214", "แพ็ค 6", "ขาย", "Nara", None])
    ws.append(["A004", "ผงซักฟอก", "ของใช้ในบ้าน", None, "CTN 24", "ขาย", None, None])
    ws.append(["รวม", None, None, "1,551.50", None, None, None, None])
    st = wb.create_sheet("Stock")
    st.append(["รหัสสินค้า", "คลัง", "คงเหลือ"])
    st.append(["123", "BKK", "10"])
    st.append(["A002", "BKK", "๒๐"])
    st.append(["A002", "CNX", "5"])
    st.append(["ZZZ", "BKK", "1"])  # orphan: no such product
    pr = wb.create_sheet("Promotions")
    pr.append(["โปรโมชั่น Q4"])
    pr.append([None])
    pr.append(["รหัสสินค้า", "รหัสโปร", "ประเภทโปร", "ส่วนลด %", "เริ่ม", "สิ้นสุด"])
    pr.append(["A002", "PR1", "ลด%", "10%", "1/10/2569", "31 ต.ค. 69"])
    pr.append(["A003", "PR2", "ซื้อแถม", None, "2569-10-05", "15/10/69"])
    cat = wb.create_sheet("Categories")
    cat.append(["หมวด", "Category EN"])
    cat.append(["เครื่องดื่ม", "Beverages"])
    cat.append(["ขนมขบเคี้ยว", "Snacks"])
    p = tmp_path / "สินค้า_ตค69.xlsx"
    wb.save(p)
    return p


def _mapping() -> MappingDocument:
    return MappingDocument(
        meta=MappingMeta(target_schema="product@1", confidence=0.9),
        sources=[
            SourceSpec(id="products", role="products", sheet=SheetSelect(name="สินค้า")),
            SourceSpec(id="stock", role="inventory", sheet=SheetSelect(match="^stock$")),
            SourceSpec(id="promo", role="promotions", sheet=SheetSelect(index=2)),
            SourceSpec(id="cats", role="categories", sheet=SheetSelect(name="Categories")),
        ],
        joins=[
            JoinSpec(
                left="products", right="cats", on=[JoinKey(left="หมวด", right="หมวด")], cardinality="N:1"
            )
        ],
        record=RecordSpec(root_source="products"),
        fields=[
            FieldMapping(target="/sku", expr=call("strip_leading_zeros", call("trim", col("รหัสสินค้า")))),
            FieldMapping(target="/product_name", expr=call("normalize_whitespace", col("ชื่อสินค้า"))),
            FieldMapping(target="/brand", expr=call("coalesce", col("ยี่ห้อ"), const("Unknown"))),
            FieldMapping(target="/category", expr=call("to_list", col("หมวด"), col("Category EN", "cats"))),
            FieldMapping(target="/unit/sell_unit", expr=call("unit_normalize", col("หน่วย"))),
            FieldMapping(target="/unit/pack_size", expr=call("pack_size", col("หน่วย"))),
            FieldMapping(target="/currency", expr=const("THB")),
            FieldMapping(
                target="/status",
                expr=call(
                    "map_values",
                    col("สถานะ"),
                    mapping={"ขาย": "active", "ไม่ขาย": "inactive", "เลิกขาย": "discontinued"},
                    fuzzy=True,
                    default="active",
                ),
            ),
            FieldMapping(
                target="/description",
                expr=call("conditional", call("is_null", col("หมายเหตุ")), const("no note"), col("หมายเหตุ")),
            ),
        ],
        collections=[
            CollectionMapping(
                target="/prices",
                source="products",
                link=[JoinKey(left="รหัสสินค้า", right="รหัสสินค้า")],
                filter=call("not_null", call("parse_number_th", col("ราคาขาย (รวม VAT)"))),
                fields=[
                    FieldMapping(target="/prices/*/price_list", expr=const("standard")),
                    FieldMapping(
                        target="/prices/*/amount",
                        expr=call(
                            "round", call("vat_adjust", col("ราคาขาย (รวม VAT)"), mode="remove"), decimals=2
                        ),
                    ),
                    FieldMapping(target="/prices/*/vat_included", expr=const(False)),
                ],
            ),
            CollectionMapping(
                target="/inventory",
                source="stock",
                link=[JoinKey(left="รหัสสินค้า", right="รหัสสินค้า")],
                normalize_keys=["trim", "upper", "strip_leading_zeros"],
                fields=[
                    FieldMapping(target="/inventory/*/warehouse_code", expr=col("คลัง", "stock")),
                    FieldMapping(
                        target="/inventory/*/qty", expr=call("parse_number_th", col("คงเหลือ", "stock"))
                    ),
                ],
            ),
            CollectionMapping(
                target="/promotions",
                source="promo",
                link=[JoinKey(left="รหัสสินค้า", right="รหัสสินค้า")],
                fields=[
                    FieldMapping(target="/promotions/*/promo_code", expr=col("รหัสโปร", "promo")),
                    FieldMapping(
                        target="/promotions/*/type",
                        expr=call(
                            "map_values",
                            col("ประเภทโปร", "promo"),
                            mapping={"ลด%": "percent_off", "ซื้อแถม": "buy_x_get_y"},
                        ),
                    ),
                    FieldMapping(
                        target="/promotions/*/discount_percent",
                        expr=call("parse_number_th", col("ส่วนลด %", "promo")),
                    ),
                    FieldMapping(
                        target="/promotions/*/start_date", expr=call("parse_date_th", col("เริ่ม", "promo"))
                    ),
                    FieldMapping(
                        target="/promotions/*/end_date", expr=call("parse_date_th", col("สิ้นสุด", "promo"))
                    ),
                ],
            ),
        ],
        quality=[
            QualityRule(target="/sku", rule="unique", severity="warning"),
            QualityRule(target="/product_name", rule="not_null"),
            QualityRule(target="/product_name", rule="max_length", params={"value": 5}, severity="warning"),
        ],
    )


def _bind(doc: MappingDocument, path: Path) -> dict[str, BoundSource]:
    return {s.id: BoundSource(path, file_name=path.name) for s in doc.sources}


def test_end_to_end(schema: TargetSchema, workbook: Path) -> None:
    doc = _mapping()
    assert validate_mapping(doc, schema).ok
    res = TransformEngine(schema, provenance_meta={"customer_id": "c1"}).run(doc, _bind(doc, workbook))

    assert res.stats["rows_in"] == 5  # totals row dropped, title rows skipped
    assert res.stats["rows_out"] == 4  # duplicate A003 collapsed
    assert res.stats["identity_keys"] == ["/sku"]
    kinds = {(f.kind, f.severity) for f in res.findings}
    assert ("dedupe", "warning") in kinds
    assert ("join", "warning") in kinds
    assert not [f for f in res.findings if f.kind == "validation"], [f.message for f in res.findings]
    assert res.errors == []
    dedupe = next(f for f in res.findings if f.kind == "dedupe" and f.severity == "warning")
    assert dedupe.details["sample"] == ["A003"]
    orphan = next(f for f in res.findings if f.kind == "join" and f.target == "/inventory")
    assert orphan.details["orphans"] == 1
    quality = next(f for f in res.findings if f.kind == "quality")
    assert quality.target == "/product_name" and quality.details["rule"] == "max_length"
    assert res.summary()["findings"]["error"] == 0

    by_sku = {r["sku"]: r for r in res.records}
    assert set(by_sku) == {"123", "A002", "A003", "A004"}  # leading zeros stripped
    r1 = by_sku["123"]
    assert r1["product_name"] == "น้ำดื่ม 500ml"  # whitespace normalised
    assert r1["brand"] == "Siam" and by_sku["A002"]["brand"] == "Unknown"  # coalesce
    assert r1["category"] == ["เครื่องดื่ม", "Beverages"]  # joined lookup table
    assert by_sku["A004"]["category"] == ["ของใช้ในบ้าน"]  # unmatched join → nulls dropped from list
    assert r1["unit"] == {"sell_unit": "CARTON", "pack_size": 12}
    assert by_sku["A004"]["unit"] == {"sell_unit": "CARTON", "pack_size": 24}
    assert by_sku["A002"]["unit"]["sell_unit"] == "PCS" and by_sku["A002"]["unit"]["pack_size"] is None
    assert r1["currency"] == "THB"
    assert r1["description"] == "x" and by_sku["A002"]["description"] == "no note"  # conditional
    assert [by_sku[k]["status"] for k in ("123", "A002", "A003")] == ["active", "discontinued", "inactive"]
    # vat_adjust: 1,070 incl. VAT → 1000 ex VAT ; Thai digits ๕๓.๕๐ → 50.0
    assert r1["prices"] == [
        {"price_list": "standard", "amount": 1000.0, "vat_included": False, "vat_rate": 0.07}
    ]
    assert by_sku["A002"]["prices"][0]["amount"] == 50.0
    assert "prices" not in by_sku["A004"]  # filtered out (no price)
    # inventory linked through strip_leading_zeros ("123" ↔ "00123"); Thai digit quantities parsed
    assert r1["inventory"] == [{"warehouse_code": "BKK", "qty": 10.0}]
    assert sorted(i["qty"] for i in by_sku["A002"]["inventory"]) == [5.0, 20.0]
    # promotions: BE dates → ISO strings, map_values exact
    promo = by_sku["A002"]["promotions"][0]
    assert promo == {
        "promo_code": "PR1",
        "type": "percent_off",
        "discount_percent": 10.0,
        "start_date": "2026-10-01",
        "end_date": "2026-10-31",
    }
    assert by_sku["A003"]["promotions"][0]["type"] == "buy_x_get_y"
    assert by_sku["A003"]["promotions"][0]["start_date"] == "2026-10-05"
    assert by_sku["A003"]["promotions"][0]["end_date"] == "2026-10-15"
    # provenance
    assert r1["source_meta"]["customer_id"] == "c1"
    assert r1["source_meta"]["file"] == workbook.name
    # lineage names the source row of the record, of each collection item and of each joined lookup row
    assert r1["source_meta"]["lineage"] == {
        "root_row": 0,
        "source": "products",
        "items": {"/inventory": [0], "/prices": [0]},
        "joined": {"cats": 0},
    }
    # the dedupe kept the first row: A003 is 'inactive', not 'active'
    assert by_sku["A003"]["product_name"] == "มันฝรั่งทอด"
    # attributes passthrough: unmapped columns are kept
    assert "attributes" not in r1 or isinstance(r1["attributes"], dict)
    # root frame carries target columns
    assert "/sku" in res.root_frame.columns
    assert schema.validate_records(res.records) == []


def test_export_formats(schema: TargetSchema, workbook: Path, tmp_path: Path) -> None:
    doc = _mapping()
    res = TransformEngine(schema).run(doc, _bind(doc, workbook))
    records = res.records

    as_json = json.loads(export_bytes(records, "json", schema))
    assert [r["sku"] for r in as_json] == ["123", "A002", "A003", "A004"]

    lines = export_bytes(records, "jsonl", schema).splitlines()
    assert len(lines) == 4 and json.loads(lines[1])["sku"] == "A002"
    assert b"".join(iter_jsonl(records)) == export_bytes(records, "jsonl", schema)

    raw = export_bytes(records, "csv", schema)
    assert raw.startswith("﻿".encode())  # BOM for Excel
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    assert len(rows) == 4  # one price per record → one row each
    first = rows[0]
    assert first["sku"] == "123"
    assert first["prices.amount"] == "1000.0"  # prices exploded into columns
    assert first["unit.sell_unit"] == "CARTON"
    assert first["category"] == "เครื่องดื่ม|Beverages"
    assert "source_meta" not in " ".join(first)
    assert json.loads(first["inventory"])["warehouse_code"] == "BKK"
    assert rows[3]["prices.amount"] == ""  # A004 has no price → no exploded values

    out = export_file(records, "csv", schema, tmp_path / "out" / "x.csv")
    assert out.exists() and not (tmp_path / "out" / "x.csv.tmp").exists()
    with pytest.raises(ValueError, match="unsupported"):
        export_bytes(records, "xml", schema)


def test_flatten_record_explodes_collection() -> None:
    rec = {
        "sku": "a",
        "unit": {"sell_unit": "PCS"},
        "prices": [{"amount": 1}, {"amount": 2}],
        "tags": ["x", "y"],
        "source_meta": {"z": 1},
    }
    rows = list(flatten_record(rec, explode="/prices"))
    assert [r["prices.amount"] for r in rows] == [1, 2]
    assert rows[0]["unit.sell_unit"] == "PCS" and rows[0]["tags"] == "x|y"
    assert "source_meta" not in rows[0]
    assert list(flatten_record({"sku": "b"}, explode="/prices")) == [{"sku": "b"}]


def test_limit_and_record_filter(schema: TargetSchema, workbook: Path) -> None:
    doc = _mapping()
    doc.record.filters = [call("neq", col("หมวด"), const("ของใช้ในบ้าน"))]
    res = TransformEngine(schema).run(doc, _bind(doc, workbook))
    assert {r["sku"] for r in res.records} == {"123", "A002", "A003"}
    res2 = TransformEngine(schema).run(doc, _bind(doc, workbook), limit=2)
    assert res2.stats["rows_in"] == 2


def test_unbound_and_missing_sources(schema: TargetSchema, workbook: Path) -> None:
    doc = _mapping()
    bindings = _bind(doc, workbook)
    del bindings["products"]
    res = TransformEngine(schema).run(doc, bindings)
    assert res.records == [] and any("not bound" in f.message for f in res.errors)

    doc2 = _mapping()
    doc2.sources[1].sheet = SheetSelect(match="^nothing-here$")
    res2 = TransformEngine(schema).run(doc2, _bind(doc2, workbook))
    assert any("no sheet matches" in f.message for f in res2.errors)


def test_missing_column_warning_and_dedupe_error(schema: TargetSchema, workbook: Path) -> None:
    doc = _mapping()
    doc.fields.append(FieldMapping(target="/barcode", expr=col("ไม่มีคอลัมน์นี้")))
    doc.record.dedupe.keep = "error"
    res = TransformEngine(schema).run(doc, _bind(doc, workbook))
    assert any(f.kind == "compile" and "not found" in f.message for f in res.findings)
    assert any(f.kind == "dedupe" and f.severity == "error" for f in res.findings)
    assert all(r.get("barcode") is None for r in res.records)  # nullable field → explicit null


def test_header_drift_recovers_from_wrong_header_row(schema: TargetSchema, workbook: Path) -> None:
    doc = _mapping()
    # mapping says the header is on row 0, but the file has two title rows above it
    doc.sources[0].table = TableSpec(header_row=0)
    res = TransformEngine(schema).run(doc, _bind(doc, workbook))
    assert res.stats["rows_out"] == 4
    frame = load_source_frame(doc.sources[0], BoundSource(workbook), expected_columns=set(PRODUCTS_HDR))
    assert set(PRODUCTS_HDR) <= set(frame.columns)


def test_join_findings(schema: TargetSchema, workbook: Path) -> None:
    doc = _mapping()
    doc.joins[0].on_unmatched = "drop"
    res = TransformEngine(schema).run(doc, _bind(doc, workbook))
    assert {r["sku"] for r in res.records} == {"123", "A002", "A003"}  # A004 has no category match
    doc.joins[0].on_unmatched = "error"
    res = TransformEngine(schema).run(doc, _bind(doc, workbook))
    assert any(f.kind == "join" and f.severity == "error" for f in res.findings)
    doc.joins[0].on = [JoinKey(left="nope", right="หมวด")]
    res = TransformEngine(schema).run(doc, _bind(doc, workbook))
    assert any("missing on left side" in f.message for f in res.findings)
    # duplicated right keys with 1:1 cardinality → warning
    doc = _mapping()
    doc.joins = [JoinSpec(left="products", right="stock", on=[JoinKey(left="รหัสสินค้า", right="รหัสสินค้า")])]
    res = TransformEngine(schema).run(doc, _bind(doc, workbook))
    assert any("duplicated but cardinality" in f.message for f in res.findings)


def test_quality_rules(schema: TargetSchema, workbook: Path) -> None:
    doc = _mapping()
    doc.quality = [
        QualityRule(target="/sku", rule="regex", params={"pattern": "^A"}, severity="warning"),
        QualityRule(target="/status", rule="enum", params={"values": ["active"]}, severity="warning"),
        QualityRule(target="/brand", rule="not_null"),
        QualityRule(
            target="/unit/pack_size", rule="range", params={"min": 10, "max": 20}, severity="warning"
        ),
        QualityRule(target="/unit/pack_size", rule="positive", severity="warning"),
        QualityRule(target="/description", rule="date_order", params={"after": "/brand"}, severity="warning"),
        QualityRule(target="/nope", rule="not_null"),
    ]
    res = TransformEngine(schema).run(doc, _bind(doc, workbook))
    q = {(f.target, f.details["rule"]): f for f in res.findings if f.kind == "quality"}
    assert q[("/sku", "regex")].details["count"] == 1
    assert q[("/status", "enum")].details["count"] == 2
    assert ("/brand", "not_null") not in q  # coalesce filled every brand
    assert q[("/unit/pack_size", "range")].details["count"] == 2  # 6 and 24 outside 10..20
    assert ("/unit/pack_size", "positive") not in q
    assert q[("/sku", "regex")].details["sample"][0]["identity"] == "123"


def test_ops_compile_coverage(schema: TargetSchema) -> None:
    df = pl.DataFrame(
        {
            "s.a": ["  Hello World ", "x", None, "12.5%"],
            "s.b": ["1,000", "(50)", "๑๐", "abc"],
            "s.c": ["a, b ,,c", "", "d", None],
            "s.d": ["ขาย", "no", "Y", None],
        }
    )
    ctx = CompileContext(default_source="s", columns=set(df.columns), lookups={"lk": {"x": "ex"}})
    exprs = {
        "upper": call("upper", col("a")),
        "lower": call("lower", col("a")),
        "cast_int": call("cast", col("b"), to="int"),
        "cast_float": call("cast", col("b"), to="float"),
        "cast_str": call("cast", col("b"), to="string"),
        "cast_bool": call("cast", col("d"), to="bool"),
        "cast_date": call("cast", const("15/10/2569"), to="date"),
        "regex_extract": call("regex_extract", col("a"), pattern=r"(\d+)"),
        "regex_replace": call("regex_replace", col("a"), pattern=r"\s+", replacement="_"),
        "split": call("split", col("c"), separator=","),
        "split_idx": call("split", col("c"), separator=",", index=1),
        "lookup_dict": call("lookup", col("a"), table={"x": "ex"}, default="?"),
        "lookup_src": call("lookup", col("a"), source="lk"),
        "pct": call("parse_number_th", col("a"), percent_to_fraction=True),
        "bool": call("parse_bool", col("d")),
        "vat_add": call("vat_adjust", col("b"), mode="add"),
        "add": call("add", col("b"), const(1), const(1)),
        "sub": call("subtract", col("b"), const(1)),
        "mul": call("multiply", col("b"), const(2)),
        "div": call("divide", col("b"), const(0)),
        "div2": call("divide", col("b"), const(2)),
        "round": call("round", const(1.2345), decimals=2),
        "default": call("default", col("a"), value="dflt"),
        "null_if": call("null_if", col("a"), values=["x"]),
        "eq": call("eq", col("a"), const("X")),
        "neq": call("neq", col("a"), const("X")),
        "gt": call("gt", col("b"), const(0)),
        "gte": call("gte", col("b"), const(10)),
        "lt": call("lt", col("b"), const(0)),
        "lte": call("lte", col("b"), const(-50)),
        "is_null": call("is_null", col("a")),
        "not_null": call("not_null", col("a")),
        "and": call("and", call("not_null", col("a")), call("gt", col("b"), const(0))),
        "or": call("or", call("is_null", col("a")), call("lt", col("b"), const(0))),
        "not": call("not", call("is_null", col("a"))),
        "contains": call("contains", col("a"), pattern="hello", literal=True),
        "contains_rx": call("contains", col("a"), pattern="(?i)hello"),
        "starts": call("starts_with", col("a"), pattern="x"),
        "in": call("in", col("d"), values=["ขาย", "y"]),
        "concat": call("concat", col("a"), col("b"), separator="-"),
        "map_keep": call("map_values", col("d"), mapping={"ขาย": "active"}, keep_unmapped=True),
        "map_int": call("map_values", col("d"), mapping={"ขาย": 1, "no": 0}),
        "to_list": call("to_list", col("a"), col("d")),
        "const_num": const(5),
    }
    out = df.with_columns([compile_expr(e, ctx).alias(k) for k, e in exprs.items()])
    row0 = out.row(0, named=True)
    assert row0["upper"] == "  HELLO WORLD " and row0["lower"].strip() == "hello world"
    assert out["cast_int"].to_list() == [1000, -50, 10, None]
    assert out["cast_float"].to_list() == [1000.0, -50.0, 10.0, None]
    assert out["cast_bool"].to_list() == [True, False, True, None]
    assert str(out["cast_date"][0]) == "2026-10-15"
    assert out["regex_extract"].to_list()[3] == "12"
    assert row0["regex_replace"] == "_Hello_World_"
    assert out["split"].to_list()[0] == ["a", "b", "c"] and out["split_idx"].to_list()[0] == "b"
    assert out["lookup_dict"].to_list()[1] == "ex" and out["lookup_dict"].to_list()[0] == "?"
    assert out["lookup_src"].to_list()[1] == "ex"
    assert out["pct"].to_list()[3] == 0.125
    assert out["bool"].to_list() == [True, False, True, None]
    assert out["vat_add"].to_list()[0] == 1070.0
    assert (
        out["add"].to_list()[0] == 1002.0
        and out["sub"].to_list()[1] == -51.0
        and out["mul"].to_list()[2] == 20.0
    )
    assert out["div"].to_list()[0] is None and out["div2"].to_list()[0] == 500.0
    assert out["round"].to_list()[0] == 1.23
    assert out["default"].to_list()[2] == "dflt"
    assert out["null_if"].to_list()[1] is None and out["null_if"].to_list()[0] is not None
    assert out["eq"].to_list() == [False, True, False, False]
    assert out["neq"].to_list() == [True, False, False, True]
    assert out["gt"].to_list() == [True, False, True, None]  # non-numeric compares to null
    assert (
        out["gte"].to_list()[2] is True and out["lt"].to_list()[1] is True and out["lte"].to_list()[1] is True
    )
    assert out["is_null"].to_list() == [False, False, True, False]
    assert out["and"].to_list() == [True, False, False, False]
    assert out["or"].to_list() == [False, True, True, False]
    assert out["not"].to_list()[2] is False
    assert out["contains"].to_list()[0] is False and out["contains_rx"].to_list()[0] is True
    assert out["starts"].to_list() == [False, True, False, False]
    assert out["in"].to_list() == [True, False, True, False]
    assert row0["concat"] == "  Hello World -1,000"
    assert out["map_keep"].to_list() == ["active", "no", "Y", None]
    assert out["map_int"].to_list() == [1, 0, None, None]
    assert out["to_list"].to_list()[2] == ["Y"]
    assert out["const_num"].to_list()[0] == 5
    # tolerant column resolution + missing columns
    assert ctx.physical(col("A")) == "s.a"
    ctx2 = CompileContext(default_source="s", columns={"s.a"})
    assert compile_expr(col("zzz"), ctx2) is not None and ctx2.missing == ["s.zzz"]
    with pytest.raises(CompileError):
        compile_expr(call("cast", col("a"), to="datetime"), ctx)
    with pytest.raises(CompileError):
        compile_expr(call("lookup", col("a"), source="none"), ctx)
    with pytest.raises(CompileError):
        compile_expr("not an expr", ctx)  # type: ignore[arg-type]


def test_pointer_helpers_and_python_value() -> None:
    import datetime as dt

    rec: dict[str, object] = {}
    _set_pointer(rec, "/unit/sell_unit", "PCS")
    _set_pointer(rec, "/prices", [{"amount": 1}])
    _set_pointer(rec, "/prices/0/amount", 2)
    assert rec == {"unit": {"sell_unit": "PCS"}, "prices": [{"amount": 2}]}
    assert _get_pointer(rec, "/prices/0/amount") == 2
    assert _get_pointer(rec, "/prices/9/amount") is None
    assert _get_pointer(rec, "/prices/x") is None
    assert _get_pointer(rec, "/unit/sell_unit/deeper") is None
    assert python_value(dt.datetime(2026, 1, 2, 3)) == "2026-01-02"
    assert python_value(dt.date(2026, 1, 2)) == "2026-01-02"
    assert python_value(5) == 5
