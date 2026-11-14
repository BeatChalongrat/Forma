"""Repeatable offline pilot rehearsal on an existing owned pilot, never dev.

Serial steps avoid browser work racing with a restart. No automatic cleanup.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import repro


def run(name: str) -> Path:
    path, marker, config = repro.load(name)
    repro.local_daemon()
    repro.owned_resources(marker, config)
    if not marker.get("pilot"):
        raise RuntimeError("Requires an owned mock-only pilot; dev is not accepted")
    with httpx.Client(
        base_url=f"http://127.0.0.1:{marker['ports'][0]}", trust_env=False
    ) as client:
        assert client.get("/ready").raise_for_status().json()["status"] == "ok"
        ai = client.get("/api/settings/ai").raise_for_status().json()
        assert ai["mock_only"] and ai["active_provider"] == "mock"
        assert client.get("/api/me").raise_for_status().json()["pilot_mode"]
    timestamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S%fZ")
    out = repro.ROOT / "test-results" / "pilot-qualification" / timestamp
    out.mkdir(parents=True, exist_ok=False)
    env = os.environ.copy()
    env["FORMA_PILOT_NAME"] = name
    commands = [
        (
            "paired",
            [
                sys.executable,
                "-m",
                "forma.eval.pilot",
                "backend/tests/fixtures/pilot_pair/manifest.json",
                "--out",
                str(out / "paired"),
            ],
        ),
        ("review", [sys.executable, "deploy/repro/review_smoke.py", "--name", name]),
        (
            "source-evidence",
            [sys.executable, "deploy/repro/source_evidence_smoke.py", "--name", name],
        ),
        ("data-recovery", [sys.executable, "deploy/repro/smoke.py", "--name", name]),
        (
            "browser-fixture",
            [
                sys.executable,
                "deploy/repro/review_smoke.py",
                "--name",
                name,
                "--prepare-only",
            ],
        ),
        ("browser", ["node", "frontend/tests/pilot.browser.cjs"]),
        (
            "question-ledger",
            [sys.executable, "deploy/repro/pilot_report.py", "--name", name],
        ),
    ]
    for label, command in commands:
        print(f"Running {label} on {name}", flush=True)
        with (out / f"{label}.log").open("w", encoding="utf-8") as log:
            subprocess.run(
                command,
                cwd=repro.ROOT,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
            )
    artifacts = {
        "review": path / "review-journey-result.json",
        "data_recovery": path / "smoke-result.json",
        "browser": repro.ROOT / "test-results/pilot-browser/result.json",
    }
    results = {
        key: json.loads(file.read_text(encoding="utf-8"))
        for key, file in artifacts.items()
    }
    # Keep only this rehearsal's newly-created rounds in the collected ledger.
    round_ids = {value["round_id"] for value in results.values()}
    results["source_evidence"] = json.loads(
        (path / "source-evidence-result.json").read_text(encoding="utf-8")
    )
    round_ids.update(case["round_id"] for case in results["source_evidence"]["cases"])
    ledger = json.loads((path / "pilot-report.json").read_text(encoding="utf-8"))
    results["questions"] = [
        q for q in ledger["questions"] if q["round_id"] in round_ids
    ]
    results.update(
        instance=name,
        mock_only=True,
        cost_usd=0,
        passed=True,
        human_review_seconds=None,
        real_erp_accuracy=None,
    )
    (out / "result.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(str(out), flush=True)
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    run(parser.parse_args().name)
