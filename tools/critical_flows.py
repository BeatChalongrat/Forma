"""Check critical journeys against actual JUnit/browser evidence; missing or skipped is failure."""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "quality/critical-flows.json"


def read_catalog(path: Path = CATALOG) -> list[dict]:
    flows = json.loads(path.read_text(encoding="utf-8"))["flows"]
    ids = [flow["id"] for flow in flows]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("Critical flow IDs must be nonempty and unique")
    for flow in flows:
        if not flow.get("checks"):
            raise ValueError(f"No checks for {flow['id']}")
    return flows


def junit_passes(path: Path, classname: str, name: str) -> bool:
    cases = [
        case
        for case in ET.parse(path).iter("testcase")
        if case.get("classname") == classname
        and (case.get("name") == name or case.get("name", "").startswith(name + "["))
    ]
    return bool(cases) and all(
        not any(case.find(tag) is not None for tag in ("failure", "error", "skipped"))
        for case in cases
    )


def check(evidence: Path, spec: dict) -> bool:
    matches = list(evidence.rglob(spec["report"]))
    if len(matches) != 1:
        return False  # duplicate/stale reports must not mask a failed run
    if spec["kind"] == "junit":
        return junit_passes(matches[0], spec["class"], spec["name"])
    if spec["kind"] == "browser":
        data = json.loads(matches[0].read_text(encoding="utf-8"))
        return not data.get("consoleErrors") and spec["name"] in data.get("passed", [])
    raise ValueError(f"Unknown check kind: {spec['kind']}")


def evaluate(evidence: Path, catalog: Path = CATALOG) -> dict:
    results = []
    for flow in read_catalog(catalog):
        checks = []
        for spec in flow["checks"]:
            try:
                passed = check(evidence, spec)
            except (OSError, ValueError, ET.ParseError):
                passed = False
            checks.append({**spec, "passed": passed})
        results.append(
            {**flow, "checks": checks, "passed": all(c["passed"] for c in checks)}
        )
    return {
        "scope": "PR critical journeys; not all possible product behavior or real-model accuracy",
        "passed": all(flow["passed"] for flow in results),
        "passed_count": sum(flow["passed"] for flow in results),
        "total": len(results),
        "flows": results,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate(args.evidence)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Critical journeys: {report['passed_count']}/{report['total']}; {args.out}")
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
