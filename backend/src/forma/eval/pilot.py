"""Offline paired evaluation preparation. No credentials, database or paid providers.

The echo provider tests agent plumbing, not reasoning. Gold is only read by the
scorer. Live evaluations must later use the same frozen inputs and independent labels.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from forma.ai.llm import LLMClient, MockProvider
from forma.eval import benchmark
from forma.schema_registry import TargetSchema


def read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def freeze(manifest_path: Path) -> dict[str, str]:
    """Constrain fixtures to their corpus; refuse non-synthetic data in this rehearsal."""
    manifest = read(manifest_path)
    if manifest.get("synthetic") is not True:
        raise ValueError("Offline rehearsal requires an explicitly synthetic corpus")
    ids = [case["id"] for case in manifest["cases"]]
    if (
        not ids
        or len(set(ids)) != len(ids)
        or any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", i) for i in ids)
    ):
        raise ValueError("Case IDs must be unique safe directory names")
    root = manifest_path.resolve().parent
    paths = {manifest_path.name}
    for case in manifest["cases"]:
        if case.get("mapping") or case.get("baseline_output") or case.get("approved_mapping"):
            raise ValueError("Cold paired pilot must generate its own mappings")
        paths.update([*case["files"], case["schema"], case["expected"], case["question_gold"]])
    for name in paths:
        if not (root / name).resolve().is_relative_to(root):
            raise ValueError("Corpus path escapes its root")
    for case in manifest["cases"]:
        schema_path = root / case["schema"]
        schema = TargetSchema(case["schema_id"], 1, read(schema_path), schema_path)
        gold = read(root / case["expected"])
        if not isinstance(gold, list) or not gold or any(schema.validate_record(row) for row in gold):
            raise ValueError("Gold must contain nonempty schema-valid records")
        benchmark.score([], gold, schema)  # Also rejects duplicate identities in the key.
    return {name: digest(root / name) for name in sorted(paths)}


def echo_draft(_system: str, user: str, _schema: dict[str, Any]) -> dict[str, Any]:
    """Only receives normal agent input. Does not read or close over gold files."""
    # Structured retries append validation feedback after the initial JSON object.
    payload, _ = json.JSONDecoder().raw_decode(user.lstrip())
    if "draft_mapping" in payload:
        return {
            "mapping": payload["draft_mapping"],
            "open_questions": payload.get("draft_open_questions", []),
            "confidence": 0.5,
            "changes_from_draft": [],
        }
    return {"tables": [], "notes": ["Offline echo; no semantic classification performed."]}


def question_items(case: dict[str, Any]) -> list[dict[str, Any]]:
    trace = case["trace"]
    data = trace.get("mapping", {}).get("data", trace)
    if any(n.startswith("llm_failed") for n in trace.get("mapping", {}).get("notes", [])):
        data = {"open_questions": trace.get("draft_open_questions", [])}
    entries = [*data.get("clarifications", []), *data.get("open_questions", [])]
    decisions = trace.get("decision_questions", [])
    targets = {q["target"] for q in decisions}
    prompts = {q["question"] for q in decisions}
    entries = [
        q for q in entries if (q.get("target") not in targets if isinstance(q, dict) else q not in prompts)
    ]
    entries.extend(decisions)
    return [
        {
            "id": hashlib.sha256(json.dumps([case["id"], i, q], sort_keys=True).encode()).hexdigest()[:16],
            "raw": q,
            "target": q.get("target") if isinstance(q, dict) else None,
            "category": "not_assessed",
            "reviewer": None,
            "evidence_location": "",
        }
        for i, q in enumerate(entries)
    ]


def question_score(gold: dict[str, Any], observations: list[dict[str, Any]]) -> dict[str, Any]:
    """Human labels, never a substring classifier. Unknown labels cannot earn credit."""
    allowed = {"missing_information", "conflicting_evidence", "evidence_in_file", "not_assessed"}
    for q in observations:
        if q["category"] not in allowed:
            raise ValueError("Unknown question category")
        if q["category"] != "not_assessed" and (
            not q.get("reviewer") or not q.get("target") or not q.get("evidence_location")
        ):
            raise ValueError("Assessed questions require reviewer, target and evidence/scope checked")
    required = {(q["target"], q["category"]) for q in gold["required"]}
    assessed = [q for q in observations if q["category"] != "not_assessed"]
    found = {(q["target"], q["category"]) for q in assessed}
    unresolved = len(observations) - len(assessed)
    counts = Counter(q["target"] for q in assessed)
    necessary = [q for q in assessed if (q["target"], q["category"]) in required]
    return {
        "required_topics": len(required),
        "asked": len(observations),
        "unassessed": unresolved,
        "necessary_questions": len(necessary),
        "unnecessary_questions": sum(q["category"] == "evidence_in_file" for q in assessed),
        "duplicate_topics": sum(max(0, n - 1) for n in counts.values()),
        "missing_required_topics": sorted(required - found),
        "required_recall": None if unresolved or not required else len(required & found) / len(required),
        "necessary_precision": None if unresolved or not observations else len(necessary) / len(observations),
        "status": "pending_review" if unresolved else "assessed",
    }


def compare(left: dict[str, Any], right: dict[str, Any]) -> list[dict[str, Any]]:
    """Reject mismatched corpora rather than publishing an apples-to-oranges delta."""
    if left["frozen_sha256"] != right["frozen_sha256"]:
        raise ValueError("Different source, schema or gold hashes")
    a = {c["id"]: c for c in left["cases"]}
    b = {c["id"]: c for c in right["cases"]}
    if len(a) != len(left["cases"]) or len(b) != len(right["cases"]) or a.keys() != b.keys():
        raise ValueError("Missing or duplicate paired cases")
    return [
        {
            "id": key,
            "rules": a[key]["metrics"],
            "candidate": b[key]["metrics"],
            "field_delta": b[key]["metrics"]["field_accuracy"] - a[key]["metrics"]["field_accuracy"],
            "answerable_field_accuracy_rules": a[key]["answerable_field_accuracy"],
            "answerable_field_accuracy_candidate": b[key]["answerable_field_accuracy"],
            "decision_fields": a[key]["decision_fields"],
            "wrong_fields_rules": [p for p, v in a[key]["metrics"]["per_field"].items() if v < 1],
            "wrong_fields_candidate": [p for p, v in b[key]["metrics"]["per_field"].items() if v < 1],
            "rules_seconds": a[key]["seconds"],
            "candidate_seconds": b[key]["seconds"],
            "rules_verification": a[key]["verification"],
            "candidate_verification": b[key]["verification"],
            "candidate_agent_notes": b[key]["trace"].get("mapping", {}).get("notes", []),
        }
        for key in a
    ]


def run_pair(manifest_path: Path, out: Path) -> dict[str, Any]:
    frozen = freeze(manifest_path)
    out = out.resolve()
    # Results must never overwrite source inputs or the answer key.
    if out.is_relative_to(manifest_path.resolve().parent):
        raise ValueError("Output must be outside the frozen corpus")
    out.mkdir(parents=True, exist_ok=False)
    manifest = read(manifest_path)
    # A reviewer or independent mapper receives these folders, never the gold directory.
    for spec in manifest["cases"]:
        packet = out / "blind-inputs" / spec["id"]
        packet.mkdir(parents=True)
        sources = []
        for index, name in enumerate(spec["files"]):
            destination = f"source-{index}{Path(name).suffix}"
            shutil.copyfile(manifest_path.parent / name, packet / destination)
            sources.append(destination)
        shutil.copyfile(manifest_path.parent / spec["schema"], packet / "schema.json")
        write(
            packet / "task.json",
            {
                "case_id": spec["id"],
                "files": sources,
                "schema": "schema.json",
                "task": (
                    "Map to the supplied schema. Use file evidence before asking. "
                    "Ask when evidence is missing or conflicts."
                ),
                "response": {"records": [], "questions": [], "evidence": []},
            },
        )
    write(out / "corpus-lock.json", frozen)
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=manifest_path.parent, capture_output=True, text=True, check=False
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "diff", "--binary"], cwd=manifest_path.parent, capture_output=True, check=False
    ).stdout
    provider = MockProvider(echo_draft)
    reports = []
    for mode, client in [("rules", None), ("mock_echo", LLMClient(provider, model="offline-echo"))]:
        report = benchmark.run(manifest_path, client=client, include_output=True)
        for case in report["cases"]:
            spec = next(c for c in manifest["cases"] if c["id"] == case["id"])
            question_gold = read(manifest_path.parent / spec["question_gold"])
            decision_fields = sorted({q["target"] for q in question_gold["required"]})
            answerable = [v for p, v in case["metrics"]["per_field"].items() if p not in decision_fields]
            case["decision_fields"] = decision_fields
            case["answerable_field_accuracy"] = sum(answerable) / len(answerable) if answerable else None
        report.update(
            mode=mode,
            synthetic=True,
            semantic_ai_evaluation=False,
            execution_scope="component_schema_mapper_engine; not full production graph",
            question_scope=("agent questions only; final UI/policy questions measured separately on staging"),
            frozen_sha256=frozen,
            git_revision=revision,
            tracked_diff_sha256=hashlib.sha256(dirty).hexdigest(),
            harness_sha256={
                "pilot.py": digest(Path(__file__)),
                "benchmark.py": digest(Path(benchmark.__file__)),
            },
            cost_usd=0,
            provider="none" if client is None else "mock",
            model=None if client is None else "offline-echo",
        )
        if freeze(manifest_path) != frozen:
            raise ValueError("Corpus changed during evaluation")
        write(out / f"{mode}.json", report)
        reports.append(report)
    reviews = []
    for report in reports:
        for case in report["cases"]:
            spec = next(c for c in read(manifest_path)["cases"] if c["id"] == case["id"])
            gold = read(manifest_path.parent / spec["question_gold"])
            items = question_items(case)
            reviews.append(
                {
                    "mode": report["mode"],
                    "case": case["id"],
                    "questions": items,
                    "score": question_score(gold, items),
                }
            )
    write(out / "question-review.json", reviews)
    sessions = [
        {
            "case": c["id"],
            "method": method,
            "reviewer": None,
            "started_at": None,
            "finished_at": None,
            "active_seconds": None,
            "corrections": None,
            "post_approval_errors": None,
            "downstream_accepted": None,
            "question_clarity_1_to_5": None,
            "task_completed_without_help": None,
            "note": "Human measurement pending; never infer from browser runtime.",
        }
        for c in read(manifest_path)["cases"]
        for method in ("manual", "rules_assisted", "ai_assisted")
    ]
    write(out / "reviewer-sessions.json", sessions)
    result = {
        "synthetic": True,
        "semantic_ai_evaluation": False,
        "cost_usd": 0,
        "mock_calls": len(provider.calls),
        "cases": compare(*reports),
        "limitation": "Echo mock proves plumbing only. Real ERP and live AI scores are pending.",
    }
    write(out / "comparison.json", result)
    return result


def write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def score_review(manifest: Path, out: Path, annotations: Path) -> list[dict[str, Any]]:
    """Rescore signed human labels; no dropping difficult questions from the denominator."""
    if freeze(manifest) != read(out / "corpus-lock.json"):
        raise ValueError("Corpus changed since the run")
    original = read(out / "question-review.json")
    submitted = read(annotations)
    by_key = {(q["mode"], q["case"]): q for q in submitted}
    if len(by_key) != len(submitted) or set(by_key) != {(q["mode"], q["case"]) for q in original}:
        raise ValueError("Every case and mode must be reviewed, without duplicates")
    results = []
    for source in original:
        labels = by_key[source["mode"], source["case"]]["questions"]
        expected = {q["id"]: q["raw"] for q in source["questions"]}
        actual = {q["id"]: q["raw"] for q in labels}
        if len(actual) != len(labels) or actual != expected:
            raise ValueError("Questions cannot be added, removed, duplicated or rewritten")
        spec = next(c for c in read(manifest)["cases"] if c["id"] == source["case"])
        gold = read(manifest.parent / spec["question_gold"])
        results.append(
            {
                "mode": source["mode"],
                "case": source["case"],
                "score": question_score(gold, labels),
                "annotations": labels,
            }
        )
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--out", type=Path, required=True, help="New output directory; refuses overwrite")
    parser.add_argument(
        "--review", type=Path, help="Score annotated question-review JSON from an existing run"
    )
    args = parser.parse_args()
    if args.review:
        scored = score_review(args.manifest, args.out, args.review)
        write(args.out / "question-scores.json", scored)
        print(json.dumps({"case_modes": len(scored), "cost_usd": 0}))
    else:
        result = run_pair(args.manifest, args.out)
        print(json.dumps({"cases": len(result["cases"]), "mock_calls": result["mock_calls"], "cost_usd": 0}))


if __name__ == "__main__":
    main()
