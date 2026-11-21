"""What a first real-model run over the ERP exports got wrong on the card, and the rules that now hold
whatever the model writes (ADR-076): one prompt for one decision, no VAT flag from thin air, and an ERP's
"no end date" never reaching the output. A scripted model stands in for the real one."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests import test_erp_export_formats as erp

from forma.ai.heuristics import VAT_ASSUMED_TEXT, VAT_OPEN_TEXT, propose_mapping
from forma.ai.llm import LLMClient, MockProvider
from forma.engine.transform import BoundSource, TransformEngine
from forma.mapping_dsl.models import FieldMapping, MappingDocument, call, col, const
from forma.profiling.profile import profile_workbook
from forma.schema_registry import SchemaRegistry, TargetSchema
from forma.services.mapping import leave_unproven_vat_open, one_question_per_decision, release_row_bounds

VAT = "/prices/*/vat_included"
WAREHOUSE = "/inventory/*/warehouse_code"
ASK_VAT = {
    "target": VAT,
    "question": "ราคาในคอลัมน์ Amount รวม VAT แล้วหรือไม่",
    "why": "ไฟล์ไม่มีคำว่า VAT",
    "kind": "boolean",
    "action": "constant",
}

Responder = Callable[[dict[str, Any]], dict[str, Any]]


@pytest.fixture
def product() -> TargetSchema:
    return SchemaRegistry(Path(__file__).parents[2] / "target_schema").get("product")


@pytest.fixture
def graph(isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., dict[str, Any]]]:
    """Send one export as a new customer's first file, with a scripted model writing the mapping."""
    from tests.conftest import clear_forma_caches

    from forma.api.app import create_app
    from forma.services.context import get_context

    monkeypatch.setenv("FORMA_MAPPING_WORKFLOW", "graph")
    clear_forma_caches()

    with TestClient(create_app()) as c:

        def send(name: str, data: bytes, mapper: Responder) -> dict[str, Any]:
            def responder(system: str, user: str, output: Any) -> dict[str, Any]:
                if "Evidence Analyst" in system:
                    return {
                        "analysis": {
                            "facts": [],
                            "hypotheses": [],
                            "mapping_guidance": [],
                            "missing_information": [],
                        }
                    }
                if "Semantic Reviewer" in system:
                    return {
                        "review": {
                            "verdict": "pass",
                            "checked": ["all"],
                            "concerns": [],
                            "clarifications": [],
                        }
                    }
                return mapper(json.loads(user)["draft_mapping"])

            get_context().llm = LLMClient(MockProvider(responder), "test")
            code = "".join(ch for ch in name.upper() if ch.isalnum())[:20]
            customer = c.post("/api/customers", json={"code": code, "name": name}).json()
            rid = c.post("/api/rounds", json={"customer_id": customer["id"]}).json()["id"]
            up = c.post(
                f"/api/rounds/{rid}/files", files=[("files", (name, data, "application/octet-stream"))]
            )
            assert up.status_code == 201, up.text
            c.post(f"/api/rounds/{rid}/profile", params={"sync": True})
            asked = c.post(f"/api/rounds/{rid}/propose", json={"sync": True, "use_llm": True}).json()
            job = c.get(f"/api/jobs/{asked['job_id']}")
            assert job.json()["status"] == "done", job.json().get("error")
            mapping = c.get(f"/api/mappings/{job.json()['result']['mapping_version_id']}").json()
            return {
                "client": c,
                "mapping": mapping,
                "card": c.get(f"/api/mappings/{mapping['id']}/questions").json(),
            }

        yield send


def _field(document: dict[str, Any], target: str) -> dict[str, Any]:
    fields = [*document.get("fields", []), *(f for c in document.get("collections", []) for f in c["fields"])]
    return next(f for f in fields if f["target"] == target)


def _vat_lines(questions: list[str]) -> list[str]:
    return [q for q in questions if "VAT" in q]


# ---- a VAT flag the file does not state ---------------------------------------------------------
def test_a_vat_flag_the_model_kept_from_the_matcher_is_reopened_and_asked_once(graph: Any) -> None:
    """The model returns the matcher's draft: VAT-inclusive, assumed. Nothing in a SAP price list says so."""
    out = graph("zpricing.xlsx", erp.sap_alv_xlsx(), lambda draft: {"mapping": draft, "clarifications": []})
    mapping, card = out["mapping"], out["card"]
    assert _field(mapping["document"], VAT)["expr"] == {"op": "const"}
    assert {"agent": "vat_evidence", "reopened": [VAT]}.items() <= next(
        e for e in mapping["agent_log"] if e.get("agent") == "vat_evidence"
    ).items()
    # the sentence no longer claims an assumption the mapping does not make
    (line,) = _vat_lines(mapping["open_questions"])
    assert VAT_OPEN_TEXT in line and VAT_ASSUMED_TEXT not in line
    assert mapping["policy"]["decision"] == "review" and mapping["status"] == "proposed"
    controls = [q for q in card["questions"] if q["field"]["semantic"] == "price.vat_included"]
    assert [q["kind"] for q in controls] == ["boolean"]
    assert not _vat_lines(card["open_questions"])  # the control is the question

    c = out["client"]
    answers = {controls[0]["id"]: True}
    preview = c.post(f"/api/mappings/{mapping['id']}/answers/preview", json={"answers": answers}).json()
    applied = c.post(
        f"/api/mappings/{mapping['id']}/answers/apply", json={"answers": answers, "digest": preview["digest"]}
    )
    assert applied.status_code == 201, applied.text
    after = c.get(f"/api/mappings/{applied.json()['id']}").json()
    assert _field(after["document"], VAT)["expr"] == {"op": "const", "value": True}
    assert not _vat_lines(after["open_questions"])
    assert all(p["vat_included"] is True for r in after["sample"] for p in r["prices"])


def test_the_models_own_vat_question_replaces_the_matchers_sentence(graph: Any) -> None:
    out = graph(
        "zpricing.xlsx", erp.sap_alv_xlsx(), lambda draft: {"mapping": draft, "clarifications": [ASK_VAT]}
    )
    mapping = out["mapping"]
    assert _field(mapping["document"], VAT)["expr"] == {"op": "const"}  # written true, reopened
    assert _vat_lines(mapping["open_questions"]) == [ASK_VAT["question"]]
    merged = next(e for e in mapping["agent_log"] if e.get("agent") == "question_reconciliation")
    assert len(merged["merged"]) == 1 and VAT_ASSUMED_TEXT in merged["merged"][0]
    controls = [q for q in out["card"]["questions"] if q["field"]["semantic"] == "price.vat_included"]
    assert [(q["kind"], q["question"]) for q in controls] == [("boolean", ASK_VAT["question"])]
    assert len(set(mapping["open_questions"])) == len(mapping["open_questions"])


def test_the_matchers_warehouse_sentence_merges_into_the_models_confirmation(graph: Any) -> None:
    ask = {
        "target": WAREHOUSE,
        "question": "คอลัมน์ Organization Code ใช้เป็นรหัสคลังได้ใช่หรือไม่",
        "why": "ค่า BKK, CNX",
        "kind": "confirm",
        "action": "confirm",
    }
    out = graph(
        "analysis.csv",
        erp.otbi_data_csv(),
        lambda draft: {"mapping": draft, "clarifications": [ask, ASK_VAT]},
    )
    said = out["mapping"]["open_questions"]
    assert not any("is given per 'Organization Code'" in q for q in said), said
    assert ask["question"] in said and len(said) == 2, said


@pytest.fixture
def sap(tmp_path: Path, product: TargetSchema) -> tuple[Path, Any]:
    path = tmp_path / "zpricing.xlsx"
    path.write_bytes(erp.sap_alv_xlsx())
    return path, propose_mapping(profile_workbook(path), product)


def test_a_vat_flag_is_left_alone_when_something_could_have_told_the_model(
    tmp_path: Path, product: TargetSchema, sap: tuple[Path, Any]
) -> None:
    path, draft = sap

    def reopened(source: Path = path, proposal: Any = draft, **given: Any) -> list[str]:
        doc = MappingDocument.model_validate(proposal.mapping.model_dump(mode="json"))
        bound = {s.id: BoundSource(source) for s in doc.sources}
        return leave_unproven_vat_open(
            doc, product, proposal, bound, notes=given.get("notes"), decided=given.get("decided", set())
        )

    assert reopened() == [VAT]
    assert reopened(notes="ราคาในไฟล์รวม VAT 7% แล้ว") == []  # the operator said so
    assert reopened(decided={VAT}) == []  # a remembered decision covers the field

    said = tmp_path / "with_note.xlsx"  # a line above the table states it
    rows = [["Prices include VAT"], erp.SAP_LABELS, *erp._sap_rows(text=False)]
    said.write_bytes(erp._xlsx(rows))
    told = propose_mapping(profile_workbook(said), product)
    assert any(VAT_ASSUMED_TEXT in q for q in told.open_questions)  # the matcher reads headers only
    assert reopened(said, told) == []


def test_matcher_sentences_stay_when_no_structured_question_covers_them(
    product: TargetSchema, sap: tuple[Path, Any]
) -> None:
    _, draft = sap
    doc = draft.mapping
    vat_line = next(q for q in draft.open_questions if VAT_ASSUMED_TEXT in q)
    other = "Sheet 'cost' is hidden and was left out: confirm it is not needed"
    # the mapping still assumes and the model asked nothing: the sentence is accurate and is the question
    assert one_question_per_decision([vat_line, other], doc, product, []) == ([vat_line, other], [])
    # a clarification that did not reach the question list covers nothing
    assert one_question_per_decision([vat_line], doc, product, [ASK_VAT]) == ([vat_line], [])
    asked = [vat_line, ASK_VAT["question"]]
    assert one_question_per_decision(asked, doc, product, [ASK_VAT]) == ([ASK_VAT["question"]], [vat_line])


# ---- two spellings of one table ------------------------------------------------------------------
def test_a_column_range_that_reads_the_same_cells_takes_the_matchers_spelling(
    tmp_path: Path, product: TargetSchema
) -> None:
    """SAP's "Text with Tabs" starts every line with a tab. The matcher says the table starts at column 0
    and drops the empty column; a model says column 1. Same cells: they must not count as two readings."""
    from forma.ai.evidence import reading_core

    path = tmp_path / "zpricing.txt"
    path.write_bytes(erp.sap_text_with_tabs())
    draft = propose_mapping(profile_workbook(path), product).mapping
    (source,) = draft.sources
    assert source.table.first_column == 0
    bound = {source.id: BoundSource(path)}

    model = MappingDocument.model_validate(draft.model_dump(mode="json"))
    model.sources[0].table = model.sources[0].table.model_copy(update={"first_column": 1})
    assert reading_core(model, "/sku") != reading_core(draft, "/sku")  # spelled apart
    assert release_row_bounds(model, bound, matcher=(draft, bound)) == [f"{source.id}.columns"]
    assert model.sources[0].table.first_column == 0
    assert all(reading_core(model, f.target) == reading_core(draft, f.target) for f in draft.fields)

    narrower = MappingDocument.model_validate(draft.model_dump(mode="json"))
    narrower.sources[0].table = narrower.sources[0].table.model_copy(update={"first_column": 3})
    assert release_row_bounds(narrower, bound, matcher=(draft, bound)) == []  # other cells: left as written
    assert narrower.sources[0].table.first_column == 3
    assert release_row_bounds(model, bound) == []  # without the matcher nothing about columns is touched


def test_saps_pricing_unit_is_not_taken_for_a_pack_size(product: TargetSchema, sap: tuple[Path, Any]) -> None:
    """``per`` is the number of units the amount is for. As a word inside the synonym "qty per pack" it was
    read as the pack size; the model then rightly left it out and FORMA reported the pack size as lost."""
    from forma.ai.heuristics import score_header

    _, draft = sap
    mapped = {f.target for f in draft.mapping.fields}
    assert "/unit/pack_size" not in mapped
    assert "per" in draft.unmatched_columns[draft.mapping.record.root_source]
    pack = product.field("/unit/pack_size")
    assert score_header("per", pack)[0] == 0.0
    assert score_header("Qty per pack", pack)[0] == 1.0  # the synonym itself still names the field
    assert score_header("Pack size (pcs)", pack)[0] >= 0.75


# ---- the matcher reads an ERP price list as the model does ---------------------------------------
def test_a_column_of_currency_codes_is_the_currency_whatever_its_header(product: TargetSchema) -> None:
    import polars as pl

    from forma.ai.heuristics import match_columns
    from forma.profiling.profile import profile_column

    assert "currency_code" in profile_column(pl.Series("Unit", ["THB", "THB", "USD"]), 0).hints
    for units in (["PCS", "BOX"], ["CUP", "CUP"], ["THB", "kg"]):  # a cup is a unit, not Cuban pesos
        assert "currency_code" not in profile_column(pl.Series("Unit", units), 0).hints
    _, draft = _sap_draft(product)
    fields = {f.target: f for f in draft.mapping.fields}
    assert fields["/currency"].evidence == ["every value of 'Unit' is a currency code (THB)"]
    assert fields["/unit/sell_unit"].expr.args[0].name == "UoM"
    table = profile_workbook(_written("zpricing.xlsx", erp.sap_alv_xlsx())).tables[0]
    unit = next(m for m in match_columns(table, [product.field("/unit/sell_unit")]))
    assert unit.column.name == "UoM"


def test_saps_short_heading_for_condition_type_is_the_price_list(product: TargetSchema) -> None:
    from forma.ai.heuristics import score_header

    plist = product.field("/prices/*/price_list")
    assert score_header("CnTy", plist)[0] == 1.0
    assert "short for 'condition type'" in (score_header("CnTy", plist)[1] or "")
    _, draft = _sap_draft(product)
    (prices,) = [c for c in draft.mapping.collections if c.target == "/prices"]
    read = next(f for f in prices.fields if f.target == "/prices/*/price_list")
    assert read.expr.op == "map_values" and read.expr.args[0].name == "CnTy"
    # NetSuite's item "Type" holds item kinds: a word of "condition type" does not make it a price list
    netsuite = propose_mapping(
        profile_workbook(_written("ItemSearchResults.csv", erp.netsuite_csv())), product
    ).mapping
    (prices,) = [c for c in netsuite.collections if c.target == "/prices"]
    assert next(f for f in prices.fields if f.target == "/prices/*/price_list").expr.op == "const"


def _written(name: str, data: bytes) -> Path:
    import tempfile

    path = Path(tempfile.mkdtemp()) / name
    path.write_bytes(data)
    return path


def _sap_draft(product: TargetSchema) -> tuple[Path, Any]:
    path = _written("zpricing.xlsx", erp.sap_alv_xlsx())
    return path, propose_mapping(profile_workbook(path), product)


def test_readings_are_compared_by_what_they_write(product: TargetSchema) -> None:
    """The same cell can be written two ways (ZPR0 or 'standard'); a constant can write what a column
    would. Run every value of the column through both and ask only where they differ."""
    from forma.services.mapping import _agreement, compare_readings

    path, draft = _sap_draft(product)
    columns = {c.name: c.to_dict() for c in profile_workbook(path).tables[0].columns}

    def columns_of(spec: Any) -> list[dict[str, Any]]:
        return list(columns.values())

    def model_reading(expr: Any) -> MappingDocument:
        doc = MappingDocument.model_validate(draft.mapping.model_dump(mode="json"))
        prices = next(c for c in doc.collections if c.target == "/prices")
        next(f for f in prices.fields if f.target == "/prices/*/price_list").expr = expr
        return doc

    passed = model_reading(call("trim", col("CnTy")))  # writes ZPR0
    agreement = _agreement(draft.mapping, passed)
    assert "/prices/*/price_list" in agreement["agree"]  # same cell: once taken as no question at all
    (q,) = compare_readings(draft.mapping, passed, product, agreement, columns_of)
    assert (q["target"], q["action"], q["source_value"]) == ("/prices/*/price_list", "value_map", "ZPR0")
    assert q["options"] == ["standard", "ZPR0"] and "'ZPR0'" in q["question"]

    constant = model_reading(const("standard"))  # writes what the matcher's map writes for every row
    agreement = _agreement(draft.mapping, constant)
    assert "/prices/*/price_list" in agreement["disagree"]  # once a choice of column on the card
    assert compare_readings(draft.mapping, constant, product, agreement, columns_of) == []
    assert "/prices/*/price_list" in agreement["resolved_by_evidence"]
    assert "/prices/*/price_list" not in agreement["disagree"]

    other = model_reading(const("wholesale"))  # a real difference stays a difference
    agreement = _agreement(draft.mapping, other)
    assert compare_readings(draft.mapping, other, product, agreement, columns_of) == []
    assert "/prices/*/price_list" in agreement["disagree"]


def test_the_value_question_reaches_the_card_and_its_answer_is_written(graph: Any) -> None:
    def mapper(draft: dict[str, Any]) -> dict[str, Any]:
        doc = MappingDocument.model_validate(draft)
        prices = next(c for c in doc.collections if c.target == "/prices")
        next(f for f in prices.fields if f.target == "/prices/*/price_list").expr = call("trim", col("CnTy"))
        return {"mapping": doc.model_dump(mode="json"), "clarifications": [ASK_VAT]}

    out = graph("zpricing.xlsx", erp.sap_alv_xlsx(), mapper)
    mapping, card, c = out["mapping"], out["card"], out["client"]
    assert not [q for q in card["questions"] if q["kind"] == "choice"], [q["id"] for q in card["questions"]]
    (value,) = [q for q in card["questions"] if q.get("action") == "value_map"]
    assert value["source_value"] == "ZPR0" and value["options"] == ["standard", "ZPR0"]
    answers = {
        value["id"]: "standard",
        next(q["id"] for q in card["questions"] if q["kind"] == "boolean"): True,
    }
    preview = c.post(f"/api/mappings/{mapping['id']}/answers/preview", json={"answers": answers}).json()
    applied = c.post(
        f"/api/mappings/{mapping['id']}/answers/apply", json={"answers": answers, "digest": preview["digest"]}
    )
    assert applied.status_code == 201, applied.text
    after = c.get(f"/api/mappings/{applied.json()['id']}").json()
    assert {p["price_list"] for r in after["sample"] for p in r["prices"]} == {"standard"}
    assert not after["open_questions"], after["open_questions"]


# ---- an ERP's "no end date" ----------------------------------------------------------------------
def test_sap_open_ended_date_settles_the_date_order_and_is_not_asked_about(
    product: TargetSchema, sap: tuple[Path, Any]
) -> None:
    path, draft = sap
    hints = {c.name: (c.inferred_type, c.hints) for t in profile_workbook(path).tables for c in t.columns}
    assert hints["Valid to"] == ("date", ["day_first"])  # 31.12.9999: only day-first allows a 31
    assert hints["Valid From"] == ("date", ["ambiguous_date"])  # 01.10.2026 alone would not say
    assert not any("day-first or month-first" in q for q in draft.open_questions), draft.open_questions


@pytest.mark.parametrize(
    "end",
    [
        const("9999-12-31"),
        call("parse_date_th", col("Valid to")),
        call(
            "conditional", call("contains", col("Valid to"), pattern="9999"), const("9999-12-31"), const(None)
        ),
    ],
    ids=["constant", "parsed", "condition-around-it"],
)
def test_no_end_date_leaves_as_null_however_the_mapping_wrote_it(
    product: TargetSchema, sap: tuple[Path, Any], end: Any
) -> None:
    path, draft = sap
    doc = draft.mapping
    prices = next(c for c in doc.collections if c.target == "/prices")
    prices.fields.append(FieldMapping(target="/prices/*/effective_to", expr=end))
    prices.fields.append(
        FieldMapping(target="/prices/*/effective_from", expr=call("parse_date_th", col("Valid From")))
    )
    result = TransformEngine(product).run(doc, {s.id: BoundSource(path) for s in doc.sources})
    rows = [p for r in result.records for p in r["prices"]]
    assert len(rows) == erp.N and not result.errors
    assert {p.get("effective_to") for p in rows} == {None}
    assert {p["effective_from"] for p in rows} == {"2026-10-01"}  # a real date is untouched
