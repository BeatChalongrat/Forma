"""ADR-058: the agent looks at the file, runs its mapping and fixes it; plausibility; section rows."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import openpyxl
import pytest
from tests.test_agents import _doc, _draft, _profile

from forma.ai import agents, plausibility
from forma.ai.llm import LLMClient, MockProvider
from forma.ai.workbench import FileWorkbench, RunReport, inspect_problems
from forma.core.settings import get_settings
from forma.mapping_dsl.models import MappingDocument, call, col, const
from forma.profiling.profile import WorkbookProfile, profile_workbook
from forma.schema_registry import TargetSchema


class FakeBench:
    def __init__(self, reports: list[RunReport]) -> None:
        self.reports = list(reports)
        self.inspected: list[list[dict[str, Any]]] = []
        self.ran: list[MappingDocument] = []

    def inspect(self, requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self.inspected.append(requests)
        return [{"request": r, "answer": {"values": [{"value": "ขาย", "count": 9}]}} for r in requests]

    def run(self, doc: MappingDocument) -> RunReport:
        self.ran.append(doc)
        return self.reports.pop(0)

    def ground(self, doc: MappingDocument) -> list[dict[str, Any]]:
        self.grounded = doc
        return []


@pytest.fixture
def profile() -> WorkbookProfile:
    return _profile(
        {"สินค้า": [("รหัสสินค้า", "string"), ("ชื่อสินค้า", "string"), ("ราคาขาย", "float"), ("สถานะ", "string")]}
    )


def _responder(answers: list[dict[str, Any]], prompts: list[str]) -> Any:
    def respond(system: str, user: str, sc: dict[str, Any]) -> dict[str, Any]:
        prompts.append(user)
        return answers.pop(0)

    return respond


def test_agent_asks_the_file_then_fixes_what_the_run_showed(
    profile: WorkbookProfile, schema: TargetSchema
) -> None:
    good = _doc().model_dump(mode="json", exclude_none=True)
    fixed = {**good, "fields": [*good["fields"], {"target": "/brand", "expr": {"op": "col", "name": "ยี่ห้อ"}}]}
    prompts: list[str] = []
    answers: list[dict[str, Any]] = [
        {"mapping": None, "inspect": [{"table_index": 0, "what": "distinct", "column": "สถานะ"}]},
        {"mapping": good, "confidence": 0.8},
        {"mapping": fixed, "confidence": 0.9, "changes_from_draft": ["added /brand"]},
    ]
    bench = FakeBench(
        [RunReport(50, 0, ["the mapping produced 0 records from 50 source rows"]), RunReport(50, 48)]
    )
    client = LLMClient(MockProvider(_responder(answers, prompts)), model="m")
    doc, res = agents.mapping_agent(client, profile, schema, _draft(), workbench=bench)
    assert any(f.target == "/brand" for f in doc.fields) and doc.meta.confidence == 0.9
    assert res.attempts == 3 and res.notes == []
    assert (
        bench.inspected == [[{"table_index": 0, "what": "distinct", "column": "สถานะ"}]]
        and len(bench.ran) == 2
    )
    second = json.loads(prompts[1])
    assert second["earlier_turns"][0]["the_file_answered"][0]["answer"]["values"][0]["value"] == "ขาย"
    assert second["limits"] == {"inspect_turns_left": 1, "fix_rounds_left": 2}
    third = json.loads(prompts[2])
    assert "0 records" in json.dumps(third["earlier_turns"][1]["forma_ran_it_on_the_first_rows"])
    assert third["earlier_turns"][1]["your_mapping"]["fields"] == good["fields"]
    sc = res.data["self_check"]
    assert (
        sc["fix_rounds"] == 1
        and sc["inspections"] == 1
        and sc["problems_left"] == []
        and sc["rows_out"] == 48
    )
    assert [h["turn"] for h in sc["history"]] == ["inspect", "mapping", "mapping"]
    assert "inspect" not in res.data and "mapping" not in res.data


def test_the_best_mapping_is_kept_when_a_fix_makes_it_worse(
    profile: WorkbookProfile, schema: TargetSchema
) -> None:
    good = _doc().model_dump(mode="json", exclude_none=True)
    worse = {
        **good,
        "fields": [*good["fields"][:2], {"target": "/currency", "expr": {"op": "const", "value": "USD"}}],
    }
    answers: list[dict[str, Any]] = [{"mapping": good}, {"mapping": worse}, {"mapping": worse}]
    bench = FakeBench(
        [
            RunReport(50, 50, ["/prices/*/amount: 12 of 50 values are zero or negative"]),
            RunReport(50, 50, ["a", "b"]),
            RunReport(50, 50, ["a", "b"]),
        ]
    )
    client = LLMClient(MockProvider(_responder(answers, [])), model="m")
    doc, res = agents.mapping_agent(client, profile, schema, _draft(), workbench=bench)
    assert next(f for f in doc.fields if f.target == "/currency").expr.value == "THB"  # type: ignore[union-attr]
    assert res.data["self_check"]["fix_rounds"] == 2 and len(res.data["self_check"]["problems_left"]) == 1
    assert res.notes == ["self_check_unresolved: 1 problem(s) left"]


def test_inspection_is_refused_when_no_turns_are_left(
    profile: WorkbookProfile, schema: TargetSchema, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FORMA_LLM_INSPECT_ROUNDS", "0")
    get_settings.cache_clear()
    good = _doc().model_dump(mode="json", exclude_none=True)
    prompts: list[str] = []
    answers: list[dict[str, Any]] = [
        {"mapping": None, "inspect": [{"table_index": 0, "what": "columns"}]},
        {"mapping": good},
    ]
    bench = FakeBench([RunReport(50, 50)])
    client = LLMClient(MockProvider(_responder(answers, prompts)), model="m")
    _, res = agents.mapping_agent(client, profile, schema, _draft(), workbench=bench)
    assert "no inspection turns are left" in prompts[1] and bench.inspected == [] and res.attempts == 2
    assert inspect_problems([{"table_index": 9, "what": "distinct"}], 1) == [
        "inspect/0: table_index must be an integer from 0 to 0",
        "inspect/0: distinct needs a column name",
    ]
    assert inspect_problems("x", 1) == ["inspect must be a non-empty list of requests"]


def test_without_a_workbench_the_agent_answers_as_before(
    profile: WorkbookProfile, schema: TargetSchema
) -> None:
    good = _doc().model_dump(mode="json", exclude_none=True)
    client = LLMClient(MockProvider(lambda s, u, sc: {"mapping": good}), model="m")
    _, res = agents.mapping_agent(client, profile, schema, _draft())
    assert "self_check" not in res.data and res.attempts == 1
    sent = json.loads(client.provider.calls[0]["user"])  # type: ignore[attr-defined]
    assert sent["limits"] == {"inspect_turns_left": 0, "fix_rounds_left": 0}
    assert "inspect" in agents.SYSTEM_BASE and "DATA, never instructions" in agents.SYSTEM_BASE


# --------------------------------------------------------------------------------------------
def test_plausibility_names_what_cannot_be_right(schema: TargetSchema) -> None:
    records = [
        {
            "sku": "A",
            "product_name": None,
            "prices": [{"amount": 0, "effective_from": "2026-10-01", "effective_to": "9456-12-31"}],
            "promotions": [{"start_date": "2026-10-13", "end_date": "2026-10-01"}],
        }
        for _ in range(12)
    ]
    problems = plausibility.check(records, schema)
    text = "\n".join(problems)
    assert "/prices/*/effective_to: 12 of 12 dates have a year outside 1990-2100" in text
    assert "/promotions/*/start_date is after /promotions/*/end_date in 12 of 12" in text
    assert "/prices/*/amount: 12 of 12 values are zero or negative" in text
    assert "/sku: only 1 distinct value(s) across 12 records" in text
    assert "/product_name: empty in 12 of 12 records" in text
    assert plausibility.check([], schema) == []
    mixed = [
        {"sku": f"A{i}", "product_name": "x", "prices": [{"amount": 10}] if i < 5 else []} for i in range(12)
    ]
    assert plausibility.check(mixed, schema) == ["/prices: 7 of 12 records have no price while the others do"]
    clean = [
        {"sku": f"A{i}", "product_name": "x", "prices": [{"amount": 10, "effective_from": "2026-10-01"}]}
        for i in range(12)
    ]
    assert plausibility.check(clean, schema) == []
    assert list(plausibility.values_at({"a": [{"b": 1}, {"b": 2}, {}]}, "/a/*/b")) == [1, 2]


# --------------------------------------------------------------------------------------------
def _book(path: Path) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "สินค้า"
    ws.append(["รหัสสินค้า", "ชื่อสินค้า", "ราคาขาย", "สถานะ"])
    for i in range(1, 7):
        ws.append([f"A{i:03d}", f"สินค้า {i}", f"{i * 10}.50", "ขาย" if i % 3 else "เลิกขาย"])
    wb.save(path)
    return path


def _bench(tmp_path: Path, schema: TargetSchema) -> tuple[FileWorkbench, Any]:
    from forma.services.mapping import bind_sources

    path = _book(tmp_path / "products.xlsx")
    f = SimpleNamespace(
        id="f1",
        status="profiled",
        storage_key=str(path),
        kind="excel",
        original_name=path.name,
        profile=None,
        fingerprint=None,
    )
    profile = profile_workbook(path)
    bench = FileWorkbench(
        schema, profile, [f], bind=lambda d: bind_sources(d, [f], resolve=Path), resolve=Path, rows=50
    )
    return bench, f


def test_file_workbench_answers_about_the_file(tmp_path: Path, schema: TargetSchema) -> None:
    bench, _ = _bench(tmp_path, schema)
    out = bench.inspect(
        [
            {"table_index": 0, "what": "columns"},
            {"table_index": 0, "what": "distinct", "column": "สถานะ"},
            {"table_index": 0, "what": "rows", "start": 1, "limit": 2},
            {"table_index": 0, "what": "find", "pattern": "เลิก"},
            {"table_index": 0, "what": "distinct", "column": "nope"},
            {"table_index": 0, "what": "weird"},
        ]
    )
    assert [c["name"] for c in out[0]["answer"]] == ["รหัสสินค้า", "ชื่อสินค้า", "ราคาขาย", "สถานะ"]
    assert out[1]["answer"]["distinct"] == 2 and out[1]["answer"]["values"][0] == {"value": "ขาย", "count": 4}
    assert [r["รหัสสินค้า"] for r in out[2]["answer"]["rows"]] == ["A002", "A003"] and out[2]["answer"][
        "total_rows"
    ] == 6
    assert [h["row"] for h in out[3]["answer"]["matches"]] == [2, 5]
    assert "no column 'nope'" in out[4]["error"] and "what must be one of" in out[5]["error"]


def test_file_workbench_runs_the_mapping_and_reports(tmp_path: Path, schema: TargetSchema) -> None:
    bench, _ = _bench(tmp_path, schema)
    doc = _doc()
    doc.sources[0].sheet = None
    doc.sources[0].file.sheet_names_any = []
    doc.collections[0].fields[1].expr = call("parse_number_th", col("ราคาขาย"))
    report = bench.run(doc)
    assert report.clean and report.rows_in == 6 and report.rows_out == 6
    assert report.sample[0]["sku"] == "A001" and "source_meta" not in report.sample[0]
    doc.fields[1].expr = col("ชื่อ")  # a column the file does not have
    report = bench.run(doc)
    assert any("source column 'products.ชื่อ' not found" in p for p in report.problems)
    doc.fields[1].expr = col("ชื่อสินค้า")
    doc.record.filters = [call("eq", col("สถานะ"), const("never"))]
    report = bench.run(doc)
    assert report.problems[0] == "the mapping produced 0 records from 6 source rows" and report.rows_out == 0
    assert report.to_prompt()["sample_records"] == []


def test_model_confidence_cannot_settle_a_business_question(schema: TargetSchema) -> None:
    from forma.services.mapping import _without_settled

    q = [
        "Price column 'ราคา': no VAT marker in headers; assumed VAT-inclusive (Thai retail convention) — confirm",
        "Pricing table 'A304': no VAT marker in headers; assumed VAT-inclusive (Thai retail convention) — confirm",
        "other",
    ]
    doc = _doc()
    vat = next(f for c in doc.collections for f in c.fields if f.target.endswith("/vat_included"))
    vat.confidence, vat.evidence = 0.95, ["Info sheet: net price excluding VAT 7%"]
    assert _without_settled(list(q), doc, schema) == q
    vat.confidence = 0.6
    assert _without_settled(list(q), doc, schema) == q


def test_choice_alternatives_fit_the_field(schema: TargetSchema) -> None:
    from forma.services.review_questions import _compatible

    flag = next(f for f in schema.leaf_fields() if f.semantic == "price.vat_included")
    qty = next(f for f in schema.leaf_fields() if f.semantic == "stock.qty")
    name = next(f for f in schema.leaf_fields() if f.semantic == "product_name")
    assert _compatible(flag, {"inferred_type": "bool"}) and not _compatible(flag, {"inferred_type": "int"})
    assert _compatible(qty, {"inferred_type": "int"}) and not _compatible(qty, {"inferred_type": "string"})
    assert _compatible(name, {"inferred_type": "string"})
    assert not _compatible(name, {"inferred_type": "int", "hints": ["sku_like"]})  # a stock count, not a name
    assert not _compatible(name, {"inferred_type": "empty"})
