"""Run a case set through the live stack (real AI) and record what happened, round by round.

usage, from the repository root (the stack must be up at http://127.0.0.1):

    backend/.venv/Scripts/python manual_test_files/tools/airun.py SET OUT.json [CODE ...] [r1 r2 r3]

SET is a folder under manual_test_files (``erp`` or ``cases``). Codes default to every customer folder in the set
that has an r1; rounds default to every rN folder the customer has. Each round is uploaded to a customer named
``AI-<code><AIRUN_SUFFIX>``, proposed with the model, answered the way a person would (yes/no from the round's
own expected.json, keep the column the card reads, confirm the rest), approved, transformed, scored with
score.py against expected.json and exported. The per-round record (mapping, questions, self-check, scores) is
appended to OUT.json after every round, so a stopped run keeps what it did.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

sys.stdout.reconfigure(encoding="utf-8")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "erp" / "tools"))
from score import score  # noqa: E402

BASE = os.environ.get("FORMA_API", "http://127.0.0.1/api")
SUFFIX = os.environ.get("AIRUN_SUFFIX", "")
EXTENSIONS = {".xlsx", ".csv", ".xls", ".xlsb", ".txt", ".tsv"}
c = httpx.Client(base_url=BASE, timeout=180)


def customer(code: str) -> str:
    want = f"AI-{code}{SUFFIX}"
    for cu in c.get("/customers").json():
        if cu["code"] == want:
            return str(cu["id"])
    r = c.post("/customers", json={"code": want, "name": f"AI run — {code}{SUFFIX}", "notes": "real-AI test run"})
    assert r.status_code in (200, 201), r.text
    return str(r.json()["id"])


def wait(job_id: str, limit: float = 1200) -> dict[str, Any]:
    t0 = time.time()
    while time.time() - t0 < limit:
        j = c.get(f"/jobs/{job_id}").json()
        if j.get("status") in ("done", "failed"):
            return dict(j)
        time.sleep(3)
    return {"status": "timeout"}


def compact_log(log: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keep = ("agent", "result", "confidence", "attempts", "notes", "error", "fallback", "drift", "unmatched", "table_roles", "agree", "disagree", "model_only", "dropped", "aliases", "constants", "anchored", "anchors", "used")
    out = []
    for e in log:
        d = {k: v for k, v in e.items() if k in keep}
        u = e.get("usage") or {}
        if u:
            d["tokens"] = (u.get("input_tokens"), u.get("output_tokens"))
            d["calls"] = u.get("calls")
            d["cost_usd"] = u.get("cost_usd")
        data = e.get("data") if isinstance(e.get("data"), dict) else {}
        for k in ("changes_from_draft", "open_questions", "clarifications"):
            if data.get(k):
                d[k] = data[k][:8]
        if data.get("self_check"):
            sc = data["self_check"]
            d["self_check"] = {k: sc.get(k) for k in ("rows_checked", "rows_out", "fix_rounds", "inspections", "grounded", "python_turns", "complete", "problems_left")}
            d["self_check"]["history"] = [{k: v for k, v in h.items() if k not in ("problems", "coverage", "code", "result")} | ({"problems": h["problems"][:3]} if h.get("problems") else {}) for h in sc.get("history", [])]
        out.append(d)
    return out


def mapping_of(rid: str) -> dict[str, Any] | None:
    r = c.get(f"/rounds/{rid}").json()
    if not r.get("mappings"):
        return None
    latest = max(r["mappings"], key=lambda m: m.get("version") or 0)
    return dict(c.get(f"/mappings/{latest['id']}").json())


def vat_truth(want: list[dict[str, Any]]) -> bool:
    flags = [p.get("vat_included") for r in want for p in (r.get("prices") or []) if p.get("vat_included") is not None]
    return (sum(1 for f in flags if f) * 2 >= len(flags)) if flags else True


def person_answers(m: dict[str, Any], want: list[dict[str, Any]]) -> dict[str, Any]:
    """What an operator would do with the card: yes/no from the answer key, keep the column read, confirm."""
    q = c.get(f"/mappings/{m['id']}/questions").json()
    answers: dict[str, Any] = {}
    for item in q.get("questions", []):
        sem = item["field"].get("semantic")
        if item["kind"] == "boolean" and sem == "price.vat_included":
            answers[item["id"]] = vat_truth(want)
        elif item["kind"] == "confirm":
            answers[item["id"]] = "confirm"
        elif item["kind"] in ("choice", "enum") and item.get("options"):
            answers[item["id"]] = item["options"][0]
    note = {
        "questions": [(i["id"], i["kind"], i.get("options") or (i.get("evidence") or [""])[-1][:90], "contested" if i.get("contested") else "", i.get("scope") or "") for i in q.get("questions", [])],
        "open_questions": q.get("open_questions", []),
        "reasons": q.get("reasons", []),
    }
    if not answers:
        return {**note, "applied": None}
    pv = c.post(f"/mappings/{m['id']}/answers/preview", json={"answers": answers})
    if pv.status_code >= 300:
        return {**note, "applied": f"preview {pv.status_code} {pv.text[:200]}"}
    ap = c.post(f"/mappings/{m['id']}/answers/apply", json={"answers": answers, "digest": pv.json()["digest"]})
    return {**note, "answers": answers, "applied": ap.status_code, "new_mapping": ap.json().get("id") if ap.status_code < 300 else ap.text[:200]}


def run_round(root: Path, code: str, cust: str, rn: str) -> dict[str, Any]:
    folder = root / code / rn
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() in EXTENSIONS)
    want = json.loads((folder / "expected.json").read_text(encoding="utf-8"))
    rec: dict[str, Any] = {"code": code, "round": rn, "files": [p.name for p in files]}
    t0 = time.time()
    rid = c.post("/rounds", json={"customer_id": cust, "label": f"{code} {rn} — AI run"}).json()["id"]
    rec["round_id"] = rid
    up = c.post(f"/rounds/{rid}/files", files=[("files", (p.name, p.read_bytes(), "application/octet-stream")) for p in files])
    assert up.status_code == 201, up.text
    j = wait(c.post(f"/rounds/{rid}/run", json={"use_llm": True}).json()["job_id"])
    rec["job"] = {"status": j.get("status"), "error": str(j.get("error") or "")[:300] or None, "seconds": round(time.time() - t0, 1)}
    r = c.get(f"/rounds/{rid}").json()
    s = r.get("summary") or {}
    rec["status"] = r["status"]
    rec["summary"] = {k: s.get(k) for k in ("decision", "drift", "rows", "error") if s.get(k) is not None}
    m = mapping_of(rid)
    if m:
        pol = m.get("policy") or {}
        rec["mapping"] = {
            "id": m["id"],
            "created_by": m.get("created_by"),
            "model": m.get("model"),
            "confidence": m.get("confidence"),
            "decision": pol.get("decision"),
            "reasons": [str(x)[:120] for x in pol.get("reasons", [])][:8],
            "verification": {k: pol.get("verification", {}).get(k) for k in ("complete", "passed", "problems")},
            "agreement": {k: (len(v) if isinstance(v, (list, dict)) else v) for k, v in (pol.get("agreement") or {}).items()},
            "confidence_basis": pol.get("confidence_basis"),
            "open_questions": [str(q)[:160] for q in (m.get("open_questions") or [])][:10],
            "agent_log": compact_log(m.get("agent_log") or []),
        }
    if m and m.get("status") == "proposed" and r["status"] in ("review", "proposed", "blocked"):
        rec["person"] = person_answers(m, want)
        m = mapping_of(rid) or m
        if r["status"] != "blocked":
            ap = c.post(f"/mappings/{m['id']}/approve", json={})
            rec["person"]["approve_mapping"] = ap.status_code if ap.status_code < 300 else f"{ap.status_code} {ap.text[:200]}"
    r = c.get(f"/rounds/{rid}").json()
    if not r.get("datasets") and r["status"] not in ("blocked", "failed", "cancelled"):
        tj = c.post(f"/rounds/{rid}/transform").json()
        if "job_id" in tj:
            wait(tj["job_id"])
        r = c.get(f"/rounds/{rid}").json()
    if r.get("datasets"):
        d = c.get(f"/datasets/{r['datasets'][-1]['id']}").json()
        rows = c.get(f"/datasets/{d['id']}/rows", params={"limit": 500}).json()["rows"]
        rec["dataset"] = {"id": d["id"], "rows": d.get("row_count"), "invalid": d.get("invalid_records")}
        rec["score"] = score(rows, want)
        (folder / "got.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        if d.get("status") not in ("approved", "published") and rec["score"]["field_accuracy"] is not None:
            a = c.post(f"/datasets/{d['id']}/approve", json={"acknowledge_anomalies": True})
            e = c.post(f"/datasets/{d['id']}/export", json={"format": "json"})
            rec["dataset"]["approve"] = a.status_code if a.status_code < 300 else f"{a.status_code} {a.text[:160]}"
            rec["dataset"]["export"] = e.status_code if e.status_code < 300 else f"{e.status_code} {e.text[:160]}"
    rec["final_status"] = c.get(f"/rounds/{rid}").json()["status"]
    rec["seconds_total"] = round(time.time() - t0, 1)
    return rec


def main() -> None:
    root = HERE.parent / sys.argv[1]
    out_path = Path(sys.argv[2])
    args = sys.argv[3:]
    rounds = [a for a in args if a in ("r1", "r2", "r3")]
    codes = [a for a in args if a not in rounds] or sorted(p.name for p in root.iterdir() if (p / "r1").is_dir())
    results: list[dict[str, Any]] = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else []
    for code in codes:
        cust = customer(code)
        for rn in rounds or sorted(p.name for p in (root / code).iterdir() if p.is_dir() and p.name.startswith("r")):
            try:
                rec = run_round(root, code, cust, rn)
            except Exception as e:  # keep going: the report needs every row
                rec = {"code": code, "round": rn, "crash": f"{type(e).__name__}: {e}"[:400]}
            results.append(rec)
            out_path.write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
            sc = rec.get("score") or {}
            m = rec.get("mapping") or {}
            print(code, rn, rec.get("status"), "->", rec.get("final_status"), "|", m.get("created_by"), m.get("confidence"), (rec.get("summary") or {}).get("decision"), (rec.get("summary") or {}).get("drift"), "| acc", sc.get("field_accuracy"), "rows", sc.get("rows_got"), "| card", len((rec.get("person") or {}).get("questions", [])), "free", len(m.get("open_questions") or []), "|", rec.get("job", {}).get("seconds"), "s", rec.get("crash", ""))
            sys.stdout.flush()
    print("done")


if __name__ == "__main__":
    main()
