"""Independent line and branch floors. Count untested production files, never an empty report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def percent(covered: int, total: int) -> float:
    if total <= 0 or covered < 0 or covered > total:
        raise ValueError("Coverage requires nonempty, valid counters")
    return 100 * covered / total


def backend_metrics(report: dict) -> dict:
    if not report.get("meta", {}).get("branch_coverage"):
        raise ValueError("Backend report must measure branches")
    summary = report["totals"]
    return {
        "lines": percent(summary["covered_lines"], summary["num_statements"]),
        "branches": percent(summary["covered_branches"], summary["num_branches"]),
    }


def frontend_metrics(report: dict) -> dict:
    return {
        metric: percent(report[metric]["covered"], report[metric]["total"])
        for metric in ("lines", "branches")
    }


def evaluate(kind: str, report: dict, policy: dict) -> dict:
    metrics = {
        "all": backend_metrics(report)
        if kind == "backend"
        else frontend_metrics(report["total"])
    }
    if kind == "frontend":
        for suffix in policy.get("files", {}):
            matches = [
                v
                for k, v in report.items()
                if k.replace("\\", "/").endswith("/" + suffix)
            ]
            if len(matches) != 1:
                raise ValueError(f"Missing or ambiguous coverage: {suffix}")
            metrics[suffix] = frontend_metrics(matches[0])
    floors = {"all": policy["all"], **policy.get("files", {})}
    failures = [
        f"{scope} {metric}: {metrics[scope][metric]:.2f}% < {floor}%"
        for scope, limits in floors.items()
        for metric, floor in limits.items()
        if metrics[scope][metric] < floor
    ]
    return {
        "passed": not failures,
        "metrics": metrics,
        "floors": floors,
        "failures": failures,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=["backend", "frontend"])
    parser.add_argument("report", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    policy = json.loads((ROOT / "quality/coverage-policy.json").read_text())[args.kind]
    result = evaluate(
        args.kind, json.loads(args.report.read_text(encoding="utf-8")), policy
    )
    args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
