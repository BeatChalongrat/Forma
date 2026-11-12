"""A release cannot borrow old, incomplete, failed or locally modified test evidence."""

import importlib.util
import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("release_gate", ROOT / "tools/release_gate.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
SHA = "a" * 40


@pytest.fixture
def evidence(tmp_path):
    for suite, names in gate.REQUIRED.items():
        path = tmp_path / suite / "summary.json"
        path.parent.mkdir()
        source = {"sha": SHA, "dirty": False, "changes_sha256": "digest"}
        path.write_text(
            json.dumps(
                {
                    "status": "passed",
                    "model": "mock",
                    "paid_api_calls": 0,
                    "source": source,
                    "source_after": source,
                    "run_id": "123",
                    "run_attempt": "1",
                    "gates": [{"name": n, "exit_code": 0} for n in sorted(names)],
                }
            )
        )
    (tmp_path / "images.json").write_text(
        json.dumps(
            {
                "sha": SHA,
                "images": {k: "sha256:" + "b" * 64 for k in ("api", "web", "sandbox")},
            }
        )
    )
    return tmp_path


def test_complete_same_revision_release_records_immutable_evidence(evidence):
    result = gate.verify(evidence, SHA, "123", "1")
    assert result["status"] == "passed"
    assert set(result["suites"]) == set(gate.REQUIRED)
    assert len(result["evidence_sha256"]) == 6
    assert len(result["evidence_sha256"]["runtime/summary.json"]) == 64


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "failed"),
        ("model", "deepseek"),
        ("paid_api_calls", 1),
        ("run_id", "old-run"),
        ("run_attempt", "0"),
        ("source_after", {}),
        ("source", {"sha": SHA, "dirty": True}),
    ],
)
def test_failed_or_unrelated_evidence_blocks_release(evidence, field, value):
    path = evidence / "runtime/summary.json"
    data = json.loads(path.read_text())
    data[field] = value
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        gate.verify(evidence, SHA, "123", "1")


@pytest.mark.parametrize("mode", ["missing", "duplicate", "skipped", "wrong_sha", "image_tag"])
def test_partial_or_ambiguous_release_is_rejected(evidence, mode):
    path = evidence / "runtime/summary.json"
    data = json.loads(path.read_text())
    if mode == "missing":
        path.unlink()
    elif mode == "duplicate":
        (evidence / "old").mkdir()
        (evidence / "old/summary.json").write_text(json.dumps(data))
    elif mode == "skipped":
        data["gates"][0]["exit_code"] = None
        path.write_text(json.dumps(data))
    else:
        path = evidence / "images.json"
        data = json.loads(path.read_text())
        if mode == "wrong_sha":
            data["sha"] = "c" * 40
        else:
            data["images"]["api"] = "latest"
        path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        gate.verify(evidence, SHA, "123", "1")


def test_release_requires_both_workflows_and_same_run_artifacts():
    release = yaml.load((ROOT / ".github/workflows/release.yml").read_text(), Loader=yaml.BaseLoader)
    ci = yaml.load((ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader)
    runtime = yaml.load((ROOT / ".github/workflows/runtime.yml").read_text(), Loader=yaml.BaseLoader)
    assert "workflow_call" in ci["on"] and "workflow_call" in runtime["on"]
    jobs = release["jobs"]
    assert jobs["ci"]["uses"] == "./.github/workflows/ci.yml"
    assert jobs["runtime"]["uses"] == "./.github/workflows/runtime.yml"
    assert set(jobs["release-gate"]["needs"]) == {"ci", "runtime"}
    steps = jobs["release-gate"]["steps"]
    assert "success" in steps[0]["run"] and "assert" in steps[0]["run"]
    download = next(s for s in steps if s.get("uses", "").startswith("actions/download-artifact"))
    assert "run-id" not in download.get("with", {})  # current workflow only
