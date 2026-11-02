"""Regression cases for the evidence/execution harness. No paid provider is needed."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import openpyxl
import pytest
from tests.test_agents import _doc, _draft, _profile
from tests.test_self_check import FakeBench, _bench, _responder

from forma.ai import agents
from forma.ai.clarifications import validate
from forma.ai.llm import LLMClient, MockProvider
from forma.ai.policy import PolicyDecision
from forma.ai.reconciliation import coverage, regressions, require_verification, verification
from forma.ai.workbench import RunReport
from forma.code_sandbox.client import RunnerPool, RunResult, SandboxBusy, SandboxUnavailable
from forma.eval.benchmark import score
from forma.mapping_dsl.models import call, col, const
from forma.schema_registry import TargetSchema
from forma.services.mapping import _agreement, _match_file


def test_equal_columns_do_not_mean_equal_formula_or_range():
    original = _doc()
    changed = original.model_copy(deep=True)
    f = changed.collections[0].fields[1]
    f.expr = call("multiply", f.expr, const(1000))
    assert f.target in _agreement(original, changed)["disagree"]
    changed = original.model_copy(deep=True)
    changed.sources[0].table.data_end = 3
    assert original.fields[0].target in _agreement(original, changed)["disagree"]


def test_binding_is_conjunctive_and_never_breaks_ties_arbitrarily():
    source = _doc().sources[0].model_copy(deep=True)
    source.sheet = None
    source.file.sheet_names_any = []
    source.file.name_pattern = r"^wanted\.xlsx$"
    a = SimpleNamespace(id="a", original_name="wrong.xlsx", profile=None, fingerprint=None, kind="excel")
    b = SimpleNamespace(id="b", original_name="WANTED.xlsx", profile=None, fingerprint=None, kind="excel")
    assert _match_file(source, [a, b]) is b
    assert _match_file(source, [a]) is None
    source.file.name_pattern = None
    assert _match_file(source, [a, b]) is None


def test_original_cells_and_alternative_table_do_not_mutate_input(tmp_path: Path, schema: TargetSchema):
    bench, file = _bench(tmp_path, schema)
    original = Path(file.storage_key).read_bytes()
    result = bench.inspect(
        [
            {"table_index": 0, "what": "raw", "limit": 2},
            {"table_index": 0, "what": "sample"},
            {"table_index": 0, "what": "trial_table", "table": {"header_row": 0}},
        ]
    )
    assert result[0]["answer"]["cells"][0]["cell"] == "A1"
    assert result[1]["answer"]["rows"][-1]["table_row"] == 5
    assert result[2]["answer"]["rows"] == 6
    assert Path(file.storage_key).read_bytes() == original


def test_full_verification_catches_damage_after_sample(tmp_path: Path, schema: TargetSchema):
    bench, file = _bench(tmp_path, schema)
    path = Path(file.storage_key)
    wb = openpyxl.load_workbook(path)
    for i in range(60):
        wb.active.append([f"B{i}", "tail item", "100", "ขาย"])
    wb.active.append(["TAIL", "damaged", "not-a-price", "ขาย"])
    wb.save(path)
    wb.close()
    doc = _doc()
    doc.sources[0].sheet = None
    doc.sources[0].file.sheet_names_any = []
    doc.collections[0].fields[1].expr = call("parse_number_th", col("ราคาขาย"))
    assert bench.run(doc).clean
    full = bench.verify(doc)
    assert full.complete and full.rows_in > 50 and not full.clean


def test_agent_uses_scoped_sandbox_then_validates_mapping(schema: TargetSchema):
    bench = FakeBench([RunReport(6, 6)])
    runs = []
    bench.execute = lambda code: runs.append(code) or {"status": "ok", "stdout": "cell A1 is item code"}
    prompts = []
    replies = [
        {"mapping": None, "python": "print('bounded evidence')"},
        {"mapping": _doc().model_dump(mode="json"), "open_questions": ["Is the price tax inclusive?"]},
    ]
    llm = LLMClient(MockProvider(_responder(replies, prompts)), model="same-brain")
    profile = _profile({"สินค้า": [("รหัสสินค้า", "string"), ("ชื่อสินค้า", "string"), ("ราคาขาย", "float")]})
    _, result = agents.mapping_agent(llm, profile, schema, _draft(), workbench=bench)
    assert runs == ["print('bounded evidence')"]
    assert result.data["self_check"]["python_turns"] == 1
    assert json.loads(prompts[1])["earlier_turns"][0]["sandbox_result"]["status"] == "ok"


def test_full_check_failures_always_disable_automatic_approval(schema: TargetSchema):
    decision = PolicyDecision("auto_approve", 0)
    require_verification(decision, verification(None, schema, complete=False))
    assert decision.decision == "review" and decision.checks["full_verification"] is False


def test_dropping_records_or_collection_items_is_a_regression(schema: TargetSchema):
    before = coverage([{"sku": "001", "prices": [{"amount": 100}]}], schema, {})
    after = coverage([], schema, {})
    problems = regressions(before, after)
    assert any("/sku" in p for p in problems) and any("/prices" in p for p in problems)


def test_typed_question_cannot_write_unknown_field_or_wrong_type(schema: TargetSchema):
    q = {
        "target": "/product_name",
        "question": "Which name is authoritative?",
        "why": "Two sources disagree",
        "kind": "enum",
        "options": ["Retail", "Internal"],
    }
    assert validate([q], _doc(), schema) == []
    assert validate([{**q, "target": "/not_in_contract"}], _doc(), schema)
    assert validate([{**q, "kind": "number"}], _doc(), schema)
    assert validate([q, q], _doc(), schema)


def test_runner_pool_only_retries_explicit_busy():
    class Runner:
        def __init__(self, error=None):
            self.error, self.calls = error, 0

        def run(self, *args):
            self.calls += 1
            if self.error:
                raise self.error
            return RunResult("ok", 0, 1, "", "")

    pool = RunnerPool([])
    first, second = Runner(SandboxBusy()), Runner()
    pool.runners = [first, second]
    assert pool.run("pass", [], 1).status == "ok" and second.calls == 1
    first.error = SandboxUnavailable("unknown whether execution started")
    with pytest.raises(SandboxUnavailable):
        pool.run("pass", [], 1)
    assert second.calls == 1


def test_generic_benchmark_penalizes_missing_duplicate_and_wrong_identities(schema: TargetSchema):
    expected = [{"sku": "001", "product_name": "A"}, {"sku": "002", "product_name": "B"}]
    actual = copy.deepcopy(expected)
    actual[1]["sku"] = "001"
    metrics = score(actual, expected, schema)
    assert metrics["duplicate_identities"] == 1 and metrics["identity_recall"] == 0.5
    assert metrics["field_accuracy"] < 1


def test_business_invariants_cannot_be_silenced_by_deleting_dsl_quality_rules():
    from forma.ai.contract_checks import evaluate, validate_checks

    contract = {
        "x-forma": {
            "checks": [
                {
                    "id": "total",
                    "rule": "sum_equals",
                    "left": "/total",
                    "right": "/lines/*/amount",
                    "tolerance": 0.01,
                }
            ]
        }
    }
    assert not validate_checks(contract)
    assert not evaluate([{"total": 30.0, "lines": [{"amount": 10}, {"amount": 20}]}], contract)
    assert evaluate([{"total": 3000, "lines": [{"amount": 10}, {"amount": 20}]}], contract)
    assert evaluate([{"total": 0, "lines": []}], contract)
    broken = {"x-forma": {"checks": [{"id": "x", "rule": "execute_python"}]}}
    assert validate_checks(broken) and evaluate([{}], broken)


def test_benchmark_detects_prices_swapped_between_items(schema: TargetSchema):
    expected = [{"sku": "001", "prices": [{"amount": 10, "channel": "A"}, {"amount": 20, "channel": "B"}]}]
    actual = copy.deepcopy(expected)
    actual[0]["prices"][0]["amount"] = 20
    actual[0]["prices"][1]["amount"] = 10
    result = score(actual, expected, schema)
    assert result["per_field"]["/prices/*/amount"] == 1
    assert result["per_collection"]["/prices"] == 0
    assert result["exact_record_accuracy"] == 0
    assert (
        score([{"sku": "001", "weight": 10.0}], [{"sku": "001", "weight": 10}], schema)[
            "exact_record_accuracy"
        ]
        == 1
    )


def test_drift_rebinding_is_comparison_only_and_rejects_ambiguous_files(monkeypatch):
    from forma.services import format_memory

    doc = _doc()
    doc.sources[0].file.name_pattern = "^old.xlsx$"
    fp = {"strict": "old", "loose": "old", "tables": [], "kind": "excel"}
    mapping = SimpleNamespace(
        document=doc.model_dump(),
        status="approved",
        approved_at=None,
        policy={"file_fingerprints": {"f": fp}},
    )
    file = SimpleNamespace(
        id="f1", original_name="new.xlsx", status="profiled", fingerprint=fp, profile=None, kind="excel"
    )
    monkeypatch.setattr(
        format_memory,
        "compare",
        lambda *args: SimpleNamespace(classification="columns_renamed", similarity=0.95),
    )
    assert format_memory.rebind_approved(mapping, [file]).sources[0].file.name_pattern == "^old.xlsx$"
    comparison = format_memory.rebind_approved(mapping, [file], for_comparison=True)
    assert _match_file(comparison.sources[0], [file]) is file
    other = SimpleNamespace(**{**vars(file), "id": "f2", "original_name": "other.xlsx"})
    ambiguous = format_memory.rebind_approved(mapping, [file, other], for_comparison=True)
    assert ambiguous.sources[0].file.name_pattern == "^old.xlsx$"


def test_manifest_run_is_schema_driven_and_reordering_is_invariant():
    from forma.eval.benchmark import run

    report = run(Path(__file__).parent / "fixtures" / "harness" / "manifest.json")
    assert report["summary"]["cold"]["cases"] == 2
    assert report["summary"]["cold"]["mean_field_accuracy"] == 1
    assert all(
        case["verification"]["complete"] and case["verification"]["passed"] for case in report["cases"]
    )


def test_cold_split_rejects_family_leakage(tmp_path: Path):
    from forma.eval.benchmark import run

    manifest = {
        "training_families": ["same-erp"],
        "cases": [{"id": "leaked", "split": "cold", "family": "same-erp"}],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="leaks"):
        run(path)


def test_structured_answer_changes_one_field_and_keeps_other_questions(schema: TargetSchema):
    from forma.db.models import MappingVersion
    from forma.services.review_questions import answer_document, questions

    clarification = {
        "target": "/product_name",
        "question": "Which name for this file?",
        "why": "Missing label",
        "kind": "enum",
        "options": ["Retail", "Wholesale"],
    }
    document = _doc().model_dump(mode="json")
    # A file-wide answer is valid for a missing constant, not a row-varying name.
    next(f for f in document["fields"] if f["target"] == "/product_name")["expr"] = {
        "op": "const",
        "value": None,
    }
    mapping = MappingVersion(
        id="test",
        status="proposed",
        target_schema=schema.ref,
        document=document,
        policy={"clarifications": [clarification]},
        open_questions=[clarification["question"], "Confirm the currency with the owner"],
    )
    ctx = SimpleNamespace(registry=SimpleNamespace(get_ref=lambda ref: schema))
    q = next(q for q in questions(ctx, mapping)["questions"] if q["id"] == "/product_name")
    assert q["kind"] == "enum" and q["scope"] == "constant_for_this_file"
    document, remaining, _ = answer_document(ctx, mapping, {"/product_name": "Retail"}, "reviewer")
    field = next(f for f in document["fields"] if f["target"] == "/product_name")
    assert field["expr"] == {"op": "const", "value": "Retail"}
    assert remaining == ["Confirm the currency with the owner"]
    assert mapping.document["fields"] != document["fields"]  # answered copy differs
    assert next(f for f in mapping.document["fields"] if f["target"] == "/product_name")["expr"] == {
        "op": "const",
        "value": None,
    }  # original version is immutable


def test_mapping_sandbox_only_receives_the_round_files(tmp_path: Path, schema: TargetSchema, monkeypatch):
    from forma.core.settings import Settings
    from forma.services import mapping_lab
    from forma.storage.local import LocalStorage

    storage = LocalStorage(tmp_path)
    (tmp_path / "input.csv").write_bytes(b"code,value\na,1\n")
    (tmp_path / "secret.txt").write_bytes(b"not part of the round")
    seen = []

    class Runner:
        def run(self, code, inputs, timeout):
            for item in inputs:
                with item.open() as f:
                    seen.append((item.path, f.read()))
            return RunResult("ok", 0, 1, "read one file", "")

    monkeypatch.setattr(mapping_lab, "get_runner", lambda settings: Runner())
    ctx = SimpleNamespace(settings=Settings(_env_file=None), storage=storage)
    f = SimpleNamespace(id="file1", status="profiled", original_name="input.csv", storage_key="input.csv")
    run = mapping_lab.executor(ctx, [f], schema)
    assert run("print('evidence')")["status"] == "ok"
    assert {name for name, _ in seen} == {"sources/file1.csv", "manifest.json", "target_schema.json"}
    assert all(b"not part of the round" not in data for _, data in seen)
