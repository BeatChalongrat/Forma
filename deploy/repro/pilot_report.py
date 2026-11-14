"""Export a complete, paginated pilot review ledger from an owned mock-only instance."""

import argparse
import csv
import json
from collections import Counter

import httpx
import repro


def run(name: str) -> dict:
    path, marker, config = repro.load(name)
    repro.local_daemon()
    repro.owned_resources(marker, config)
    if not marker.get("pilot"):
        raise RuntimeError("Use a pilot instance, never dev or production.")
    with httpx.Client(
        base_url=f"http://127.0.0.1:{marker['ports'][0]}", trust_env=False, timeout=120
    ) as client:
        ai = client.get("/api/settings/ai").raise_for_status().json()
        assert ai["mock_only"] and ai["active_provider"] == "mock"

        def pages(endpoint):
            result, offset = [], 0
            while True:
                page = (
                    client.get(endpoint, params={"offset": offset})
                    .raise_for_status()
                    .json()
                )
                result.extend(page["items"])
                offset = page["next_offset"]
                if offset is None:
                    return result

        questions = pages("/api/pilot/questions")
        history = pages("/api/pilot/observations")
    counts = Counter(
        (q["observation"] or {}).get("category", "not_assessed") for q in questions
    )
    result = {
        "mock_only": True,
        "cost_usd": 0,
        "unit": "Question occurrence per mapping version, including revisions; not file accuracy",
        "counts": dict(counts),
        "total_question_occurrences": len(questions),
        "warning": "Synthetic flow test only. Unassessed is not missing information. No live-model accuracy measured.",
        "questions": questions,
        "observation_history": history,
    }
    (path / "pilot-report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (path / "pilot-questions.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as stream:
        fields = [
            "round_id",
            "mapping_id",
            "question_id",
            "question",
            "category",
            "note",
            "evidence_location",
            "actor",
        ]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for q in questions:
            obs = q["observation"] or {}
            question = q["question"]
            row = {k: q[k] for k in fields[:3]}
            row.update(
                question=question.get("question_th")
                or question.get("question")
                or q["question_id"],
                category=obs.get("category", "not_assessed"),
                **{k: obs.get(k, "") for k in fields[5:]},
            )
            # Keep file-provided text inert when a reviewer opens the CSV in Excel.
            writer.writerow(
                {
                    k: "'" + str(v)
                    if str(v).lstrip().startswith(("=", "+", "-", "@"))
                    else v
                    for k, v in row.items()
                }
            )
    return {
        "counts": dict(counts),
        "total_question_occurrences": len(questions),
        "report": str(path / "pilot-report.json"),
        "csv": str(path / "pilot-questions.csv"),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="pilot-nov14")
    print(json.dumps(run(parser.parse_args().name), indent=2))
