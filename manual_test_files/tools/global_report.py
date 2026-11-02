"""Aggregate frozen international benchmark evidence; never calls the model."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
from pathlib import Path

from global_eval import ROOT, read, save


def summarize(folder: Path) -> list[dict]:
    rows = []
    for p in sorted(folder.glob("*/before.json")):
        b = read(p)
        state = read(p.parent / "state.json")
        if not state.get("complete") or b["round"]["id"] != state.get("round_id"):
            continue  # Never score a stale snapshot while a replacement attempt is running.
        m = b["mapping"]
        policy = m.get("policy", {})
        q = b["questions"]
        s = read(p.parent / "score-before.json")
        log = m.get("agent_log", [])
        usage = [x.get("usage", {}) for x in log]
        sc = policy.get("self_check") or {}
        ver = policy.get("verification") or {}
        disagreement = (policy.get("agreement") or {}).get("disagree", {})
        if isinstance(disagreement, int):
            ndis = disagreement
        else:
            ndis = len(disagreement)
        jobs = [read(j) for j in p.parent.glob("*-job.json")]
        flags = []
        if s["rows_got"] != 12:
            flags.append("rows != 12")
        if s["extra_rows"]:
            flags.append("extra SKU rows")
        if any(x["reason"] == "extra collection member" for x in s["extra_values"]):
            flags.append("extra collection items")
        if ndis >= 8:
            flags.append("disagree >= 8")
        if len(q["questions"]) > 8:
            flags.append("cards > 8")
        if ver.get("passed") is False:
            flags.append("verification false")
        if (policy.get("checks") or {}).get("llm_fallback") or any(
            x.get("agent") == "llm_failed" for x in log
        ):
            flags.append("LLM fallback")
        initial_failure = p.parent / "initial-profile-failure.json"
        if initial_failure.exists():
            flags.append("initial profile failed, recovered")
        started, finished = state.get("started_at"), state.get("finished_at")
        elapsed = (
            (
                datetime.fromisoformat(finished) - datetime.fromisoformat(started)
            ).total_seconds()
            if started and finished
            else None
        )
        rows.append(
            {
                "id": p.parent.name,
                "score": s,
                "questions": len(q["questions"]),
                "question_types": dict(Counter(x["kind"] for x in q["questions"])),
                "question_fields": [x["id"] for x in q["questions"]],
                "open_questions": q.get("open_questions", []),
                "contested": sum(bool(x.get("contested")) for x in q["questions"]),
                "decision": policy.get("decision"),
                "source": policy.get("source"),
                "reused_from": policy.get("reused_from"),
                "drift": (policy.get("checks") or {}).get("drift"),
                "disagree": ndis,
                "verification_passed": ver.get("passed"),
                "verification_problems": ver.get("problems", []),
                "self_check": {
                    k: sc.get(k) for k in ("fix_rounds", "grounded", "python_turns")
                },
                "elapsed_seconds": elapsed,
                "job_seconds": sum((x.get("duration_ms") or 0) / 1000 for x in jobs),
                "cost_usd_log": sum(x.get("cost_usd", 0) for x in usage),
                "calls": sum(x.get("calls", 0) for x in usage),
                "input_tokens": sum(x.get("input_tokens", 0) for x in usage),
                "output_tokens": sum(x.get("output_tokens", 0) for x in usage),
                "flags": flags,
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    args = parser.parse_args()
    folder = ROOT / "runs" / args.run_id
    rows = summarize(folder)
    save(folder / "summary.json", rows)
    lines = [
        "| กรณี/รอบ | Field % | Exact strict % | แถว | การ์ด (ชนิด) / ข้อความ | Contested | Decision / drift | Fix / ground / Python | งานวินาที | USD ตาม log | ธง |",
        "|---|---:|---:|---:|---|---:|---|---|---:|---:|---|",
    ]
    for r in rows:
        s = r["score"]
        kinds = ", ".join(f"{k}:{v}" for k, v in r["question_types"].items())
        sc = "/".join(
            str(v) if v is not None else "–" for v in r["self_check"].values()
        )
        lines.append(
            f"| {r['id']} | {s['field_accuracy'] * 100:.2f} | {s['exact_record_accuracy'] * 100:.2f} | "
            f"{s['rows_got']} | {r['questions']} ({kinds}) / {len(r['open_questions'])} | {r['contested']} | "
            f"{r['decision']} / {r['drift'] or '–'} | {sc} | {r['job_seconds']:.1f} | "
            f"{r['cost_usd_log']:.6f} | {', '.join(r['flags']) or '–'} |"
        )
    (folder / "RESULT-TABLE.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        f"{len(rows)} results; logged estimate USD {sum(r['cost_usd_log'] for r in rows):.6f}"
    )


if __name__ == "__main__":
    main()
