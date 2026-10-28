"""`forma config show|example` and the eval baseline gate (ADR-016)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

runner = CliRunner()
REPO = Path(__file__).resolve().parents[2]


def test_config_show_masks_secrets(isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORMA_LLM_API_KEY", "sk-super-secret-value")
    monkeypatch.setenv("FORMA_DATABASE_URL", "postgresql+psycopg://forma:hunter2@db:5432/forma")
    from tests.conftest import clear_forma_caches

    clear_forma_caches()
    from forma.cli import app

    res = runner.invoke(app, ["config", "show"])
    assert res.exit_code == 0, res.output
    cfg = json.loads(res.stdout)
    assert cfg["llm_api_key"] == "sk-***" and "hunter2" not in cfg["database_url"]
    assert cfg["database_url"].startswith("postgresql+psycopg://forma:***@db")
    res = runner.invoke(app, ["config", "show", "--secrets"])
    assert "hunter2" in json.loads(res.stdout)["database_url"]


def test_env_example_is_generated_and_up_to_date(tmp_path: Path) -> None:
    from forma.cli import app
    from forma.core.config_example import render_env_example
    from forma.core.settings import SECRET_FIELDS, Settings

    text = render_env_example()
    for name in Settings.model_fields:
        assert f"FORMA_{name.upper()}=" in text
    for secret in SECRET_FIELDS:
        assert f"FORMA_{secret.upper()}=\n" in text  # secrets are blank
    assert "[SECRET]" in text and "# dev | prod" in text
    committed = REPO / ".env.production.example"
    assert committed.exists(), "run: forma config example --out ../.env.production.example"
    res = runner.invoke(app, ["config", "example", "--check", str(committed)])
    assert res.exit_code == 0, res.output
    stale = tmp_path / "stale.env"
    stale.write_text("FORMA_ENV=prod\n", encoding="utf-8")
    assert runner.invoke(app, ["config", "example", "--check", str(stale)]).exit_code == 1
    out = tmp_path / "gen.env"
    assert runner.invoke(app, ["config", "example", "--out", str(out)]).exit_code == 0
    assert out.read_text(encoding="utf-8") == text


def test_eval_baseline_gate() -> None:
    from forma.eval.evaluate import compare_to_baseline

    base = {
        "mean_identity_recall": 1.0,
        "mean_field_accuracy": 0.9962,
        "mean_critical_accuracy": 1.0,
        "variants_at_99": 30,
        "drift_correct": 9,
        "failed_variants": [],
    }
    assert compare_to_baseline(dict(base), base) == []
    assert compare_to_baseline({**base, "mean_field_accuracy": 0.9965}, base) == []
    drops = compare_to_baseline({**base, "mean_field_accuracy": 0.99, "variants_at_99": 29}, base)
    assert len(drops) == 2 and drops[0].startswith("mean_field_accuracy")
    assert compare_to_baseline({**base, "failed_variants": ["v1"]}, base)[0].startswith("failed_variants")
    committed = json.loads((REPO / "data" / "eval_baseline.json").read_text(encoding="utf-8"))
    assert committed["summary"]["mean_critical_accuracy"] == 1.0
