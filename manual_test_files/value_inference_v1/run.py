"""Explicit real-stack runner; NEVER imported or executed during implementation.

Uses the existing resumable harness. Isolated customers; no approve/export; source/oracle separation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
sys.path.insert(0, str(REPO / "manual_test_files/tools"))
import global_eval as harness
from global_score import score


def evaluate(snapshot, item):
    # The harness saves before.json before calling this function. Oracle is never sent to FORMA.
    expected = harness.read(ROOT / item["expected"])
    schema = harness.read(ROOT / item["schema"])
    review = harness.read(ROOT / item["review"])
    result = score(snapshot["preview"].get("records", []), expected, schema)
    questions = snapshot.get("questions", {}).get("questions", [])
    policy = (snapshot.get("mapping") or {}).get("policy") or {}
    needed = [q for q in review["questions"] if q.get("needs_review")]
    targets = {q.get("target") or q.get("field", {}).get("pointer") for q in questions}
    result.update(
        {
            "expected_questions": needed,
            "observed_questions": questions,
            "expected_target_asked": {q["target"]: q["target"] in targets for q in needed},
            "question_correctness": "Requires human audit of the exact missing fact and offered action; target matching alone is not a pass.",
            "decision": policy.get("decision"),
            "verification": policy.get("verification"),
            "unsafe_auto_approval": policy.get("decision") == "auto_approve"
            and (bool(needed) or result["penalized_accuracy"] != 1),
            "score_scope": "Before human help; do not penalize abstention on oracle-only choices as a factual mapping error. Report unresolved fields separately.",
            "cost_usd": sum(
                e.get("cost_usd", 0) or 0 for e in (snapshot.get("mapping") or {}).get("agent_log", [])
            ),
        }
    )
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1")
    parser.add_argument("--model", default="deepseek-flash")
    parser.add_argument("--case", action="append")
    args = parser.parse_args()
    if not args.run_id or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in args.run_id):
        raise SystemExit("Use a lowercase run ID containing letters, digits, hyphen or underscore")
    manifest = harness.read(ROOT / "manifest.json")
    folder = ROOT / "runs" / args.run_id
    folder.mkdir(parents=True, exist_ok=True)
    harness.ROOT = ROOT
    harness.evaluate_snapshot = evaluate
    with httpx.Client(base_url=args.base_url.rstrip("/") + "/api", timeout=180) as client:
        readiness = httpx.get(args.base_url.rstrip("/") + "/ready", timeout=30).json()
        if readiness.get("status") != "ok":
            raise RuntimeError("FORMA is not ready")
        config = harness.request(client, "GET", "/settings/ai")
        if config.get("model") != args.model:
            raise RuntimeError("Configured model differs from the comparison model; no evaluation started")
        metadata = {
            "model": config.get("model"),
            "provider": config.get("provider"),
            "manifest_sha256": hashlib.sha256((ROOT / "manifest.json").read_bytes()).hexdigest(),
            "local_code_hashes": {
                str(p.relative_to(REPO)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted((REPO / "backend/src/forma").rglob("*.py"))
            },
            "note": "Local hashes do not prove deployed code identity. Record the API/worker image IDs separately before comparing runs.",
        }
        meta_path = folder / "metadata.json"
        if meta_path.exists():
            if harness.read(meta_path)["configuration"] != metadata:
                raise RuntimeError("Model, source code or manifest changed; use a new run ID")
        else:
            harness.save(meta_path, {"started_at": datetime.now(UTC).isoformat(), "configuration": metadata})
        results = []
        for item in manifest["rounds"]:
            if args.case and item["case"] not in args.case:
                continue
            if hashlib.sha256((ROOT / item["schema"]).read_bytes()).hexdigest() != item["schema_sha256"]:
                raise RuntimeError("Schema changed after sealing the suite")
            results.append(harness.run_case(client, item, folder, args.run_id))
            print(item["id"], "snapshot saved", flush=True)
        harness.save(folder / "summary.json", results)


if __name__ == "__main__":
    main()
