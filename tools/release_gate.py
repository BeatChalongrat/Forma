"""Seal same-revision release evidence. Does not publish, deploy or access application data."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

REQUIRED = {
    "backend": {"backend", "backend-coverage", "ruff", "format", "types"},
    "frontend": {"frontend-test-coverage", "frontend-build", "frontend-coverage"},
    "browser": {"browser-chat", "browser-review"},
    "eval": {"eval-baseline", "mapping-contracts"},
    "runtime": {
        "stack-up",
        "smoke",
        "review_smoke",
        "runtime-load",
        "real-renderer",
        "stack-cleanup",
    },
}


def verify(evidence: Path, sha: str, run_id: str, attempt: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Expected a full commit SHA")
    summaries = list(evidence.rglob("summary.json"))
    selected = {}
    for suite, required in REQUIRED.items():
        matches = []
        for path in summaries:
            data = json.loads(path.read_text(encoding="utf-8"))
            gates = data.get("gates", [])
            names = [gate.get("name") for gate in gates]
            if required <= set(names):
                matches.append((path, data, gates, names))
        if len(matches) != 1:
            raise ValueError(f"{suite}: missing or ambiguous evidence")
        path, data, gates, names = matches[0]
        source = data.get("source", {})
        if (
            data.get("status") != "passed"
            or data.get("model") != "mock"
            or data.get("paid_api_calls") != 0
            or any(g.get("exit_code") != 0 for g in gates)
            or len(names) != len(set(names))
            or source.get("sha") != sha
            or source.get("dirty") is not False
            or source != data.get("source_after")
            or str(data.get("run_id")) != run_id
            or str(data.get("run_attempt")) != attempt
        ):
            raise ValueError(f"{suite}: failed, stale, dirty or mixed-run evidence")
        selected[suite] = path.relative_to(evidence).as_posix()
    inventory = list(evidence.rglob("images.json"))
    if len(inventory) != 1:
        raise ValueError("Missing or ambiguous image inventory")
    images = json.loads(inventory[0].read_text(encoding="utf-8"))
    if images.get("sha") != sha or set(images.get("images", {})) != {
        "api",
        "web",
        "sandbox",
    }:
        raise ValueError("Image inventory does not match the candidate")
    if any(
        not re.fullmatch(r"sha256:[0-9a-f]{64}", v) for v in images["images"].values()
    ):
        raise ValueError("Images must be recorded by immutable ID")
    return {
        "status": "passed",
        "sha": sha,
        "run_id": run_id,
        "run_attempt": attempt,
        "suites": selected,
        "images": images["images"],
        "evidence_sha256": {
            p.relative_to(evidence).as_posix(): hashlib.sha256(
                p.read_bytes()
            ).hexdigest()
            for p in sorted(evidence.rglob("*"))
            if p.is_file()
        },
        "scope": "Mock release qualification; not real-model accuracy or production OIDC/TLS acceptance",
    }


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--attempt", default="")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--record-images", action="store_true")
    args = parser.parse_args()
    if args.record_images:
        result = {"sha": args.sha, "images": {}}
        for service in ("api", "web", "sandbox"):
            result["images"][service] = subprocess.check_output(
                [
                    "docker",
                    "image",
                    "inspect",
                    f"forma-{service}:ci",
                    "--format",
                    "{{.Id}}",
                ],
                text=True,
            ).strip()
    else:
        if not args.evidence or not args.run_id or not args.attempt:
            parser.error("Evidence, run ID and attempt are required")
        result = verify(args.evidence, args.sha, args.run_id, args.attempt)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
