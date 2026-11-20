"""The matcher fills a field no column feeds only from the contract's own default (ADR-073)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import openpyxl
import pytest

from forma.ai.heuristics import propose_mapping
from forma.ai.policy import PolicyEngine
from forma.engine.transform import BoundSource, TransformEngine, TransformResult
from forma.mapping_dsl.models import MappingDocument
from forma.profiling.profile import profile_workbook
from forma.schema_registry import SchemaRegistry, TargetSchema

BUILT_IN = Path(__file__).parents[2] / "target_schema" / "product.v1.json"
WAREHOUSE, PRICE_LIST, PROMO_TYPE = (
    "/inventory/*/warehouse_code",
    "/prices/*/price_list",
    "/promotions/*/type",
)


def _schema(
    tmp_path: Path, *, drop_defaults: tuple[str, ...] = (), optional: tuple[str, ...] = ()
) -> TargetSchema:
    """The built-in contract, with the defaults of some item fields removed (``collection.field``)."""
    document: dict[str, Any] = copy.deepcopy(json.loads(BUILT_IN.read_text(encoding="utf-8")))
    document["x-forma"]["schema_id"] = "no_defaults"
    for path in drop_defaults:
        collection, name = path.split(".")
        del document["properties"][collection]["items"]["properties"][name]["default"]
    for path in optional:
        collection, name = path.split(".")
        document["properties"][collection]["items"]["required"].remove(name)
    return SchemaRegistry(tmp_path / "schemas").prepare(document)


def _workbook(path: Path, *, promo_header: list[str] | None = None, status: list[Any] | None = None) -> Path:
    """Products with one price column, stock with no warehouse column, promotions as asked."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)  # type: ignore[arg-type]
    products = wb.create_sheet("สินค้า")
    products.append(["รหัสสินค้า", "ชื่อสินค้า", "ราคาขาย (รวม VAT)", *(["Blocked"] if status else [])])
    for i in range(1, 7):
        products.append([f"A{i:03d}", f"สินค้า {i}", f"{i * 107}.00", *([status[i - 1]] if status else [])])
    stock = wb.create_sheet("Stock")
    stock.append(["รหัสสินค้า", "ยอดคงเหลือ"])
    for i in range(1, 7):
        stock.append([f"A{i:03d}", i * 3])
    if promo_header:
        promo = wb.create_sheet("Promotions")
        promo.append(promo_header)
        for i in range(1, 4):
            promo.append([f"A{i:03d}", f"PR{i}", "10", "1/10/2569", "31/10/2569"][: len(promo_header)])
    wb.save(path)
    return path


def _propose(path: Path, schema: TargetSchema) -> tuple[MappingDocument, TransformResult]:
    mapping = propose_mapping(profile_workbook(path), schema).mapping
    return mapping, TransformEngine(schema).run(mapping, {s.id: BoundSource(path) for s in mapping.sources})


def _field(mapping: MappingDocument, target: str) -> Any:
    return next((f for c in mapping.collections for f in c.fields if f.target == target), None)


def test_contract_defaults_are_used_and_named_as_such(tmp_path: Path, schema: TargetSchema) -> None:
    mapping, result = _propose(_workbook(tmp_path / "a.xlsx"), schema)
    warehouse, price_list = _field(mapping, WAREHOUSE), _field(mapping, PRICE_LIST)
    assert warehouse.expr.value == "MAIN" and "contract default 'MAIN'" in warehouse.evidence[0]
    assert price_list.expr.value == "standard" and "contract default 'standard'" in price_list.evidence[0]
    assert not result.errors
    assert {i["warehouse_code"] for r in result.records for i in r["inventory"]} == {"MAIN"}


def test_a_required_field_without_a_default_is_left_for_a_person(tmp_path: Path) -> None:
    schema = _schema(tmp_path, drop_defaults=("inventory.warehouse_code", "prices.price_list"))
    mapping, result = _propose(_workbook(tmp_path / "a.xlsx"), schema)
    for target in (WAREHOUSE, PRICE_LIST):
        field = _field(mapping, target)
        assert field.expr.value is None and field.confidence == 0.0, target
        assert "declares no default" in field.evidence[0]
    assert "MAIN" not in json.dumps(result.records) and "standard" not in json.dumps(result.records)
    # the contract still requires both: the records do not pass, and nothing is approved by itself
    assert result.stats["invalid_records"] == len(result.records) == 6
    decision = PolicyEngine().evaluate(
        mapping,
        schema,
        findings=[f.to_dict() for f in result.findings],
        rows_out=len(result.records),
        has_previous_approval=True,
        drift="identical",
    )
    assert decision.decision == "review"
    assert {WAREHOUSE, PRICE_LIST} <= {p.split(" ")[0] for p in decision.checks["low_confidence_high"]}


def test_an_optional_field_without_a_default_is_left_out(tmp_path: Path) -> None:
    schema = _schema(
        tmp_path, drop_defaults=("inventory.warehouse_code",), optional=("inventory.warehouse_code",)
    )
    mapping, result = _propose(_workbook(tmp_path / "a.xlsx"), schema)
    assert _field(mapping, WAREHOUSE) is None
    assert not result.errors and all(
        "warehouse_code" not in i for r in result.records for i in r["inventory"]
    )


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (["รหัสสินค้า", "รหัสโปร", "ส่วนลด %", "เริ่ม", "สิ้นสุด"], "percent_off"),  # the column says so
        (["รหัสสินค้า", "รหัสโปร"], None),  # nothing says so
    ],
)
def test_promotion_type_comes_from_a_column_or_from_a_person(
    tmp_path: Path, schema: TargetSchema, header: list[str], expected: str | None
) -> None:
    mapping, result = _propose(_workbook(tmp_path / "a.xlsx", promo_header=header), schema)
    field = _field(mapping, PROMO_TYPE)
    types = {p.get("type") for r in result.records for p in r.get("promotions", [])}
    if expected:
        assert field.confidence == 0.6 and types == {expected}
    else:
        assert field.expr.value is None and field.confidence == 0.0
        assert types == {None} and "percent_off" not in json.dumps(result.records)
        assert result.stats["invalid_records"] == 3  # the three products with a promotion


def test_an_unknown_code_in_a_blocked_flag_is_not_read_as_active(
    tmp_path: Path, schema: TargetSchema
) -> None:
    path = _workbook(tmp_path / "a.xlsx", status=["X", None, "N", "yes", "H", None])
    mapping, result = _propose(path, schema)
    status = next(f for f in mapping.fields if f.target == "/status")
    assert status.expr.op == "default" and status.expr.params == {"value": "active"}
    assert status.expr.args[0].params["keep_unmapped"] is True and "default" not in status.expr.args[0].params
    by_sku = {r["sku"]: r.get("status") for r in result.records}
    assert by_sku == {
        "A001": "discontinued",
        "A002": "active",  # the flag is not set
        "A003": "active",
        "A004": "inactive",
        "A005": "H",  # FORMA does not know this code: it stays as written
        "A006": "active",
    }
    assert result.stats["invalid_records"] == 1  # and the contract's enum refuses it
