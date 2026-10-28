"""PolicyEngine decisions (ai/policy.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from forma.ai.policy import DEFAULT_POLICY, PolicyEngine, _merge
from forma.mapping_dsl.models import (
    CollectionMapping,
    FieldMapping,
    JoinKey,
    MappingDocument,
    MappingMeta,
    RecordSpec,
    SourceSpec,
    call,
    col,
    const,
)
from forma.schema_registry import TargetSchema

POLICY_DIR = Path(__file__).resolve().parents[2] / "policies"


def _doc(
    *, confidence: float = 0.98, field_conf: float = 0.95, amount_conf: float | None = None
) -> MappingDocument:
    return MappingDocument(
        meta=MappingMeta(target_schema="product@1", confidence=confidence),
        sources=[SourceSpec(id="products")],
        record=RecordSpec(root_source="products"),
        fields=[
            FieldMapping(target="/sku", expr=col("sku"), confidence=field_conf),
            FieldMapping(target="/product_name", expr=col("name"), confidence=field_conf),
            FieldMapping(target="/currency", expr=const("THB"), confidence=0.9),
        ],
        collections=[
            CollectionMapping(
                target="/prices",
                source="products",
                link=[JoinKey(left="sku", right="sku")],
                fields=[
                    FieldMapping(target="/prices/*/price_list", expr=const("standard"), confidence=0.8),
                    FieldMapping(
                        target="/prices/*/amount",
                        expr=call("parse_number_th", col("price")),
                        confidence=amount_conf if amount_conf is not None else field_conf,
                    ),
                    FieldMapping(target="/prices/*/vat_included", expr=const(True), confidence=0.9),
                ],
            )
        ],
    )


@pytest.fixture(scope="module")
def engine() -> PolicyEngine:
    return PolicyEngine.load(POLICY_DIR)


def test_load_and_merge(engine: PolicyEngine) -> None:
    assert engine.policy["auto_approve"]["min_mapping_confidence"] == 0.95
    assert engine.policy["risk_overrides"]["price.amount"] == "critical"
    assert PolicyEngine.load(Path("/nonexistent")).policy == DEFAULT_POLICY
    merged = _merge({"a": {"x": 1, "y": 2}, "b": 1}, {"a": {"y": 3}, "c": 4})
    assert merged == {"a": {"x": 1, "y": 3}, "b": 1, "c": 4}
    custom = PolicyEngine({"block": {"on_zero_rows": False}})
    assert (
        custom.policy["block"]["on_zero_rows"] is False
        and custom.policy["block"]["on_unrelated_file"] is True
    )


def test_auto_approve(engine: PolicyEngine, schema: TargetSchema) -> None:
    d = engine.evaluate(
        _doc(), schema, findings=[], rows_out=100, drift="identical", has_previous_approval=True
    )
    assert d.decision == "auto_approve"
    assert d.priority == 0 and d.reasons == ["all policy checks passed"]
    assert d.checks["errors"] == 0 and d.checks["low_confidence_critical"] == []
    assert 0 <= d.to_dict()["risk_score"] <= 1
    # a few warnings and additive drift are tolerated
    warnings = [{"severity": "warning", "message": "x"}] * 5
    assert (
        engine.evaluate(
            _doc(), schema, findings=warnings, rows_out=10, drift="columns_added", has_previous_approval=True
        ).decision
        == "auto_approve"
    )


def test_new_customer_goes_to_review(engine: PolicyEngine, schema: TargetSchema) -> None:
    d = engine.evaluate(_doc(), schema, findings=[], rows_out=100, has_previous_approval=False)
    assert d.decision == "review"
    assert d.reasons == ["first time this customer/format is seen"]
    assert d.priority == 15


def test_blocks(engine: PolicyEngine, schema: TargetSchema) -> None:
    d = engine.evaluate(
        _doc(), schema, findings=[], rows_out=100, drift="unrelated_file", has_previous_approval=True
    )
    assert d.decision == "block" and d.priority == 100 and d.risk_score == 1.0
    assert "wrong file" in d.reasons[0]
    z = engine.evaluate(_doc(), schema, findings=[], rows_out=0, has_previous_approval=True)
    assert z.decision == "block" and "zero records" in z.reasons[0]
    relaxed = PolicyEngine({"block": {"on_zero_rows": False, "on_unrelated_file": False}})
    assert (
        relaxed.evaluate(
            _doc(), schema, findings=[], rows_out=0, drift="unrelated_file", has_previous_approval=True
        ).decision
        == "review"
    )


def test_review_reasons_accumulate(engine: PolicyEngine, schema: TargetSchema) -> None:
    findings = [
        {"severity": "error", "message": "3 rows have a null identity key ['/sku']"},
        {"severity": "error", "message": "boom"},
        *[{"severity": "warning", "message": "w"} for _ in range(6)],
    ]
    d = engine.evaluate(
        _doc(confidence=0.5, field_conf=0.5, amount_conf=0.3),
        schema,
        findings=findings,
        rows_out=10,
        drift="columns_removed",
        has_previous_approval=True,
        row_change_ratio=0.6,
    )
    assert d.decision == "review"
    text = " ".join(d.reasons)
    assert "identity keys missing" in text
    assert "/prices/*/amount (0.30)" in text and "/sku (0.50)" in text  # critical fields
    assert "/product_name (0.50)" in text  # high-risk field
    assert "mapping confidence 0.50" in text
    assert "2 engine/validation errors" in text
    assert "6 warnings > 5" in text
    assert "schema drift: columns_removed" in text
    assert "row count changed 60%" in text
    assert d.priority > 150
    assert d.risk_score == 1.0
    assert d.checks["low_confidence_critical"] == ["/sku (0.50)", "/prices/*/amount (0.30)"]


def test_other_customer_match_goes_to_review(engine: PolicyEngine, schema: TargetSchema) -> None:
    d = engine.evaluate(
        _doc(),
        schema,
        findings=[],
        rows_out=10,
        has_previous_approval=True,
        other_customer={"customer_id": "cus_1", "customer_code": "ACME"},
    )
    assert d.decision == "review"
    assert d.reasons == [
        "structure is identical to customer ACME's approved format — confirm this file belongs here"
    ]
    assert d.priority == 50
    no_code = engine.evaluate(
        _doc(),
        schema,
        findings=[],
        rows_out=10,
        has_previous_approval=True,
        other_customer={"customer_id": "cus_1"},
    )
    assert "customer cus_1" in no_code.reasons[0]


def test_reused_approved_ignores_low_field_confidence(engine: PolicyEngine, schema: TargetSchema) -> None:
    doc = _doc(confidence=1.0, field_conf=0.2, amount_conf=0.1)
    strict = engine.evaluate(
        doc, schema, findings=[], rows_out=50, drift="identical", has_previous_approval=True
    )
    assert strict.decision == "review" and strict.checks["low_confidence_critical"]
    reused = engine.evaluate(
        doc,
        schema,
        findings=[],
        rows_out=50,
        drift="identical",
        has_previous_approval=True,
        reused_approved=True,
    )
    assert reused.decision == "auto_approve"
    assert reused.checks["reused_approved"] is True and reused.checks["low_confidence_critical"] == []


def test_risk_override_and_field_confidence_fallback(engine: PolicyEngine, schema: TargetSchema) -> None:
    assert engine.risk_of(schema, "/prices/*/amount") == "critical"
    assert engine.risk_of(schema, "/promotions/*/start_date") == "critical"
    assert engine.risk_of(schema, "/brand") == "low"
    assert engine.risk_of(schema, "/unknown") == "medium"
    # fields without confidence inherit meta.confidence
    doc = _doc(confidence=0.97)
    for f in doc.fields:
        f.confidence = None
    d = engine.evaluate(doc, schema, findings=[], rows_out=5, has_previous_approval=True)
    assert d.decision == "auto_approve"
