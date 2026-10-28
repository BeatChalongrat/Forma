"""Heuristic mapper + engine against synthetic ground truth (synth → profile → propose → transform → eval)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from forma.ai.heuristics import (
    crosstab_value_columns,
    file_name_pattern,
    norm,
    propose_mapping,
    score_header,
    table_role,
)
from forma.eval.evaluate import evaluate_corpus, evaluate_variant
from forma.mapping_dsl.validate import validate_mapping
from forma.profiling.profile import WorkbookProfile, merge_profiles, profile_workbook
from forma.schema_registry import TargetSchema
from forma.synth.generator import generate_corpus, plan_corpus


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("corpus")
    stats = generate_corpus(out, seed=1, n_variants=8)
    assert stats["variants"] == 12 and stats["sequences"] == 3
    assert (out / "index.json").exists()
    return out


def test_plan_corpus_shapes() -> None:
    variants = plan_corpus(seed=1, n_variants=20)
    assert len(variants) == 20
    seqs = [v for v in variants if v.sequence]
    assert len(seqs) == 12
    assert {v.expected_relation for v in seqs} == {None, "same_customer_unchanged", "renamed", "wrong_file"}
    assert all("renamed_columns" not in v.perturbations for v in variants if v.sequence is None)
    # deterministic
    assert [v.variant_id for v in plan_corpus(1, 20)] == [v.variant_id for v in variants]


def test_evaluate_corpus(corpus: Path) -> None:
    rep = evaluate_corpus(corpus)
    s = rep["summary"]
    assert s["variants"] == 12
    assert s["failed_variants"] == []
    assert s["mean_identity_recall"] == 1.0
    assert s["mean_critical_accuracy"] >= 0.99
    assert s["mean_field_accuracy"] >= 0.95
    assert s["drift_checks"] == 9 and s["drift_correct"] == 9
    assert set(s["by_archetype"]) >= {"express_th", "odoo_like", "csv_vendor"}
    for v in rep["variants"]:
        assert v["mapping_valid"] is True
        assert v["errors"] == []
        assert v["identity_precision"] == 1.0
        assert v["coverage"] >= 0.9
    wrong = [d for d in rep["drift"] if d["expected"] == "wrong_file"]
    assert wrong and all(d["got"] == "unrelated_file" for d in wrong)


def test_single_variant_details(corpus: Path, schema: TargetSchema) -> None:
    index = json.loads((corpus / "index.json").read_text(encoding="utf-8"))
    m = next(v for v in index["variants"] if v["archetype"] == "express_th" and v["round_no"] == 0)
    vdir = corpus / m["variant_id"]
    res = evaluate_variant(vdir, schema)
    assert res["identity_recall"] == 1.0
    acc = res["field_accuracy_by_pointer"]
    assert acc["/sku"] == 1.0 and acc["/prices/0/amount"] == 1.0 and acc["/prices/0/vat_included"] == 1.0
    assert acc["/inventory"] >= 0.99

    files = [vdir / "files" / f for f in m["files"]]
    profile = merge_profiles([(f"f{i}", f.name, profile_workbook(f)) for i, f in enumerate(files)])
    prop = propose_mapping(profile, schema, customer_id=m["customer_code"])
    assert prop.confidence > 0.9
    assert validate_mapping(prop.mapping, schema).ok
    assert prop.mapping.record.root_source == "products"
    assert {c.target for c in prop.mapping.collections} >= {"/prices", "/inventory", "/promotions"}
    assert set(prop.table_roles.values()) >= {"products", "inventory", "promotions"}
    assert prop.to_dict()["confidence"] == prop.confidence
    # round trip of the profile used by the API layer
    again = WorkbookProfile.from_dict(profile.to_dict())
    assert [t.sheet for t in again.tables] == [t.sheet for t in profile.tables]
    assert [c.name for c in again.tables[0].columns] == [c.name for c in profile.tables[0].columns]


def test_crosstab_variant(corpus: Path, schema: TargetSchema) -> None:
    index = json.loads((corpus / "index.json").read_text(encoding="utf-8"))
    m = next(v for v in index["variants"] if v["archetype"] == "crosstab_months")
    vdir = corpus / m["variant_id"]
    files = [vdir / "files" / f for f in m["files"]]
    profile = merge_profiles([(f"f{i}", f.name, profile_workbook(f)) for i, f in enumerate(files)])
    grid = next(t for t in profile.tables if t.sheet == "Price by Month")
    assert crosstab_value_columns(grid) == ["Oct-26", "Nov-26", "Dec-26"]
    assert table_role(grid, schema)[0] == "pricing"
    prop = propose_mapping(profile, schema)
    prices = next(c for c in prop.mapping.collections if c.target == "/prices")
    src = prop.mapping.source(prices.source)
    assert src.table.unpivot is not None and src.table.unpivot.value_columns == ["Oct-26", "Nov-26", "Dec-26"]
    assert any("price grid" in q for q in prop.open_questions)


def test_heuristic_helpers(schema: TargetSchema) -> None:
    sku = schema.field("/sku")
    assert score_header("รหัสสินค้า", sku) == (1.0, "header 'รหัสสินค้า' == synonym 'รหัสสินค้า'")
    assert score_header("Item Code", sku)[0] >= 0.75
    assert score_header("width", schema.field("/sku"))[0] < 0.55  # 'id' must not match inside 'width'
    assert score_header("", sku) == (0.0, None)
    assert norm(" Sales_Price (THB) ") == "sales price thb"
    assert file_name_pattern("export_C001_20261001.xlsx") == "(?i)^export_C"
    assert file_name_pattern("1234.csv") == "(?i).*"


def test_propose_requires_tables(schema: TargetSchema) -> None:
    from forma.ingestion.readers import WorkbookMeta

    empty = WorkbookProfile(WorkbookMeta(Path("x.xlsx"), "excel", []), [])
    with pytest.raises(ValueError, match="no tables"):
        propose_mapping(empty, schema)
