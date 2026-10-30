"""Independent expected JSON and failure gates, not generated from the production mapper."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from forma.ai.heuristics import propose_mapping
from forma.ai.policy import PolicyEngine
from forma.core.errors import ValidationFailed
from forma.engine.transform import BoundSource, TransformEngine
from forma.export.exporter import export_bytes
from forma.mapping_dsl.validate import validate_mapping
from forma.profiling.profile import profile_workbook
from forma.schema_registry import SchemaRegistry, TargetSchema

CONTRACT = {
    "type": "object",
    "additionalProperties": False,
    "required": ["item_id", "label", "selling_price", "includes_tax"],
    "x-forma": {"schema_id": "acceptance", "identity_keys": ["/item_id"]},
    "properties": {
        "item_id": {
            "type": "string",
            "x-forma": {"semantic": "sku", "synonyms_th": ["รหัสสินค้า"], "synonyms_en": ["SKU"]},
        },
        "label": {
            "type": "string",
            "x-forma": {"semantic": "product_name", "synonyms_th": ["ชื่อสินค้า"], "synonyms_en": ["Name"]},
        },
        "selling_price": {
            "type": "number",
            "minimum": 0,
            "x-forma": {
                "semantic": "price.amount",
                "synonyms_th": ["ราคาขาย"],
                "synonyms_en": ["Sales Price"],
            },
        },
        "includes_tax": {"type": "boolean", "x-forma": {"semantic": "price.vat_included"}},
    },
}


def source(tmp_path: Path, header: str = "ราคาขาย (รวม VAT)") -> Path:
    p = tmp_path / "สินค้า.csv"
    p.write_text(f"รหัสสินค้า,ชื่อสินค้า,{header}\n000123,น้ำดื่ม,107\n000124,ชา,214\n", encoding="utf-8-sig")
    return p


def test_json_meaning_and_exact_shape_follow_custom_contract(tmp_path: Path) -> None:
    schema = SchemaRegistry(tmp_path / "schemas").prepare(CONTRACT)
    path = source(tmp_path)
    proposal = propose_mapping(profile_workbook(path), schema)
    assert validate_mapping(proposal.mapping, schema).ok
    result = TransformEngine(schema).run(
        proposal.mapping, {s.id: BoundSource(path) for s in proposal.mapping.sources}
    )
    assert not result.errors
    payload = json.loads(export_bytes(result.records, "json", schema))
    assert payload == [
        {"item_id": "000123", "label": "น้ำดื่ม", "selling_price": 107.0, "includes_tax": True},
        {"item_id": "000124", "label": "ชา", "selling_price": 214.0, "includes_tax": True},
    ]
    assert schema.validate_records(payload) == []


@pytest.mark.parametrize("nested", [False, True])
def test_non_product_contract_without_builtin_semantics(tmp_path: Path, nested: bool) -> None:
    """A new entity and nested field paths require configuration only, not product aliases."""
    detail = {
        "type": "object",
        "additionalProperties": False,
        "required": ["recipient", "hours"],
        "properties": {"recipient": {"type": "string"}, "hours": {"type": "number", "minimum": 0}},
    }
    props = {"ticket_no": {"type": "string"}}
    props.update({"service": detail} if nested else detail["properties"])
    doc = {
        "type": "object",
        "additionalProperties": False,
        "required": list(props),
        "x-forma": {"schema_id": "support_ticket", "identity_keys": ["/ticket_no"]},
        "properties": props,
    }
    schema = SchemaRegistry(tmp_path / "schemas").prepare(doc)
    path = tmp_path / "support.csv"
    path.write_text("ticket_no,recipient,hours\n0007,Nida,1.5\n0008,Som,2\n", encoding="utf-8")
    proposal = propose_mapping(profile_workbook(path), schema)
    assert validate_mapping(proposal.mapping, schema).ok
    result = TransformEngine(schema).run(
        proposal.mapping, {s.id: BoundSource(path) for s in proposal.mapping.sources}
    )
    assert not result.errors
    payload = json.loads(export_bytes(result.records, "json", schema))
    expected = []
    for key, person, hours in [("0007", "Nida", 1.5), ("0008", "Som", 2.0)]:
        values = {"recipient": person, "hours": hours}
        expected.append({"ticket_no": key, **({"service": values} if nested else values)})
    assert payload == expected
    assert schema.validate_records(payload) == []


def test_ai_receives_business_meaning_and_full_contract(tmp_path: Path) -> None:
    from forma.ai.agents import mapping_agent, schema_agent
    from forma.ai.llm import LLMClient, MockProvider

    doc = copy.deepcopy(CONTRACT)
    field = doc["properties"]["selling_price"]
    field["description"] = "The amount charged to the customer, not the purchase cost."
    field["examples"] = [107, 214]
    field["maximum"] = 10000
    field["x-forma"]["description_th"] = "ราคาที่เรียกเก็บจากลูกค้า ไม่ใช่ต้นทุนซื้อ"
    schema = TargetSchema("acceptance", 1, doc)
    profile = profile_workbook(source(tmp_path))
    draft = propose_mapping(profile, schema)
    provider = MockProvider(lambda s, u, sc: {"mapping": draft.mapping.model_dump(mode="json"), "tables": []})
    client = LLMClient(provider, model="offline")
    schema_agent(client, profile, schema)
    mapping_agent(client, profile, schema, draft)
    for request in provider.calls:
        sent = json.loads(request["user"])
        assert sent["target_json_schema"] == doc
        target = next(f for f in sent["target_fields"] if f["pointer"] == "/selling_price")
        assert target["description"] == field["description"]
        assert target["description_th"] == field["x-forma"]["description_th"]
        assert target["examples"] == [107, 214]


def test_ambiguous_tax_goes_to_review_even_if_json_is_valid(tmp_path: Path) -> None:
    schema = TargetSchema("acceptance", 1, CONTRACT)
    path = source(tmp_path, "Sales Price")
    proposal = propose_mapping(profile_workbook(path), schema)
    result = TransformEngine(schema).run(
        proposal.mapping, {s.id: BoundSource(path) for s in proposal.mapping.sources}
    )
    decision = PolicyEngine().evaluate(
        proposal.mapping,
        schema,
        findings=[f.to_dict() for f in result.findings],
        rows_out=len(result.records),
        has_previous_approval=True,
    )
    assert proposal.open_questions
    assert decision.decision != "auto_approve"


@pytest.mark.parametrize("rows", [0, 2])
def test_unanswered_question_overrides_high_confidence_and_prior_approval(tmp_path: Path, rows: int) -> None:
    schema = TargetSchema("acceptance", 1, CONTRACT)
    proposal = propose_mapping(profile_workbook(source(tmp_path)), schema)
    proposal.mapping.meta.confidence = 1.0
    for field in proposal.mapping.fields:
        field.confidence = 1.0
    common = {"findings": [], "rows_out": rows, "has_previous_approval": True, "reused_approved": True}
    engine = PolicyEngine()
    without_question = engine.evaluate(proposal.mapping, schema, **common)
    decision = engine.evaluate(proposal.mapping, schema, open_questions=["Retail or wholesale?"], **common)
    if rows:
        assert without_question.decision == "auto_approve"
        assert decision.decision == "review"
    else:
        assert without_question.decision == decision.decision == "block"
    assert decision.checks["unresolved_questions"] == 1


@pytest.mark.parametrize(
    "field",
    [
        {"$ref": "#/$defs/price"},
        {"oneOf": [{"type": "string"}, {"type": "number"}]},
        {"type": "array", "prefixItems": [{"type": "string"}]},
        True,
    ],
)
def test_unsupported_contract_is_rejected_before_registration(tmp_path: Path, field: Any) -> None:
    doc = copy.deepcopy(CONTRACT)
    doc["properties"]["extra"] = field
    doc["$defs"] = {"price": {"type": "number"}}
    registry = SchemaRegistry(tmp_path / "schemas")
    with pytest.raises(ValidationFailed, match="does not support"):
        registry.register(doc)
    assert not (tmp_path / "schemas").exists()


def test_existing_unsupported_contract_cannot_bypass_mapping_validation(tmp_path: Path) -> None:
    proposal = propose_mapping(profile_workbook(source(tmp_path)), TargetSchema("acceptance", 1, CONTRACT))
    bad = copy.deepcopy(CONTRACT)
    bad["properties"]["extra"] = {"$ref": "https://example.invalid/shape"}
    report = validate_mapping(proposal.mapping, TargetSchema("acceptance", 1, bad))
    assert not report.ok and report.issues[0].code == "unsupported_schema"


def test_cached_formula_notice_still_needs_review_with_approved_mapping(tmp_path: Path) -> None:
    from test_ingestion_readiness import text_cell, workbook_xml

    schema = TargetSchema("acceptance", 1, CONTRACT)
    rows = f'<row r="1">{text_cell("A1", "SKU")}{text_cell("B1", "Name")}{text_cell("C1", "ราคาขาย (รวม VAT)")}</row>'
    rows += f'<row r="2">{text_cell("A2", "000123")}{text_cell("B2", "Tea")}<c r="C2"><f>100+7</f><v>107</v></c></row>'
    path = workbook_xml(tmp_path / "cached.xlsx", rows)
    proposal = propose_mapping(profile_workbook(path), schema)
    result = TransformEngine(schema).run(
        proposal.mapping, {s.id: BoundSource(path) for s in proposal.mapping.sources}
    )
    assert result.records and not result.errors
    assert any(f.kind == "source_review" for f in result.findings)
    decision = PolicyEngine().evaluate(
        proposal.mapping,
        schema,
        findings=[f.to_dict() for f in result.findings],
        rows_out=len(result.records),
        has_previous_approval=True,
        reused_approved=True,
    )
    assert decision.decision == "review"
    assert decision.checks["source_review_required"]


def test_missing_formula_cache_blocks_engine_before_json_can_look_valid(tmp_path: Path) -> None:
    from test_ingestion_readiness import text_cell, workbook_xml

    schema = TargetSchema("acceptance", 1, CONTRACT)
    rows = f'<row r="1">{text_cell("A1", "SKU")}{text_cell("B1", "Name")}{text_cell("C1", "ราคาขาย (รวม VAT)")}</row>'
    rows += (
        f'<row r="2">{text_cell("A2", "000123")}{text_cell("B2", "Tea")}<c r="C2"><f>100+7</f><v/></c></row>'
    )
    path = workbook_xml(tmp_path / "uncached.xlsx", rows)
    proposal = propose_mapping(profile_workbook(path), schema)
    result = TransformEngine(schema).run(
        proposal.mapping, {s.id: BoundSource(path) for s in proposal.mapping.sources}
    )
    assert result.records == [] and result.errors
    assert "cached" in result.errors[0].message
