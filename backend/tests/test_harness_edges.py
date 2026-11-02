"""ADR-060 edges, one mechanism at a time: inspection kinds and their bounds, run scores and full
verification, contract checks, typed clarifications, reading signatures, file binding and rebinding,
learned constants and anchors, the benchmark manifest, the runner pool and the mapping lab executor."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from tests.test_agents import _doc, _draft, _profile
from tests.test_self_check import FakeBench, _bench, _responder

from forma.ai import agents
from forma.ai.clarifications import validate as validate_clarifications
from forma.ai.contract_checks import evaluate as evaluate_checks
from forma.ai.contract_checks import validate_checks
from forma.ai.evidence import collection_signature, reading_core, reading_signature, record_signature
from forma.ai.llm import LLMClient, MockProvider
from forma.ai.policy import PolicyDecision
from forma.ai.reconciliation import coverage, regressions, require_verification, verification
from forma.ai.workbench import RunReport, inspect_problems
from forma.code_sandbox.client import RunnerPool, RunResult, SandboxBusy, SandboxUnavailable
from forma.core.settings import Settings, get_settings
from forma.eval import benchmark
from forma.mapping_dsl.models import FieldMapping, JoinKey, SheetSelect, call, col, const
from forma.schema_registry import TargetSchema
from forma.services import format_memory, mapping_lab
from forma.services.learning import Anchors, Learned
from forma.services.mapping import _match_file

FIXTURES = Path(__file__).parent / "fixtures" / "harness"


# --------------------------------------------------------------------------------------------
# inspection: raw cells, samples, literal find, truncation, structure, trial tables
# --------------------------------------------------------------------------------------------
def test_raw_cells_come_with_addresses_and_a_bounded_window(tmp_path: Path, schema: TargetSchema) -> None:
    bench, _ = _bench(tmp_path, schema)  # 4 columns, a header and 6 rows
    out = bench.inspect(
        [{"table_index": 0, "what": "raw", "start": 1, "limit": 400, "column_start": 2, "column_limit": 99}]
    )
    answer = out[0]["answer"]
    assert answer["total_rows"] == 7 and answer["total_columns"] == 4
    assert answer["cells"][0]["cell"] == "C2"  # column_start 2 → C, start 1 → sheet row 2
    assert len(answer["cells"]) == 6 * 2  # six rows remain below the header, two columns from C
    assert out[0]["source"]["sheet"] == "สินค้า" and out[0]["source"]["table_index"] == 0
    assert "untrusted" in answer["note"]


def test_sample_spans_the_table_and_find_is_literal(tmp_path: Path, schema: TargetSchema) -> None:
    bench, _ = _bench(tmp_path, schema)
    sample = bench.inspect([{"table_index": 0, "what": "sample"}])[0]["answer"]
    assert sample["strategy"] == "evenly_spaced_including_tail" and sample["total_rows"] == 6
    assert sample["rows"][0]["table_row"] == 0 and sample["rows"][-1]["table_row"] == 5
    hits = bench.inspect([{"table_index": 0, "what": "find", "pattern": "า 1"}])[0]["answer"]
    assert [h["row"] for h in hits["matches"]] == [0]  # "สินค้า 1" only; "สินค้า 10" does not exist
    literal = bench.inspect([{"table_index": 0, "what": "find", "pattern": "(ขาย)"}])[0]["answer"]
    assert literal["matches"] == []  # parentheses are text, not a regular expression group
    distinct = bench.inspect([{"table_index": 0, "what": "distinct", "column": "รหัสสินค้า", "limit": 2}])[0]
    assert distinct["answer"]["truncated"] is True and distinct["answer"]["distinct"] == 6
    assert distinct["answer"]["limit"] == 2 and len(distinct["answer"]["values"]) == 2


def test_structure_and_a_bad_trial_table_are_answers_not_crashes(
    tmp_path: Path, schema: TargetSchema
) -> None:
    bench, _ = _bench(tmp_path, schema)
    out = bench.inspect(
        [
            {"table_index": 0, "what": "structure"},
            {"table_index": 0, "what": "trial_table", "table": {"header_row": "nope"}},
            {"table_index": 0, "what": "trial_table", "table": {"header_row": 2}},
            {"table_index": 9, "what": "columns"},
        ]
    )
    structure = out[0]["answer"]
    assert structure["rows"] == 7 and structure["columns"] == 4 and len(structure["profiled_tables"]) == 1
    assert "error" in out[1] and "answer" not in out[1]
    assert out[2]["answer"]["rows"] == 4 and out[2]["answer"]["table"]["header_row"] == 2
    assert "error" in out[3]


def test_inspect_requests_are_validated_before_they_run() -> None:
    problems = inspect_problems(
        [
            {"table_index": 0, "what": "raw", "start": -1},
            {"table_index": 0, "what": "rows", "limit": "5"},
            {"table_index": 0, "what": "find"},
            {"table_index": 0, "what": "distinct"},
            {"table_index": 1, "what": "sample"},
            {"table_index": 0, "what": "trial_table", "column_limit": True},
        ],
        1,
    )
    assert any("start must be a non-negative integer" in p for p in problems)
    assert any("limit must be a non-negative integer" in p for p in problems)
    assert any("find needs literal text" in p for p in problems)
    assert any("distinct needs a column name" in p for p in problems)
    assert any("table_index must be an integer from 0 to 0" in p for p in problems)
    assert any("column_limit must be" in p for p in problems)
    assert inspect_problems([], 1) and inspect_problems("columns", 1)


# --------------------------------------------------------------------------------------------
# run reports, full verification, coverage regressions
# --------------------------------------------------------------------------------------------
def test_run_report_score_never_prefers_an_empty_or_broken_result() -> None:
    empty = RunReport(50, 0, ["the mapping produced 0 records from 50 source rows"])
    noisy = RunReport(50, 48, ["a", "b", "c", "coverage: /sku fell from 50 to 48 populated records"])
    broken = RunReport(50, 48, ["engine: boom"], errors=1)
    assert noisy.score < empty.score and noisy.score < broken.score
    assert noisy.to_dict()["problem_count"] == 4 and noisy.to_dict()["complete"] is True
    assert RunReport(50, 50).clean and RunReport(50, 50).score == (0, 0, 0)


def test_verify_runs_the_whole_file_and_reports_coverage(tmp_path: Path, schema: TargetSchema) -> None:
    bench, _ = _bench(tmp_path, schema)
    doc = _doc()
    doc.sources[0].sheet = None
    doc.sources[0].file.sheet_names_any = []
    doc.collections[0].fields[1].expr = call("parse_number_th", col("ราคาขาย"))
    sample = bench.run(doc)
    full = bench.verify(doc)
    assert sample.complete is False and full.complete is True
    assert full.rows_in == 6 and full.coverage["fields"]["/sku"] == {
        "records_filled": 6,
        "records": 6,
        "risk": "critical",
    }
    assert full.coverage["collections"]["/prices"] == 6 and full.errors == 0
    assert "/prices/*/amount" in full.coverage["fields"]


def test_verification_report_fails_closed(schema: TargetSchema) -> None:
    missing = verification(None, schema, complete=True)
    assert missing["passed"] is False and missing["problems"] == ["No transformation result was verified"]

    def result(records: list[dict[str, Any]], findings: list[Any] | None = None) -> Any:
        return SimpleNamespace(
            errors=[f for f in findings or [] if f.severity == "error"],
            findings=findings or [],
            records=records,
            stats={"rows_in": 10, "sources": {}},
        )

    good = [{"sku": f"A{i}", "product_name": f"n{i}", "prices": [{"amount": 1.0}]} for i in range(10)]
    ok = verification(result(good), schema, complete=True)
    assert (
        ok["passed"]
        and ok["complete"]
        and ok["rows_out"] == 10
        and ok["coverage"]["fields"]["/sku"]["records_filled"] == 10
    )
    partial = verification(result(good), schema, complete=False)
    assert not partial["passed"] and "Only a sample was checked" in partial["problems"]
    empty = verification(result([]), schema, complete=True)
    assert "No output records" in empty["problems"]
    orphan = SimpleNamespace(severity="warning", message="/inventory: 6 items reference unknown root records")
    with_orphans = verification(result(good, [orphan]), schema, complete=True)
    assert not with_orphans["passed"] and orphan.message in with_orphans["problems"]
    carried = verification(result(good), schema, complete=True, self_check={"problems_left": ["left over"]})
    assert "left over" in carried["problems"]
    bound = verification(result(good), schema, complete=True, binding_problems=["binding: no file"])
    assert bound["problems"][0] == "binding: no file" and not bound["passed"]
    implausible = [{**r, "prices": [{"amount": -5.0}]} for r in good]
    report = verification(result(implausible), schema, complete=True)
    assert report["passed"] and any("zero or negative" in w for w in report["warnings"])  # a warning class


def test_require_verification_blocks_automation_and_marks_the_risk() -> None:
    d = PolicyDecision("auto_approve", 0, ["all policy checks passed"])
    require_verification(d, {"complete": True, "passed": False, "problems": ["p1", "p2"]})
    assert d.decision == "review" and d.checks["full_verification"] is False and d.risk_level == "high"
    assert d.reasons[-2:] == ["verification: p1", "verification: p2"] and d.priority == 40
    ok = PolicyDecision("auto_approve", 0)
    require_verification(ok, {"complete": True, "passed": True, "problems": []})
    assert ok.decision == "auto_approve" and ok.checks["full_verification"] is True
    # plausibility warnings: a new mapping goes to review, a mapping reused from an approval keeps the budget
    fresh = PolicyDecision("auto_approve", 0)
    require_verification(fresh, {"complete": True, "passed": True, "problems": [], "warnings": ["w1"]})
    assert (
        fresh.decision == "review" and fresh.reasons == ["plausibility: w1"] and fresh.risk_level == "medium"
    )
    reused = PolicyDecision("auto_approve", 0)
    require_verification(
        reused, {"complete": True, "passed": True, "problems": [], "warnings": ["w1"]}, reused=True
    )
    assert reused.decision == "auto_approve" and reused.checks["plausibility_warnings"] == 1
    blocked = PolicyDecision("block", 100, ["wrong file"])
    require_verification(blocked, {"complete": False, "passed": False, "problems": ["sample"]})
    assert blocked.decision == "block" and blocked.priority == 100  # a block is already the stronger answer


def test_regressions_watch_high_risk_fields_and_collections_only(schema: TargetSchema) -> None:
    low = next(f.pointer for f in schema.leaf_fields() if f.risk in ("low", "medium") and not f.in_collection)
    rel = low.lstrip("/")
    before = coverage([{"sku": str(i), rel: "x", "prices": [{"amount": 1}]} for i in range(3)], schema, {})
    after = coverage(
        [{"sku": str(i), "prices": [{"amount": 1}], "brand": "new"} for i in range(3)], schema, {}
    )
    assert regressions(before, after) == []  # a low-risk field emptied and a new field do not count
    fewer = coverage([{"sku": "0", "prices": [{"amount": 1}]}], schema, {})
    problems = regressions(before, fewer)
    assert any("/sku fell from 3 to 1" in p for p in problems) and any(
        "/prices lost 2 collection items" in p for p in problems
    )
    assert regressions({}, after) == [] and before["sources"] == {} and before["invalid_records"] == 0


# --------------------------------------------------------------------------------------------
# contract checks (x-forma.checks) and typed clarifications
# --------------------------------------------------------------------------------------------
def _contract(*checks: dict[str, Any]) -> dict[str, Any]:
    return {"x-forma": {"checks": list(checks)}}


def test_contract_check_validation_fails_closed() -> None:
    assert validate_checks({}) == [] and validate_checks(_contract()) == []
    many = _contract(*[{"id": f"c{i}", "rule": "equals", "left": "/a", "right": "/b"} for i in range(101)])
    assert validate_checks(many) == ["x-forma.checks must be a list of at most 100 rules"]
    dup = _contract(
        {"id": "x", "rule": "equals", "left": "/a", "right": "/b"},
        {"id": "x", "rule": "equals", "left": "/a", "right": "/b"},
    )
    assert any("unique" in e for e in validate_checks(dup))
    assert any(
        "JSON pointer" in e
        for e in validate_checks(_contract({"id": "p", "rule": "equals", "left": "a", "right": "/b"}))
    )
    assert any(
        "tolerance" in e
        for e in validate_checks(
            _contract({"id": "t", "rule": "equals", "left": "/a", "right": "/b", "tolerance": -1})
        )
    )
    assert any(
        "tolerance" in e
        for e in validate_checks(
            _contract({"id": "t2", "rule": "equals", "left": "/a", "right": "/b", "tolerance": "abc"})
        )
    )
    assert any(
        "Unsupported" in e
        for e in validate_checks(_contract({"id": "r", "rule": "greater", "left": "/a", "right": "/b"}))
    )
    assert validate_checks({"x-forma": {"checks": ["not an object"]}}) == [
        "Each contract check must be an object"
    ]
    assert validate_checks({"x-forma": {"checks": "nope"}})
    # an invalid contract fails every record, with the reason, instead of silently passing
    bad = evaluate_checks([{"a": 1}], _contract({"id": "r", "rule": "greater", "left": "/a", "right": "/b"}))
    assert bad and bad[0]["row"] is None and "Unsupported" in bad[0]["message"]


def test_contract_check_rules_compare_values_strictly() -> None:
    equals = _contract({"id": "eq", "rule": "equals", "left": "/a", "right": "/b"})
    assert evaluate_checks([{"a": "x", "b": "x"}, {"a": 2, "b": 2}], equals) == []
    assert evaluate_checks([{"a": 1, "b": 1.0}], equals)  # an integer is not a float
    assert evaluate_checks([{"a": 1}], equals)  # a missing side never passes
    assert evaluate_checks([{"a": None, "b": None}], equals)
    le = _contract(
        {"id": "le", "rule": "less_equal", "left": "/discount", "right": "/price", "tolerance": 0.5}
    )
    assert evaluate_checks([{"discount": 10, "price": 9.6}], le) == []  # within tolerance
    assert evaluate_checks([{"discount": 10, "price": 9}], le)
    assert evaluate_checks([{"discount": 10, "price": True}], le)  # booleans are not amounts
    many_right = _contract({"id": "m", "rule": "less_equal", "left": "/total", "right": "/lines/*/amount"})
    assert evaluate_checks(
        [{"total": 1, "lines": [{"amount": 5}, {"amount": 5}]}], many_right
    )  # one value only
    total = _contract(
        {"id": "sum", "rule": "sum_equals", "left": "/total", "right": "/lines/*/amount", "tolerance": 0.01}
    )
    assert evaluate_checks([{"total": "30.00", "lines": [{"amount": "10.0"}, {"amount": 20}]}], total) == []
    failing = evaluate_checks(
        [
            {"total": 30, "lines": [{"amount": 10}, {"amount": 20}]},
            {"total": 31, "lines": [{"amount": 10}, {"amount": 20}]},
        ],
        total,
    )
    assert failing == [
        {
            "message": "Contract check sum failed for 1 records: /total sum_equals /lines/*/amount",
            "row": 1,
            "target": "/total",
        }
    ]
    multi_left = _contract({"id": "ml", "rule": "equals", "left": "/lines/*/amount", "right": "/total"})
    assert evaluate_checks(
        [{"total": 10, "lines": [{"amount": 10}, {"amount": 10}]}], multi_left
    )  # the left must be one value


def test_clarification_validation_edges(schema: TargetSchema) -> None:
    doc = _doc()
    base = {
        "target": "/product_name",
        "question": "Which name is printed?",
        "why": "Two labels",
        "kind": "text",
    }
    assert validate_clarifications([base], doc, schema) == []
    assert validate_clarifications([{**base, "kind": "number"}], doc, schema) == [
        "Clarification type for /product_name must match the target schema"
    ]
    assert validate_clarifications([{**base, "kind": "boolean"}], doc, schema)
    assert validate_clarifications([{**base, "kind": "enum", "options": ["A"]}], doc, schema) == [
        "Clarification /product_name needs at least two distinct choices"
    ]
    assert validate_clarifications([{**base, "kind": "enum", "options": ["A", "A"]}], doc, schema)
    assert validate_clarifications([{**base, "kind": "enum", "options": ["A", "B"]}], doc, schema) == []
    assert validate_clarifications([{**base, "question": "   "}], doc, schema) == [
        "Clarification /product_name must explain the missing business fact"
    ]
    assert validate_clarifications([{**base, "target": "/brand"}], doc, schema)  # mapped fields only
    assert validate_clarifications([{**base, "target": "/nope"}], doc, schema)
    assert validate_clarifications([{**base, "extra": 1}], doc, schema)  # no stray keys
    assert validate_clarifications([base] * 13, doc, schema)  # at most twelve
    money = {"target": "/prices/*/amount", "question": "Unit price?", "why": "Blank column", "kind": "number"}
    flag = {
        "target": "/prices/*/vat_included",
        "question": "VAT included?",
        "why": "No marker",
        "kind": "boolean",
    }
    assert validate_clarifications([money, flag], doc, schema) == []
    assert validate_clarifications("not a list", doc, schema)


# --------------------------------------------------------------------------------------------
# reading signatures: aliases do not matter, selection and arithmetic do
# --------------------------------------------------------------------------------------------
def test_reading_signature_ignores_source_aliases_but_not_what_is_read() -> None:
    a = _doc()
    renamed = _doc()
    renamed.sources[0].id = "raw"
    renamed.record.root_source = "raw"
    renamed.collections[0].source = "raw"
    for target in ("/sku", "/prices/*/amount", "/currency"):
        assert reading_signature(a, target) == reading_signature(renamed, target)
    filtered = _doc()
    filtered.record.filters = [call("eq", col("สถานะ"), const("ขาย"))]
    # a root filter or a dedupe key selects records; it does not change what a field reads
    assert reading_signature(a, "/sku") == reading_signature(filtered, "/sku")
    assert record_signature(a) != record_signature(filtered)
    deduped = _doc()
    deduped.record.dedupe.keys = ["/sku"]
    assert reading_signature(a, "/currency") == reading_signature(deduped, "/currency")
    assert record_signature(a) != record_signature(deduped) and "dedupe" in str(record_signature(deduped))
    relinked = _doc()
    relinked.collections[0].link = [JoinKey(left="รหัสสินค้า", right="บาร์โค้ด")]
    assert reading_signature(a, "/sku") == reading_signature(relinked, "/sku")
    assert reading_signature(a, "/prices/*/amount") != reading_signature(relinked, "/prices/*/amount")
    other_sheet = _doc()
    other_sheet.sources[0].sheet = SheetSelect(name="Other")
    assert reading_signature(a, "/sku") != reading_signature(other_sheet, "/sku")
    wrapped = _doc()
    wrapped.fields[0].expr = call("normalize_whitespace", call("cast", call("trim", col("รหัสสินค้า"))))
    assert reading_signature(a, "/sku") != reading_signature(wrapped, "/sku")  # the text differs ...
    assert reading_core(a, "/sku") == reading_core(wrapped, "/sku")  # ... the reading does not
    stripped = _doc()
    stripped.fields[0].expr = call("strip_leading_zeros", call("trim", col("รหัสสินค้า")))
    assert reading_core(a, "/sku") != reading_core(stripped, "/sku")  # zeros gone: another identity
    scaled = _doc()
    scaled.collections[0].fields[1].expr = call("multiply", scaled.collections[0].fields[1].expr, const(1000))
    assert reading_core(a, "/prices/*/amount") != reading_core(scaled, "/prices/*/amount")
    other_col = _doc()
    other_col.fields[1].expr = col("สถานะ")
    assert reading_core(a, "/product_name") != reading_core(other_col, "/product_name")
    item_dedupe = _doc()
    item_dedupe.collections[0].dedupe_keys = ["/price_list"]
    assert reading_core(a, "/prices/*/amount") == reading_core(item_dedupe, "/prices/*/amount")
    assert collection_signature(a) != collection_signature(item_dedupe)
    assert reading_signature(a, "/not_mapped") == {} and reading_core(a, "/not_mapped") == {}


# --------------------------------------------------------------------------------------------
# binding files: every selector must hold; rebinding an approved mapping to renamed files
# --------------------------------------------------------------------------------------------
def _file(**kw: Any) -> Any:
    base = {
        "id": "f",
        "original_name": "x.xlsx",
        "profile": None,
        "fingerprint": None,
        "kind": "excel",
        "status": "profiled",
    }
    return SimpleNamespace(**{**base, **kw})


def test_match_file_requires_every_selector_to_hold() -> None:
    source = _doc().sources[0].model_copy(deep=True)
    source.sheet = None
    source.file.sheet_names_any = []
    source.file.fingerprint = "abc"
    assert _match_file(source, [_file()]) is None  # one file is no shortcut past a declared fingerprint
    assert _match_file(source, [_file(fingerprint={"strict": "abc"})]) is not None
    assert _match_file(source, [_file(fingerprint={"strict": "zzz"})]) is None
    by_sheet = _doc().sources[0].model_copy(deep=True)  # sheet "สินค้า"
    by_sheet.file.sheet_names_any = []
    with_sheet = _file(id="s", profile={"meta": {"sheets": [{"name": "สินค้า", "index": 0}]}})
    without = _file(id="w", profile={"meta": {"sheets": [{"name": "Stock", "index": 0}]}})
    assert _match_file(by_sheet, [with_sheet, without]) is with_sheet
    assert (
        _match_file(
            by_sheet, [without, _file(id="n", profile={"meta": {"sheets": [{"name": "Other", "index": 0}]}})]
        )
        is None
    )
    broken = _doc().sources[0].model_copy(deep=True)
    broken.sheet = None
    broken.file.sheet_names_any = []
    broken.file.name_pattern = "("
    assert _match_file(broken, [_file(), _file(id="g")]) is None
    csv_first = _doc().sources[0].model_copy(deep=True)
    csv_first.sheet = None
    csv_first.file.sheet_names_any = []
    csv_first.file.name_pattern = r"^items"
    plain = _file(id="x", original_name="items.xlsx")
    comma = _file(id="c", original_name="items.csv", kind="csv")
    assert _match_file(csv_first, [plain, comma]) is comma  # a sheetless source prefers the CSV
    assert _match_file(csv_first, []) is None


def test_rebind_approved_is_for_approved_versions_with_one_structural_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doc = _doc()
    doc.sources[0].file.name_pattern = "^old\\.xlsx$"
    fp = {"strict": "old", "loose": "old", "tables": [], "kind": "excel"}
    renamed = _file(id="f1", original_name="new.xlsx", fingerprint=fp)
    proposed = SimpleNamespace(
        document=doc.model_dump(),
        status="proposed",
        approved_at=None,
        policy={"file_fingerprints": {"f": fp}},
    )
    assert format_memory.rebind_approved(proposed, [renamed]).sources[0].file.name_pattern == "^old\\.xlsx$"
    approved = SimpleNamespace(
        document=doc.model_dump(),
        status="approved",
        approved_at=None,
        policy={"file_fingerprints": {"f": fp}},
    )
    monkeypatch.setattr(
        format_memory, "compare", lambda *a: SimpleNamespace(classification="identical", similarity=1.0)
    )
    rebound = format_memory.rebind_approved(approved, [renamed])
    assert (
        rebound.sources[0].file.name_pattern == "^new\\.xlsx$" and rebound.sources[0].file.fingerprint is None
    )
    assert _match_file(rebound.sources[0], [renamed]) is renamed
    monkeypatch.setattr(
        format_memory, "compare", lambda *a: SimpleNamespace(classification="columns_renamed", similarity=0.5)
    )
    assert (
        format_memory.rebind_approved(approved, [renamed], for_comparison=True).sources[0].file.name_pattern
        == "^old\\.xlsx$"
    )
    unprofiled = _file(id="f2", original_name="new.xlsx", fingerprint=fp, status="uploaded")
    monkeypatch.setattr(
        format_memory, "compare", lambda *a: SimpleNamespace(classification="identical", similarity=1.0)
    )
    assert (
        format_memory.rebind_approved(approved, [unprofiled]).sources[0].file.name_pattern == "^old\\.xlsx$"
    )


# --------------------------------------------------------------------------------------------
# learned knowledge stays inside its contract; conflicting anchors are not aliases
# --------------------------------------------------------------------------------------------
def test_conflicting_anchors_stay_suggestions() -> None:
    anchors = Anchors()
    for c in ("c1", "c2", "c3"):
        anchors.add("code", "/sku", c)
    anchors.add("code", "/barcode", "c4")  # one customer reads the same header as the barcode
    anchors.add("name", "/product_name", "c1")
    anchors.add("name", "/product_name", "c2")
    learned = Learned()
    learned.absorb(anchors, {"code", "name"}, share_after=2)
    assert learned.alias_for("/sku", "code") is None and learned.alias_for("/product_name", "name")
    assert {(a["header"], a["target"]) for a in learned.anchors} == {("code", "/sku"), ("code", "/barcode")}


# --------------------------------------------------------------------------------------------
# the agent loop: python turns are budgeted, repairs that lose data are not accepted
# --------------------------------------------------------------------------------------------
def test_python_turns_are_budgeted(monkeypatch: pytest.MonkeyPatch, schema: TargetSchema) -> None:
    monkeypatch.setenv("FORMA_MAPPING_LAB_TURNS", "1")
    get_settings.cache_clear()
    bench = FakeBench([RunReport(6, 6)])
    runs: list[str] = []
    bench.execute = lambda code: runs.append(code) or {"status": "ok", "stdout": "A1=code"}
    prompts: list[str] = []
    good = _doc().model_dump(mode="json", exclude_none=True)
    replies = [
        {"mapping": None, "python": "print(1)"},
        {"mapping": None, "python": "print(2)"},  # refused: no turns left → the model must answer
        {"mapping": good, "confidence": 0.9},
    ]
    client = LLMClient(MockProvider(_responder(replies, prompts)), model="m")
    profile = _profile({"สินค้า": [("รหัสสินค้า", "string"), ("ชื่อสินค้า", "string"), ("ราคาขาย", "float")]})
    _, res = agents.mapping_agent(client, profile, schema, _draft(), workbench=bench)
    assert runs == ["print(1)"] and res.data["self_check"]["python_turns"] == 1
    assert json.loads(prompts[1])["limits"]["python_turns_left"] == 0
    assert "No Python turns available" in prompts[2]
    get_settings.cache_clear()


def test_without_an_executor_python_is_refused_and_no_turn_budget_is_shown(schema: TargetSchema) -> None:
    bench = FakeBench([RunReport(6, 6)])  # no execute attribute
    prompts: list[str] = []
    good = _doc().model_dump(mode="json", exclude_none=True)
    replies = [{"mapping": None, "python": "print(1)"}, {"mapping": good, "confidence": 0.9}]
    client = LLMClient(MockProvider(_responder(replies, prompts)), model="m")
    profile = _profile({"สินค้า": [("รหัสสินค้า", "string"), ("ชื่อสินค้า", "string"), ("ราคาขาย", "float")]})
    _, res = agents.mapping_agent(client, profile, schema, _draft(), workbench=bench)
    assert "python_turns_left" not in json.loads(prompts[0])["limits"]
    assert "No Python turns available" in prompts[1] and res.data["self_check"]["python_turns"] == 0


def test_a_repair_that_drops_data_does_not_beat_the_earlier_candidate(schema: TargetSchema) -> None:
    full = {
        "fields": {"/sku": {"records_filled": 6, "records": 6, "risk": "critical"}},
        "collections": {"/prices": 6},
    }
    thin = {
        "fields": {"/sku": {"records_filled": 2, "records": 2, "risk": "critical"}},
        "collections": {"/prices": 2},
    }

    class Bench(FakeBench):
        def run(self, doc: Any) -> RunReport:  # the sample looks fine every time
            self.ran.append(doc)
            return RunReport(6, 6, complete=False)

        def verify(self, doc: Any) -> RunReport:  # the whole file tells the truth
            return self.reports.pop(0)

    bench = Bench(
        [
            RunReport(6, 6, coverage=full, complete=True),  # the heuristic baseline
            RunReport(
                6, 6, ["/prices/*/amount: 3 of 6 values are zero or negative"], coverage=full, complete=True
            ),
            RunReport(6, 2, coverage=thin, complete=True),  # the "fix" filtered the problem away
            RunReport(6, 2, coverage=thin, complete=True),  # ... and insisted
        ]
    )
    good = _doc().model_dump(mode="json", exclude_none=True)
    fixed = {
        **good,
        "record": {
            **good["record"],
            "filters": [
                {"op": "gt", "args": [{"op": "col", "name": "ราคาขาย"}, {"op": "const", "value": 0}]}
            ],
        },
    }
    prompts: list[str] = []
    client = LLMClient(
        MockProvider(_responder([{"mapping": good}, {"mapping": fixed}, {"mapping": fixed}], prompts)),
        model="m",
    )
    profile = _profile({"สินค้า": [("รหัสสินค้า", "string"), ("ชื่อสินค้า", "string"), ("ราคาขาย", "float")]})
    doc, res = agents.mapping_agent(client, profile, schema, _draft(), workbench=bench)
    sc = res.data["self_check"]
    assert doc.record.filters == []  # the first candidate stayed: fewer records is a regression, not a fix
    assert any(
        p.startswith("coverage:") for h in sc["history"] if h["turn"] == "mapping" for p in h["problems"]
    )
    assert sc["complete"] is True and sc["coverage"] == full


# --------------------------------------------------------------------------------------------
# benchmark: manifests, splits, baselines, scoring
# --------------------------------------------------------------------------------------------
def _fixtures(tmp_path: Path) -> Path:
    root = tmp_path / "harness"
    shutil.copytree(FIXTURES, root)
    return root


def _manifest(root: Path, name: str, cases: list[dict[str, Any]], **extra: Any) -> Path:
    path = root / name
    path.write_text(json.dumps({"training_families": [], "cases": cases, **extra}), encoding="utf-8")
    return path


def _case(**kw: Any) -> dict[str, Any]:
    return {
        "id": "c",
        "family": "dispatch",
        "split": "cold",
        "schema_id": "dispatch",
        "schema": "schema.json",
        "files": ["dispatch.csv"],
        "expected": "gold.json",
        **kw,
    }


def test_benchmark_manifest_rules(tmp_path: Path) -> None:
    root = _fixtures(tmp_path)
    with pytest.raises(ValueError, match="approved_mapping"):
        benchmark.run(_manifest(root, "warm.json", [_case(split="warm")]))
    with pytest.raises(ValueError, match="unique"):
        benchmark.run(_manifest(root, "dup.json", [_case(), _case()]))
    with pytest.raises(ValueError, match="split"):
        benchmark.run(_manifest(root, "hot.json", [_case(split="hot")]))
    with pytest.raises(ValueError, match="nonempty"):
        benchmark.run(_manifest(root, "empty.json", []))
    with pytest.raises(ValueError, match="leaks"):
        benchmark.run(_manifest(root, "leak.json", [_case(approved_mapping="m.json")]))


def test_benchmark_warm_drift_and_baseline_scoring(tmp_path: Path) -> None:
    root = _fixtures(tmp_path)
    cold = benchmark.run(_manifest(root, "cold.json", [_case()]))
    assert cold["summary"]["cold"] == {"cases": 1, "mean_field_accuracy": 1.0} and cold["prompt_version"]
    (root / "approved.json").write_text(json.dumps(cold["cases"][0]["mapping"]), encoding="utf-8")
    gold = json.loads((root / "gold.json").read_text(encoding="utf-8"))
    gold[0]["cartons"] = 99
    (root / "baseline.json").write_text(json.dumps(gold), encoding="utf-8")
    report = benchmark.run(
        _manifest(
            root,
            "mix.json",
            [
                _case(
                    id="warm", split="warm", approved_mapping="approved.json", baseline_output="baseline.json"
                ),
                _case(
                    id="drift",
                    split="drift",
                    approved_mapping="approved.json",
                    files=["reordered.csv"],
                    drift="columns_moved",
                ),
                _case(id="given", mapping="approved.json"),
            ],
            baseline_label="vendor X",
        )
    )
    by_id = {c["id"]: c for c in report["cases"]}
    assert by_id["warm"]["trace"]["route"] == "explicit_approved_adapter_reuse"
    assert (
        by_id["warm"]["metrics"]["field_accuracy"] == 1.0
        and by_id["warm"]["baseline"]["field_accuracy"] < 1.0
    )
    assert by_id["warm"]["field_accuracy_delta"] > 0 and report["baseline_label"] == "vendor X"
    assert by_id["drift"]["metrics"]["field_accuracy"] == 1.0  # no client: the deterministic mapper answers
    assert "open_questions" in by_id["drift"]["trace"]
    assert by_id["given"]["metrics"]["exact_record_accuracy"] == 1.0
    assert report["summary"]["warm"]["cases"] == 1 and report["summary"]["drift"]["cases"] == 1
    assert all(c["verification"]["complete"] for c in report["cases"]) and report["manifest_sha256"]
    assert set(by_id["warm"]["input_sha256"]) == {"dispatch.csv"} and by_id["warm"]["gold_sha256"]


def test_benchmark_score_edges() -> None:
    document = json.loads((FIXTURES / "schema.json").read_text(encoding="utf-8"))
    schema = TargetSchema("dispatch", 1, document, FIXTURES / "schema.json")
    gold = json.loads((FIXTURES / "gold.json").read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="duplicate identities"):
        benchmark.score(gold, [*gold, gold[0]], schema)
    extra = [*gold, {**gold[0], "dispatch_id": "0009"}]
    metrics = benchmark.score(extra, gold, schema)
    assert (
        metrics["identity_recall"] == 1.0
        and metrics["identity_precision"] == 0.75
        and metrics["duplicate_identities"] == 0
    )
    assert metrics["exact_record_accuracy"] == 1.0 and metrics["critical_accuracy"] == 1.0
    assert benchmark.score([], gold, schema)["identity_recall"] == 0.0
    no_identity = TargetSchema(
        "x",
        1,
        {**document, "x-forma": {k: v for k, v in document["x-forma"].items() if k != "identity_keys"}},
        FIXTURES / "schema.json",
    )
    with pytest.raises(ValueError, match="identity_keys"):
        benchmark.score(gold, gold, no_identity)


# --------------------------------------------------------------------------------------------
# the isolated runner pool and the mapping lab executor
# --------------------------------------------------------------------------------------------
def test_runner_pool_dedupes_sockets_and_reports_when_all_are_busy() -> None:
    pool = RunnerPool(["/run/a.sock", "/run/a.sock", "/run/b.sock"])
    assert len(pool.runners) == 2

    class Busy:
        def run(self, *a: Any) -> RunResult:
            raise SandboxBusy("full")

        def healthy(self) -> bool:
            return False

    pool.runners = [Busy(), Busy()]
    with pytest.raises(SandboxBusy, match="All isolated runners are busy"):
        pool.run("pass", [], 1)
    assert pool.healthy() is False
    assert issubclass(SandboxBusy, SandboxUnavailable)


def _lab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: Any, **settings: Any) -> tuple[Any, Any]:
    from forma.storage.local import LocalStorage

    storage = LocalStorage(tmp_path)
    (tmp_path / "input.csv").write_bytes(b"code,value\na,1\n")
    monkeypatch.setattr(mapping_lab, "get_runner", lambda s: runner)
    s = Settings(_env_file=None, **settings)
    return SimpleNamespace(settings=s, storage=storage), SimpleNamespace(
        id="file1", status="profiled", original_name="input.csv", storage_key="input.csv", sha256="abc"
    )


class _Runner:
    def __init__(self, result: RunResult | None = None, error: Exception | None = None) -> None:
        self.result, self.error, self.inputs = result, error, []

    def run(self, code: str, inputs: Any, timeout: float) -> RunResult:
        self.inputs = list(inputs)
        if self.error:
            raise self.error
        assert self.result is not None
        return self.result


def test_mapping_lab_executor_bounds_and_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, schema: TargetSchema
) -> None:
    runner = _Runner(RunResult("ok", 0, 3, "out", "err"))
    ctx, f = _lab(tmp_path, monkeypatch, runner)
    hidden = SimpleNamespace(
        id="file2", status="uploaded", original_name="later.csv", storage_key="later.csv"
    )
    run = mapping_lab.executor(ctx, [f, hidden], schema)
    assert run is not None
    assert run("")["status"] == "rejected" and run("x" * 20001)["status"] == "rejected"
    out = run("print('ok')")
    assert (
        out["status"] == "ok" and out["stdout"] == "out" and out["code_sha256"] and "untrusted" in out["note"]
    )
    assert out["inputs"] == [
        {"file_id": "file1", "name": "input.csv", "path": "inputs/sources/file1.csv", "sha256": "abc"}
    ]
    assert "input.csv" not in json.dumps([i["path"] for i in out["inputs"]]).replace(
        "inputs/sources/file1.csv", ""
    )
    assert {i.path for i in runner.inputs} == {"sources/file1.csv", "manifest.json", "target_schema.json"}
    ctx.settings.code_sandbox_max_input_mb = 0
    assert run("print(1)")["status"] == "rejected" and "budget" in run("print(1)")["error"]


def test_mapping_lab_executor_is_absent_or_unavailable_when_it_should_be(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, schema: TargetSchema
) -> None:
    ctx, f = _lab(tmp_path, monkeypatch, _Runner(), mapping_lab_enabled=False)
    assert mapping_lab.executor(ctx, [f], schema) is None
    ctx, f = _lab(tmp_path, monkeypatch, None)
    assert mapping_lab.executor(ctx, [f], schema) is None
    ctx, f = _lab(tmp_path, monkeypatch, _Runner(error=SandboxBusy("busy")))
    run = mapping_lab.executor(ctx, [f], schema)
    assert run is not None and run("print(1)")["status"] == "unavailable"


def test_mapping_lab_settings_are_bounded() -> None:
    assert (
        Settings(_env_file=None).mapping_lab_turns == 3
        and Settings(_env_file=None).mapping_lab_enabled is True
    )
    assert Settings(_env_file=None, mapping_lab_turns=0).mapping_lab_turns == 0
    with pytest.raises(ValueError):
        Settings(_env_file=None, mapping_lab_turns=7)


# --------------------------------------------------------------------------------------------
# a field mapping on the draft side keeps its identity across the loop's bookkeeping
# --------------------------------------------------------------------------------------------
def test_draft_fields_are_not_mutated_by_grounding(tmp_path: Path, schema: TargetSchema) -> None:
    bench, _ = _bench(tmp_path, schema)
    draft = _doc()
    draft.fields.append(FieldMapping(target="/brand", expr=col("สถานะ"), confidence=0.4))
    before = draft.model_dump(mode="json")
    bench.ground(draft)
    assert draft.model_dump(mode="json") == before


def test_every_generated_env_example_value_loads_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """A list setting is read back as JSON, so the example must write JSON — never ``PydanticUndefined``
    for a field with a default factory, never a bare comma list."""
    from forma.core.settings import SECRET_FIELDS

    for env, value, _ in Settings.describe():
        assert "PydanticUndefined" not in value, env
        name = env.removeprefix("FORMA_").lower()
        if name in SECRET_FIELDS or not value:
            continue
        monkeypatch.setenv(env, value)
        loaded = Settings(_env_file=None)
        if isinstance(getattr(loaded, name), list):
            assert getattr(loaded, name) == json.loads(value), env
        monkeypatch.delenv(env)
    assert Settings.describe()


# --------------------------------------------------------------------------------------------
# what the mock file set exposed: total rows with a count beside them, promotion dates on product rows,
# contract checks after an edit, the exact-record score and the passthrough bag
# --------------------------------------------------------------------------------------------
def test_subtotal_rows_with_a_count_cell_are_not_records(tmp_path: Path) -> None:
    import openpyxl
    import polars as pl

    from forma.ingestion.readers import read_raw
    from forma.profiling.tables import detect_tables, extract_table

    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.append(["รหัส", "ชื่อ", "ราคา", "สถานะ"])
    ws.append(["A1", "น้ำ", 7, "ขาย"])
    ws.append(["A2", "ชา", 20, "ขาย"])
    ws.append(["รวมหมวด", None, 27, "2 รายการ"])
    ws.append(["B1", "ข้าว", 189, "ขาย"])
    ws.append(["Subtotal", None, 189, "1 items"])
    ws.append(["รวมทั้งหมด", None, 216, "3 รายการ"])
    ws.append(["รวมมิตร", "ชื่อยี่ห้อ", 5, "ขาย"])  # a product whose code merely starts with รวม
    wb.save(tmp_path / "t.xlsx")
    raw = read_raw(tmp_path / "t.xlsx", 0, validate_cells=False)
    frame = extract_table(raw, detect_tables(raw)[0].to_spec())
    assert isinstance(frame, pl.DataFrame) and frame["รหัส"].to_list() == ["A1", "A2", "B1", "รวมมิตร"]


def test_promotion_dates_on_the_product_row_are_not_price_dates(schema: TargetSchema) -> None:
    from forma.ai.heuristics import propose_mapping

    profile = _profile(
        {
            "สินค้า": [
                ("รหัสสินค้า", "string"),
                ("ชื่อสินค้า", "string"),
                ("ราคาขาย (รวม VAT)", "float"),
                ("รหัสโปร", "string"),
                ("โปรเริ่ม", "date"),
                ("โปรสิ้นสุด", "date"),
            ]
        }
    )
    doc = propose_mapping(profile, schema).mapping
    price_fields = [f.target for c in doc.collections for f in c.fields if "/prices" in c.target]
    assert not any("effective" in t for t in price_fields), price_fields


def test_exact_record_score_ignores_the_passthrough_bag_the_key_lacks(schema: TargetSchema) -> None:
    gold = [{"sku": "001", "product_name": "A"}, {"sku": "002", "product_name": "B"}]
    actual = [{**r, "attributes": {"Extra": "x"}, "source_meta": {"row": 1}} for r in gold]
    assert benchmark.score(actual, gold, schema)["exact_record_accuracy"] == 1.0
    keyed = [{**r, "attributes": {"Extra": "y"}} for r in gold]  # the key asked for the bag: it counts
    assert benchmark.score(actual, keyed, schema)["exact_record_accuracy"] == 0.0
    assert benchmark.score(keyed, keyed, schema)["exact_record_accuracy"] == 1.0


def test_selector_spelling_does_not_make_a_disagreement() -> None:
    """The model names the file by pattern and spells the header row out; the matcher relied on the only
    file and said "auto". Both read the same cells of the same sheet: that is agreement, not a question."""
    a, b = _doc(), _doc()
    b.sources[0].file.name_pattern = r"^products\.xlsx$"
    b.sources[0].file.sheet_names_any = []
    b.sources[0].table.header_row = 0
    b.sources[0].description = "the product sheet"
    b.sources[0].csv.encoding = "utf-8"
    f = SimpleNamespace(
        id="f1",
        original_name="products.xlsx",
        status="profiled",
        profile={"meta": {"sheets": [{"name": "สินค้า", "index": 0}]}},
        fingerprint=None,
        kind="excel",
    )
    for target in ("/sku", "/prices/*/amount", "/currency"):
        assert reading_signature(a, target, files=[f]) == reading_signature(b, target, files=[f]), target
    c = _doc()
    c.sources[0].table.data_end = 3
    assert reading_signature(a, "/sku", files=[f]) != reading_signature(c, "/sku", files=[f])
    d = _doc()
    d.sources[0].file.name_pattern = r"^other\.xlsx$"  # binds to nothing: another file, not an alias
    assert reading_signature(a, "/sku", files=[f]) != reading_signature(d, "/sku", files=[f])
