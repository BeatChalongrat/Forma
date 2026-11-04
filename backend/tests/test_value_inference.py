"""Prepared regression coverage for value-driven inference. No live provider dependency."""

import json
from types import SimpleNamespace

import polars as pl
from tests.test_agents import _doc, _draft, _profile
from tests.test_self_check import FakeBench, _responder

from forma.ai import agents
from forma.ai.llm import LLMClient, MockProvider
from forma.ai.workbench import RunReport, inspect_problems
from forma.engine.semantic_checks import identity_warnings
from forma.profiling.profile import WorkbookProfile, profile_column
from forma.profiling.value_evidence import column_observations, join_observations, table_observations
from forma.services.decision_memory import verification_allows_recall


def test_profile_exposes_tail_and_leading_zeros_without_declaring_price():
    series = pl.Series("x", ["12.50"] * 500 + ["000019"])
    info = profile_column(series, 0)
    assert "price_like" not in info.hints
    assert info.observations["leading_zero_examples"] == ["000019"]
    assert info.observations["scope"]["complete"] is False
    assert info.observations["scope"]["sampled_rows"] <= 400


def test_numeric_observations_do_not_treat_null_or_nonfinite_as_zero():
    info = column_observations(pl.Series("x", [None, "", "NaN", "Infinity", "0", "-2.5"]))
    assert info["zero_count"] == 1 and info["negative_count"] == 1
    assert info["numeric_parse_ratio"] == 0.5


def test_relationships_use_row_alignment_and_do_not_declare_units():
    frame = pl.DataFrame(
        {"co": ["A", "A", "B", "B"], "id": ["01", "02", "01", "02"], "a": [2, 3, 4, 5], "b": [24, 36, 48, 60]}
    )
    result = table_observations(frame)
    assert ["co", "id"] in result["composite_key_candidates"]
    pair = next(p for p in result["numeric_relationships"] if p["left"] == "a" and p["right"] == "b")
    assert pair["right_over_left"] == 12 and pair["matched_rows"] == 4
    assert "do not prove" in result["caution"]
    shuffled = frame.with_columns(pl.Series("b", [48, 24, 60, 36]))
    assert not any(
        p["left"] == "a" and p["right"] == "b" for p in table_observations(shuffled)["numeric_relationships"]
    )


def test_composite_join_avoids_false_cross_company_matches():
    frame = pl.DataFrame({"company": ["A", "B"], "code": ["001", "001"]})
    wrong = join_observations(frame, frame, ["code"], ["code"])
    right = join_observations(frame, frame, ["company", "code"], ["company", "code"])
    assert wrong["inner_join_rows"] == 4 and wrong["cardinality"] == "N:N"
    assert right["inner_join_rows"] == 2 and right["cardinality"] == "1:1"
    other = pl.DataFrame({"code": ["1"]})
    assert join_observations(frame, other, ["code"], ["code"])["matched_left_rows"] == 0


def test_new_inspection_requests_validate_table_and_composite_key_bounds():
    assert not inspect_problems(
        [
            {
                "what": "join",
                "table_index": 0,
                "other_table_index": 1,
                "left_keys": ["company", "code"],
                "right_keys": ["co", "id"],
            }
        ],
        2,
    )
    assert inspect_problems(
        [{"what": "join", "table_index": 0, "other_table_index": True, "left_keys": ["a"], "right_keys": []}],
        2,
    )
    assert inspect_problems([{"what": "column_profile", "table_index": 0}], 1)


def test_old_profiles_load_without_new_evidence_fields():
    original = _profile({"Export": [("Code", "string")]})
    data = original.to_dict()
    for table in data["tables"]:
        table.pop("observations", None)
        table.pop("total_rows", None)
        for column in table["columns"]:
            column.pop("observations", None)
    restored = WorkbookProfile.from_dict(data)
    assert restored.tables[0].total_rows is None
    assert restored.tables[0].columns[0].observations == {}


def test_candidate_comparison_does_not_choose_or_bypass_final_execution(schema):
    doc = _doc().model_dump(mode="json", exclude_none=True)
    candidates = [{"label": "a", "mapping": doc}, {"label": "b", "mapping": doc}]
    prompts = []
    replies = [
        {"mapping": None, "candidates": candidates},
        {"mapping": None, "candidates": candidates},  # budget exhausted; provider must repair
        {"mapping": doc, "confidence": 0.8},
    ]
    bench = FakeBench([RunReport(6, 6, complete=False), RunReport(6, 6, complete=False), RunReport(6, 6)])
    client = LLMClient(MockProvider(_responder(replies, prompts)), model="m")
    _, result = agents.mapping_agent(
        client, _profile({"สินค้า": [("รหัสสินค้า", "string")]}), schema, _draft(), workbench=bench
    )
    assert len(bench.ran) == 3
    assert result.data["self_check"]["candidate_turns"] == 1
    second = json.loads(prompts[1])
    assert second["earlier_turns"][0]["candidate_comparison"]["selected"] is None
    assert second["limits"]["candidate_turns_left"] == 0
    assert "No candidate comparisons left" in prompts[2]


def test_identity_zero_loss_is_reported_from_source_values(schema):
    doc = _doc()
    fm = next(f for f in doc.fields if f.target == "/sku")
    from forma.mapping_dsl.models import col

    fm.expr = col("code")
    source = doc.record.root_source + ".code"
    frame = pl.DataFrame({source: ["00012", "00013"], "/sku": ["12", "00013"]})
    warnings = identity_warnings(frame, doc, schema)
    assert warnings[0]["details"]["affected_rows"] == 1
    assert warnings[0]["details"]["examples"] == [{"source": "00012", "output": "12"}]
    assert not identity_warnings(frame.with_columns(pl.col(source).alias("/sku")), doc, schema)


def test_failed_or_incomplete_verified_memory_is_not_recalled():
    for report in ({"passed": False, "complete": True}, {"passed": True, "complete": False}):
        assert not verification_allows_recall(SimpleNamespace(policy={"verification": report}))
    assert verification_allows_recall(
        SimpleNamespace(policy={"verification": {"passed": True, "complete": True}})
    )
