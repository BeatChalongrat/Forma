"""AI agents driven by the offline MockProvider (prompt assembly, validation, fallbacks)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from forma.ai import agents
from forma.ai.heuristics import HeuristicProposal
from forma.ai.llm import LLMClient, LLMError, MockProvider
from forma.mapping_dsl.models import (
    CollectionMapping,
    FieldMapping,
    JoinKey,
    MappingDocument,
    MappingMeta,
    RecordSpec,
    SheetSelect,
    SourceSpec,
    call,
    col,
    const,
)
from forma.profiling.profile import ColumnProfile, TableProfile, WorkbookProfile
from forma.profiling.tables import TableCandidate
from forma.schema_registry import TargetSchema


def _profile(sheets: dict[str, list[tuple[str, str]]]) -> WorkbookProfile:
    from pathlib import Path

    from forma.ingestion.readers import SheetMeta, WorkbookMeta

    tables = []
    for i, (name, cols) in enumerate(sheets.items()):
        cand = TableCandidate(0, 0, 1, 1, 121, 0, len(cols), [c for c, _ in cols], 120, 0.9)
        columns = [ColumnProfile(c, j, t, 0.0, 1.0, 10, ["a"]) for j, (c, t) in enumerate(cols)]
        tables.append(TableProfile(name, i, cand, columns, 120))
    return WorkbookProfile(
        WorkbookMeta(Path("f.xlsx"), "excel", [SheetMeta(n, i) for i, n in enumerate(sheets)]), tables
    )


def _doc() -> MappingDocument:
    return MappingDocument(
        meta=MappingMeta(target_schema="product@1", confidence=0.9),
        sources=[SourceSpec(id="products", sheet=SheetSelect(name="สินค้า"))],
        record=RecordSpec(root_source="products"),
        fields=[
            FieldMapping(target="/sku", expr=call("trim", col("รหัสสินค้า")), confidence=0.9),
            FieldMapping(target="/product_name", expr=col("ชื่อสินค้า"), confidence=0.9),
            FieldMapping(target="/currency", expr=const("THB")),
        ],
        collections=[
            CollectionMapping(
                target="/prices",
                source="products",
                link=[JoinKey(left="รหัสสินค้า", right="รหัสสินค้า")],
                fields=[
                    FieldMapping(target="/prices/*/price_list", expr=const("standard")),
                    FieldMapping(target="/prices/*/amount", expr=call("parse_number_th", col("ราคาขาย"))),
                    FieldMapping(target="/prices/*/vat_included", expr=const(True)),
                ],
            )
        ],
    )


def _draft() -> HeuristicProposal:
    return HeuristicProposal(_doc(), 0.9, ["evidence"], ["q?"], {0: "products"}, {"products": ["หน่วย"]})


@pytest.fixture
def profile() -> WorkbookProfile:
    p = _profile(
        {"สินค้า": [("รหัสสินค้า", "string"), ("ชื่อสินค้า", "string"), ("ราคาขาย", "float"), ("หน่วย", "string")]}
    )
    p.tables[0].table.title = "รายงาน"
    return p


def test_profile_digest_and_catalogue(profile: WorkbookProfile, schema: TargetSchema) -> None:
    d = agents._profile_digest(profile)
    assert d["kind"] == "excel" and d["tables"][0]["sheet"] == "สินค้า" and d["tables"][0]["title"] == "รายงาน"
    assert [c["name"] for c in d["tables"][0]["columns"]][:2] == ["รหัสสินค้า", "ชื่อสินค้า"]
    cat = agents._field_catalogue(schema)
    by_ptr = {f["pointer"]: f for f in cat}
    assert by_ptr["/prices"]["collection"] is True
    assert by_ptr["/promotions/*/type"]["enum"][0] == "percent_off"
    assert by_ptr["/prices/*/effective_from"]["format"] == "date"
    assert "/source_meta" not in by_ptr
    assert "parse_date_th" in agents._ops_list()


def test_schema_agent(profile: WorkbookProfile, schema: TargetSchema) -> None:
    client = LLMClient(
        MockProvider(lambda s, u, sc: {"tables": [{"table_index": 0, "role": "products"}]}), model="m"
    )
    res = agents.schema_agent(client, profile, schema)
    assert res.name == "schema" and res.data["tables"][0]["role"] == "products" and res.attempts == 1
    sent = json.loads(client.provider.calls[0]["user"])  # type: ignore[attr-defined]
    assert "workbook_profile" in sent and sent["output_schema"] == agents.SCHEMA_AGENT_SCHEMA
    assert res.to_dict()["agent"] == "schema"
    bad = LLMClient(MockProvider(lambda s, u, sc: {"tables": "nope"}), model="m", max_retries=0)
    with pytest.raises(LLMError):
        agents.schema_agent(bad, profile, schema)


def test_mapping_agent_accepts_valid_mapping_and_retries_invalid(
    profile: WorkbookProfile, schema: TargetSchema
) -> None:
    good = _doc().model_dump(mode="json", exclude_none=True)
    good["fields"].append({"target": "/brand", "expr": {"op": "col", "name": "ยี่ห้อ"}, "confidence": 0.7})
    answers: list[dict[str, Any]] = [
        {"mapping": {"meta": "broken"}},  # does not match the DSL → retry
        {"mapping": {**good, "fields": good["fields"][1:]}},  # /sku missing → validation error → retry
        {
            "mapping": good,
            "confidence": 0.93,
            "open_questions": ["vat?"],
            "changes_from_draft": ["added /brand"],
        },
    ]
    prompts: list[str] = []

    def responder(system: str, user: str, sc: dict[str, Any]) -> dict[str, Any]:
        prompts.append(user)
        return answers.pop(0)

    client = LLMClient(MockProvider(responder), model="gpt-4.1-mini", max_retries=2)
    doc, res = agents.mapping_agent(
        client, profile, schema, _draft(), schema_analysis={"tables": []}, examples=[{"similarity": 0.5}]
    )
    assert res.attempts == 3 and res.name == "mapping"
    assert "does not match the DSL" in prompts[1] and "missing_required" in prompts[2]
    assert doc.meta.created_by == "ai" and doc.meta.model == "gpt-4.1-mini" and doc.meta.confidence == 0.93
    assert any(f.target == "/brand" for f in doc.fields)
    assert res.data["open_questions"] == ["vat?"] and "mapping" not in res.data
    first = json.loads(prompts[0])
    assert first["schema_analysis"] == {"tables": []} and first["approved_examples_from_similar_files"] == [
        {"similarity": 0.5}
    ]
    assert first["draft_unmatched_columns"] == {"products": ["หน่วย"]}


def test_mapping_agent_falls_back_to_draft(profile: WorkbookProfile, schema: TargetSchema) -> None:
    client = LLMClient(MockProvider(lambda s, u, sc: {"mapping": {"bad": 1}}), model="m", max_retries=0)
    draft = _draft()
    doc, res = agents.mapping_agent(client, profile, schema, draft)
    assert doc is draft.mapping and res.attempts == 0 and res.notes[0].startswith("llm_failed")


def test_adaptation_agent(profile: WorkbookProfile, schema: TargetSchema) -> None:
    good = _doc().model_dump(mode="json", exclude_none=True)
    client = LLMClient(
        MockProvider(lambda s, u, sc: {"mapping": good, "changes_from_draft": ["re-pointed qty"]}), model="m"
    )
    doc, res = agents.adaptation_agent(
        client, _doc(), schema, profile, {"classification": "columns_renamed"}, _draft()
    )
    assert (
        res.name == "adaptation"
        and doc.meta.created_by == "ai"
        and res.data["changes_from_draft"] == ["re-pointed qty"]
    )
    sent = json.loads(client.provider.calls[0]["user"])  # type: ignore[attr-defined]
    assert sent["drift_report"] == {"classification": "columns_renamed"} and "approved_mapping" in sent
    failing = LLMClient(MockProvider(lambda s, u, sc: {}), model="m", max_retries=0)
    draft = _draft()
    doc2, res2 = agents.adaptation_agent(failing, _doc(), schema, profile, {}, draft)
    assert doc2 is draft.mapping and res2.notes and res2.attempts == 0


def test_diagnosis_agent(schema: TargetSchema) -> None:
    good = _doc().model_dump(mode="json", exclude_none=True)
    answers = [
        {"root_causes": [], "patched_mapping": {"meta": "broken"}},  # invalid patch → retry
        {
            "root_causes": [{"finding": "f", "cause": "c", "fix": "x"}],
            "patched_mapping": good,
            "summary_th": "สรุป",
        },
    ]
    client = LLMClient(MockProvider(lambda s, u, sc: answers.pop(0)), model="m", max_retries=2)
    res, patched = agents.diagnosis_agent(
        client, _doc(), schema, [{"kind": "join", "severity": "warning", "message": "m"}], [{"sku": "a"}]
    )
    assert res.name == "diagnosis" and res.attempts == 2 and res.data["summary_th"] == "สรุป"
    assert patched is not None and patched.meta.target_schema == "product@1"
    none_client = LLMClient(
        MockProvider(lambda s, u, sc: {"root_causes": [], "patched_mapping": None}), model="m"
    )
    res2, patched2 = agents.diagnosis_agent(none_client, _doc(), schema, [], [])
    assert patched2 is None and res2.data["root_causes"] == []


def test_copilot_agent(schema: TargetSchema) -> None:
    plan = {
        "operations": [
            {"kind": "scale_field", "target": "/prices/*/amount", "factor": 0.95, "explanation_th": "ลด 5%"}
        ]
    }
    client = LLMClient(MockProvider(lambda s, u, sc: plan), model="m")
    res = agents.copilot_agent(client, "ลดราคา 5%", schema, [{"sku": "a"}])
    assert res.name == "copilot" and res.data == plan
    sent = json.loads(client.provider.calls[0]["user"])  # type: ignore[attr-defined]
    assert sent["instruction"] == "ลดราคา 5%" and sent["sample_records"] == [{"sku": "a"}]


def test_profile_dataclasses_round_trip() -> None:
    cand = TableCandidate(0, 1, 1, 2, 10, 0, 2, ["a", "b"], 8, 0.9)
    cols = [
        ColumnProfile("a", 0, "string", 0.0, 1.0, 8, ["x"]),
        ColumnProfile("b", 1, "int", 0.0, 0.5, 4, ["1"]),
    ]
    from pathlib import Path

    from forma.ingestion.readers import SheetMeta, WorkbookMeta

    prof = WorkbookProfile(
        WorkbookMeta(Path("f.csv"), "csv", [SheetMeta("f", 0)], csv_encoding="utf-8", csv_delimiter=","),
        [TableProfile("f", 0, cand, cols, 8, [], ["a"])],
    )
    again = WorkbookProfile.from_dict(json.loads(json.dumps(prof.to_dict())))
    assert again.meta.kind == "csv" and again.meta.csv_delimiter == ","
    assert again.tables[0].key_candidates == ["a"] and again.tables[0].columns[1].inferred_type == "int"
    assert again.tables[0].table.headers == ["a", "b"]
