"""Mapping DSL models + static validation against the target schema."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from forma.mapping_dsl.models import (
    CallExpr,
    CollectionMapping,
    FieldMapping,
    JoinKey,
    JoinSpec,
    MappingDocument,
    MappingMeta,
    Op,
    QualityRule,
    RecordSpec,
    SheetSelect,
    SourceSpec,
    call,
    col,
    const,
)
from forma.mapping_dsl.validate import validate_mapping
from forma.schema_registry import TargetSchema


def _doc(**overrides: object) -> MappingDocument:
    base: dict[str, object] = {
        "meta": MappingMeta(target_schema="product@1", confidence=0.9),
        "sources": [
            SourceSpec(id="products", role="products", sheet=SheetSelect(name="Products")),
            SourceSpec(id="stock", role="inventory", sheet=SheetSelect(index=1)),
        ],
        "joins": [
            JoinSpec(left="products", right="stock", on=[JoinKey(left="sku", right="sku")], how="left")
        ],
        "record": RecordSpec(root_source="products"),
        "fields": [
            FieldMapping(target="/sku", expr=call("trim", col("sku")), confidence=0.95),
            FieldMapping(target="/product_name", expr=call("normalize_whitespace", col("name"))),
            FieldMapping(target="/currency", expr=const("THB")),
            FieldMapping(
                target="/status",
                expr=call("map_values", col("st"), mapping={"y": "active"}, default="active"),
            ),
        ],
        "collections": [
            CollectionMapping(
                target="/inventory",
                source="stock",
                link=[JoinKey(left="sku", right="sku")],
                fields=[
                    FieldMapping(target="/inventory/*/warehouse_code", expr=col("wh", "stock")),
                    FieldMapping(
                        target="/inventory/*/qty", expr=call("parse_number_th", col("qty", "stock"))
                    ),
                ],
            )
        ],
        "quality": [QualityRule(target="/sku", rule="unique", severity="warning")],
    }
    base.update(overrides)
    return MappingDocument(**base)  # type: ignore[arg-type]


def test_yaml_and_json_round_trip() -> None:
    doc = _doc()
    text = doc.to_yaml()
    assert "target_schema: product@1" in text
    back = MappingDocument.from_yaml(text)
    assert back == doc
    as_json = json.dumps(doc.model_dump(mode="json", exclude_none=True), ensure_ascii=False)
    assert MappingDocument.model_validate(json.loads(as_json)) == doc
    # helpers
    assert doc.source("stock").role == "inventory"
    with pytest.raises(KeyError):
        doc.source("nope")
    assert doc.source_ids() == ["products", "stock"]
    assert "/inventory/*/qty" in doc.all_field_targets() and "/sku" in doc.all_field_targets()
    schema_doc = MappingDocument.json_schema_document()
    assert schema_doc["title"] == "MappingDocument"
    assert "CallExpr" in schema_doc["$defs"]


def test_defaults_for_normalize_keys_are_independent_lists() -> None:
    a, b = (
        JoinSpec(left="x", right="y", on=[JoinKey(left="k", right="k")]),
        JoinSpec(left="x", right="y", on=[JoinKey(left="k", right="k")]),
    )
    assert a.normalize_keys == ["trim", "upper"]
    a.normalize_keys.append("strip_leading_zeros")
    assert b.normalize_keys == ["trim", "upper"]


@pytest.mark.parametrize(
    ("op", "n_args"),
    [
        (Op.TRIM, 0),
        (Op.TRIM, 2),
        (Op.SUBTRACT, 1),
        (Op.SUBTRACT, 3),
        (Op.CONDITIONAL, 1),
        (Op.CONDITIONAL, 4),
        (Op.ADD, 1),
        (Op.COALESCE, 0),
    ],
)
def test_call_expr_arity_errors(op: Op, n_args: int) -> None:
    with pytest.raises(ValidationError, match="expects"):
        CallExpr(op=op, args=[const(1) for _ in range(n_args)])


def test_call_expr_rejects_col_const_ops_and_unknown_op() -> None:
    with pytest.raises(ValidationError, match="use ColExpr/ConstExpr"):
        CallExpr(op=Op.COL, args=[])
    with pytest.raises(ValueError):
        call("explode_everything", col("x"))
    # variadic ops accept many args
    assert len(call("coalesce", col("a"), col("b"), const(0)).args) == 3
    # strict models forbid unknown keys
    with pytest.raises(ValidationError):
        SourceSpec.model_validate({"id": "a", "bogus": 1})
    with pytest.raises(ValidationError):
        SourceSpec.model_validate({"id": "1bad"})


def test_sheet_select_requires_exactly_one() -> None:
    with pytest.raises(ValidationError):
        SheetSelect()
    with pytest.raises(ValidationError):
        SheetSelect(name="a", index=0)
    assert SheetSelect(match="^stock").match == "^stock"


def test_valid_document_passes(schema: TargetSchema) -> None:
    rep = validate_mapping(_doc(), schema)
    assert rep.ok, rep.to_dict()
    codes = {i.code for i in rep.issues}
    # advisory only: constant currency + unmapped critical fields are warnings
    assert codes <= {"critical_constant", "critical_unmapped"}
    assert rep.to_dict()["ok"] is True


def test_unknown_target_and_collection_field_at_root(schema: TargetSchema) -> None:
    doc = _doc(
        fields=[
            FieldMapping(target="/sku", expr=col("sku")),
            FieldMapping(target="/product_name", expr=col("name")),
            FieldMapping(target="/does_not_exist", expr=col("x")),
            FieldMapping(target="/prices/*/amount", expr=col("price")),
        ]
    )
    rep = validate_mapping(doc, schema)
    assert not rep.ok
    by_code = {i.code: i for i in rep.issues if i.level == "error"}
    assert "unknown_target" in by_code and "/does_not_exist" in by_code["unknown_target"].message
    assert "collection_field_at_root" in by_code
    assert by_code["collection_field_at_root"].path == "/fields/3"


def test_missing_required_and_identity(schema: TargetSchema) -> None:
    doc = _doc(fields=[FieldMapping(target="/brand", expr=col("b"))], collections=[])
    rep = validate_mapping(doc, schema)
    errors = [(i.code, i.message) for i in rep.issues if i.level == "error"]
    assert ("missing_required", "required target /sku is not mapped") in errors
    assert ("missing_required", "required target /product_name is not mapped") in errors
    assert any(c == "missing_identity" and "/sku" in m for c, m in errors)


def test_duplicate_target(schema: TargetSchema) -> None:
    doc = _doc(
        fields=[
            FieldMapping(target="/sku", expr=col("sku")),
            FieldMapping(target="/sku", expr=col("sku2")),
            FieldMapping(target="/product_name", expr=col("name")),
        ]
    )
    rep = validate_mapping(doc, schema)
    assert any(i.code == "duplicate_target" and i.path == "/fields/1" for i in rep.issues)


def test_unknown_source_everywhere(schema: TargetSchema) -> None:
    doc = _doc(
        fields=[
            FieldMapping(target="/sku", expr=col("sku", "ghost")),
            FieldMapping(target="/product_name", expr=col("name")),
        ],
        joins=[JoinSpec(left="products", right="phantom", on=[JoinKey(left="a", right="b")])],
        record=RecordSpec(root_source="nowhere"),
        collections=[
            CollectionMapping(
                target="/inventory",
                source="missing",
                link=[JoinKey(left="sku", right="sku")],
                fields=[
                    FieldMapping(target="/inventory/*/warehouse_code", expr=col("wh")),
                    FieldMapping(target="/inventory/*/qty", expr=col("qty")),
                ],
            )
        ],
    )
    rep = validate_mapping(doc, schema)
    unknown = [i for i in rep.issues if i.code == "unknown_source"]
    paths = {i.path for i in unknown}
    assert {"/fields/0", "/joins/0", "/record", "/collections/0"} <= paths


def test_collection_errors(schema: TargetSchema) -> None:
    doc = _doc(
        collections=[
            CollectionMapping(
                target="/inventory",
                source="stock",
                link=[JoinKey(left="sku", right="sku")],
                fields=[
                    FieldMapping(target="/inventory/*/warehouse_code", expr=col("wh")),
                    FieldMapping(target="/inventory/*/warehouse_code", expr=col("wh2")),
                    FieldMapping(target="/prices/*/amount", expr=col("p")),
                    FieldMapping(target="/inventory/*/nope", expr=col("x")),
                ],
            ),
            CollectionMapping(
                target="/brand", source="stock", link=[JoinKey(left="a", right="b")], fields=[]
            ),
        ]
    )
    rep = validate_mapping(doc, schema)
    codes = {(i.code, i.path) for i in rep.issues if i.level == "error"}
    assert ("duplicate_target", "/collections/0/fields/1") in codes
    assert ("target_outside_collection", "/collections/0/fields/2") in codes
    assert ("unknown_target", "/collections/0/fields/3") in codes
    assert ("missing_required", "/collections/0") in codes  # qty not mapped
    assert ("unknown_collection", "/collections/1") in codes


def test_param_checks_and_self_join(schema: TargetSchema) -> None:
    doc = _doc(
        fields=[
            FieldMapping(target="/sku", expr=call("regex_extract", col("sku"))),
            FieldMapping(target="/product_name", expr=call("cast", col("name"), to="datetime")),
            FieldMapping(target="/brand", expr=call("map_values", col("b"))),
            FieldMapping(target="/description", expr=call("vat_adjust", col("d"), mode="half")),
            FieldMapping(target="/barcode", expr=call("in", col("bc"), values="x")),
            FieldMapping(target="/status", expr=call("lookup", col("st"))),
        ],
        joins=[JoinSpec(left="products", right="products", on=[JoinKey(left="a", right="a")])],
        quality=[QualityRule(target="/ghost", rule="not_null")],
        sources=[
            SourceSpec(id="products", table={"filter": call("contains", col("x"))}),  # type: ignore[arg-type]
            SourceSpec(id="stock"),
        ],
    )
    rep = validate_mapping(doc, schema)
    errs = [(i.code, i.path) for i in rep.issues if i.level == "error"]
    assert errs.count(("missing_param", "/fields/0")) == 1
    assert ("bad_param", "/fields/1") in errs
    assert ("missing_param", "/fields/2") in errs
    assert ("bad_param", "/fields/3") in errs
    assert ("missing_param", "/fields/4") in errs
    assert ("missing_param", "/fields/5") in errs
    assert ("self_join", "/joins/0") in errs
    assert ("unknown_target", "/quality/0") in errs
    assert ("missing_param", "/sources/products/table/filter") in errs


def test_duplicate_source_ids(schema: TargetSchema) -> None:
    doc = _doc(sources=[SourceSpec(id="products"), SourceSpec(id="products")])
    rep = validate_mapping(doc, schema)
    assert any(i.code == "duplicate_source" for i in rep.issues)
