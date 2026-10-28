"""`forma` CLI commands and the demo seeder, on an isolated database."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from forma.synth.generator import generate_corpus

runner = CliRunner()


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("cli-corpus")
    generate_corpus(out, seed=3, n_variants=8)
    return out


def _first_file(corpus: Path) -> Path:
    index = json.loads((corpus / "index.json").read_text(encoding="utf-8"))
    m = next(v for v in index["variants"] if v["archetype"] == "express_th")
    return corpus / m["variant_id"] / "files" / m["files"][0]


def test_cli_profile_propose_transform(corpus: Path, tmp_path: Path, isolated_env: Path) -> None:
    from forma.cli import app

    xlsx = _first_file(corpus)
    res = runner.invoke(app, ["profile", str(xlsx)])
    assert res.exit_code == 0, res.output
    assert "fingerprint strict=" in res.output and "header_row=" in res.output
    out = tmp_path / "profile.json"
    assert runner.invoke(app, ["profile", str(xlsx), "--out", str(out)]).exit_code == 0
    assert json.loads(out.read_text(encoding="utf-8"))["fingerprint"]["strict"]

    mapping = tmp_path / "mapping.yaml"
    res = runner.invoke(app, ["propose", str(xlsx), "--out", str(mapping)])
    assert res.exit_code == 0, res.output
    assert "valid=True" in res.output and mapping.exists()
    res = runner.invoke(app, ["propose", str(xlsx), "--llm"])
    assert res.exit_code == 0 and '"ok": true' in res.output

    out_jsonl = tmp_path / "out.jsonl"
    res = runner.invoke(app, ["transform", str(xlsx), str(mapping), "--out", str(out_jsonl), "--limit", "20"])
    assert res.exit_code == 0, res.output
    assert "rows_out" in res.output and out_jsonl.exists()
    records = [json.loads(line) for line in out_jsonl.read_text(encoding="utf-8").splitlines()]
    assert records and records[0]["sku"]

    res = runner.invoke(app, ["schema", "check-output", "product", str(out_jsonl)])
    assert res.exit_code == 0 and "0 errors" in res.output
    assert "product@1" in runner.invoke(app, ["schema", "list"]).output
    assert "/prices/*/amount" in runner.invoke(app, ["schema", "fields"]).output
    schema_file = Path(__file__).resolve().parents[2] / "target_schema" / "product.v1.json"
    assert "schema OK" in runner.invoke(app, ["schema", "validate", str(schema_file)]).output


def test_cli_synth_and_eval(tmp_path: Path) -> None:
    from forma.cli import app

    out = tmp_path / "corpus"
    res = runner.invoke(app, ["synth", "--out", str(out), "--seed", "5", "--variants", "8"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.output)["variants"] == 12
    report = tmp_path / "report.json"
    res = runner.invoke(app, ["eval", "--corpus", str(out), "--report", str(report)])
    assert res.exit_code == 0, res.output
    assert json.loads(report.read_text(encoding="utf-8"))["summary"]["failed_variants"] == []


def test_seed_database(corpus: Path, isolated_env: Path) -> None:
    from forma.cli import app
    from forma.synth.seed import seed_database

    stats = seed_database(corpus, run_pipeline=True, limit=4)  # one full returning-customer sequence
    assert stats["customers"] == 1 and stats["rounds"] == 4
    results = stats["results"]
    assert results[0]["decision"] == "approved_by_operator" and results[0]["rows"] > 0
    assert (
        results[1]["decision"] == "auto_approve" and "dataset" in results[1]
    )  # identical round → memory reuse
    assert results[2]["decision"] == "review"  # renamed columns → drift review
    assert results[3]["decision"] in ("review", "block")  # wrong file

    res = runner.invoke(app, ["seed", "--corpus", str(corpus), "--no-run"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.output)["rounds"] == 12
