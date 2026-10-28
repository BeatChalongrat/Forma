"""A downstream contract with other field names, a flat price, its own enum spelling and a strict shape
(ADR-001): FORMA maps, transforms, validates and exports it without a code change."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import openpyxl

from forma.ai.heuristics import propose_mapping
from forma.engine.transform import BoundSource, TransformEngine
from forma.export.exporter import export_bytes
from forma.mapping_dsl.validate import validate_mapping
from forma.profiling.profile import profile_workbook
from forma.schema_registry import TargetSchema


def _f(type_: Any, semantic: str, *syn_th: str, **extra: Any) -> dict[str, Any]:
    return {"type": type_, **extra, "x-forma": {"semantic": semantic, "synonyms_th": list(syn_th)}}


CATALOG: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Catalog item",
    "type": "object",
    "additionalProperties": False,
    "required": ["item_code", "item_name", "price"],
    "properties": {
        "item_code": _f("string", "sku", "รหัสสินค้า"),
        "item_name": _f("string", "product_name", "ชื่อสินค้า"),
        "price": _f("number", "price.amount", "ราคาขาย"),
        "vat_inclusive": _f("boolean", "price.vat_included"),
        "state": _f("string", "status", "สถานะ", enum=["ACTIVE", "INACTIVE", "DISCONTINUED"]),
        "stock": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["branch", "on_hand"],
                "properties": {
                    "branch": _f("string", "stock.warehouse", "คลัง"),
                    "on_hand": _f("number", "stock.qty", "ยอดคงเหลือ"),
                },
            },
        },
        "deals": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["deal_id", "kind"],
                "properties": {
                    "deal_id": _f("string", "promo.code", "รหัสโปร"),
                    "kind": _f("string", "promo.type", "ประเภทโปร", enum=["PERCENT_OFF", "AMOUNT_OFF"]),
                    "pct": _f(["number", "null"], "discount.percent", "ส่วนลด %"),
                    "from": _f(["string", "null"], "promo.start_date", "เริ่ม", format="date"),
                    "to": _f(["string", "null"], "promo.end_date", "สิ้นสุด", format="date"),
                },
            },
        },
    },
    "x-forma": {
        "schema_id": "catalog",
        "version": 1,
        "identity_keys": ["/item_code"],
        "collections": {"/stock": {"identity_keys": ["/branch"]}, "/deals": {"identity_keys": ["/deal_id"]}},
    },
}


def _workbook(path: Path, *, promos: int) -> Path:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)  # type: ignore[arg-type]
    ws = wb.create_sheet("สินค้า")
    ws.append(["รายงานสินค้า"])
    ws.append([None])
    ws.append(["รหัสสินค้า", "ชื่อสินค้า", "หมวด", "หน่วย", "ราคาขาย (รวม VAT)", "สถานะ"])
    ws.append(["000123", "น้ำดื่ม 600ml", "เครื่องดื่ม", "ลัง(12)", "1,284.00", "ขาย"])
    for i in range(1, 12):
        ws.append([f"A{i:03d}", f"สินค้า {i}", "อาหาร", "ชิ้น", f"{i * 10}.00", "เลิกขาย" if i == 5 else "ขาย"])
    ws.append(["รวม", None, None, None, "9,999.00", None])
    st = wb.create_sheet("Stock")
    st.append(["รหัสสินค้า", "คลัง", "ยอดคงเหลือ"])
    for sku in ["000123"] + [f"A{i:03d}" for i in range(1, 12)]:
        st.append([sku, "BKK", 100])
        st.append([sku, "CNX", 20])
    pr = wb.create_sheet("Promotions")
    pr.append(["รหัสสินค้า", "รหัสโปร", "ประเภทโปร", "ส่วนลด %", "เริ่ม", "สิ้นสุด"])
    for i, sku in enumerate(["000123", "A002", "A003"][:promos], start=1):
        pr.append([sku, f"P{i}", "ลด%", "10%", "1/10/2569", "31/10/2569"])
    wb.save(path)
    return path


def test_new_contract_end_to_end_without_code_changes(tmp_path: Path) -> None:
    schema = TargetSchema("catalog", 1, CATALOG)
    path = _workbook(tmp_path / "สินค้า_C1.xlsx", promos=2)
    prop = propose_mapping(profile_workbook(path), schema)
    rep = validate_mapping(prop.mapping, schema)
    assert rep.ok, [i.message for i in rep.issues if i.level == "error"]
    assert {"/item_code", "/item_name", "/price", "/vat_inclusive", "/state"} <= {
        f.target for f in prop.mapping.fields
    }
    assert {c.target for c in prop.mapping.collections} == {"/stock", "/deals"}  # two promotions still count

    bindings = {s.id: BoundSource(path, file_name=path.name) for s in prop.mapping.sources}
    res = TransformEngine(schema).run(prop.mapping, bindings)
    assert not [f.message for f in res.findings if f.severity == "error"]
    assert len(res.records) == 12  # the total row is not a product

    out = json.loads(export_bytes(res.records, "json", schema))
    assert schema.validate_records(out) == []
    assert all(set(r) <= set(CATALOG["properties"]) for r in out)  # no provenance, no unmapped columns
    first = next(r for r in out if r["item_code"] == "000123")
    assert first == {
        "item_code": "000123",
        "item_name": "น้ำดื่ม 600ml",
        "price": 1284.0,
        "vat_inclusive": True,
        "state": "ACTIVE",
        "stock": [{"branch": "BKK", "on_hand": 100.0}, {"branch": "CNX", "on_hand": 20.0}],
        "deals": [
            {"deal_id": "P1", "kind": "PERCENT_OFF", "pct": 10.0, "from": "2026-10-01", "to": "2026-10-31"}
        ],
    }
    assert next(r for r in out if r["item_code"] == "A005")["state"] == "DISCONTINUED"
    # lineage stays available inside FORMA even though the contract never sees it
    assert res.records[0]["source_meta"]["lineage"]["source"] == prop.mapping.record.root_source


def test_one_promotion_is_still_mapped(schema: TargetSchema, tmp_path: Path) -> None:
    path = _workbook(tmp_path / "สินค้า_C2.xlsx", promos=1)
    prop = propose_mapping(profile_workbook(path), schema)
    promo = schema.collection_for("promotions")
    assert promo in {c.target for c in prop.mapping.collections}


def test_semantic_lookups_and_export_view(schema: TargetSchema) -> None:
    catalog = TargetSchema("catalog", 1, CATALOG)
    assert catalog.identity_field() is not None and catalog.identity_field().pointer == "/item_code"  # type: ignore[union-attr]
    assert (
        catalog.collection_kind("/stock") == "inventory" and catalog.collection_kind("/deals") == "promotions"
    )
    assert catalog.collection_for("pricing") is None and catalog.semantic_pointers("price.amount") == {
        "/price"
    }
    assert schema.collection_for("pricing") == "/prices"
    # an undeclared risk comes from the meaning: identity and money are critical in any contract
    assert [
        catalog.field(p).risk for p in ("/item_code", "/price", "/deals/*/kind", "/item_name", "/state")
    ] == [
        "critical",
        "critical",
        "critical",
        "high",
        "medium",
    ]
    assert TargetSchema.wildcard("/prices/0/amount") == "/prices/*/amount"
    rec = {"item_code": "x", "source_meta": {"lineage": {}}, "attributes": {"หมวด": "a"}}
    assert catalog.export_view(rec) == {"item_code": "x"}
    assert schema.export_view(rec) is rec  # the draft product schema declares both
