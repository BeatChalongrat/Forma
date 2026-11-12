"""A green gate must be backed by complete evidence, not missing or skipped tests."""

import ast
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"tools/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


critical = load("critical_flows")
coverage = load("coverage_gate")


@pytest.mark.parametrize("child", ["<failure/>", "<error/>", "<skipped/>"])
def test_any_failed_or_skipped_parameter_blocks_critical_flow(tmp_path, child):
    path = tmp_path / "backend.xml"
    path.write_text(
        '<testsuite><testcase classname="tests.demo" name="test_race[one]"/>'
        f'<testcase classname="tests.demo" name="test_race[two]">{child}</testcase></testsuite>'
    )
    assert not critical.junit_passes(path, "tests.demo", "test_race")
    assert not critical.junit_passes(path, "tests.other", "test_race")


def test_empty_missing_and_ambiguous_evidence_cannot_pass(tmp_path):
    spec = {"kind": "junit", "report": "backend.xml", "class": "tests.demo", "name": "test_race"}
    assert not critical.check(tmp_path, spec)
    (tmp_path / "backend.xml").write_text("<testsuite/>")
    assert not critical.check(tmp_path, spec)
    good = '<testsuite><testcase classname="tests.demo" name="test_race"/></testsuite>'
    (tmp_path / "backend.xml").write_text(good)
    assert critical.check(tmp_path, spec)
    (tmp_path / "duplicate").mkdir()
    (tmp_path / "duplicate/backend.xml").write_text(good)
    assert not critical.check(tmp_path, spec)


def test_line_coverage_cannot_hide_missing_branch_coverage():
    report = {
        "meta": {"branch_coverage": True},
        "totals": {
            "covered_lines": 99,
            "num_statements": 100,
            "covered_branches": 40,
            "num_branches": 100,
        },
    }
    result = coverage.evaluate("backend", report, {"all": {"lines": 85, "branches": 80}})
    assert not result["passed"] and len(result["failures"]) == 1
    report["meta"]["branch_coverage"] = False
    with pytest.raises(ValueError, match="branches"):
        coverage.backend_metrics(report)


def test_zero_denominator_and_missing_critical_frontend_file_fail():
    with pytest.raises(ValueError, match="nonempty"):
        coverage.percent(0, 0)
    total = {k: {"covered": 100, "total": 100} for k in ("lines", "branches")}
    with pytest.raises(ValueError, match="Missing"):
        coverage.evaluate(
            "frontend",
            {"total": total},
            {
                "all": {"lines": 8, "branches": 5},
                "files": {"src/auth.ts": {"lines": 90}},
            },
        )


def test_catalog_points_to_real_backend_tests_and_has_no_empty_flows():
    for flow in critical.read_catalog():
        for check in flow["checks"]:
            if check["kind"] != "junit" or check["report"] != "backend.xml":
                continue
            path = ROOT / "backend" / (check["class"].replace(".", "/") + ".py")
            names = {
                node.name
                for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
                if isinstance(node, ast.FunctionDef)
            }
            assert check["name"] in names, check
