"""Run a resumable, non-publishing evaluation against the real FORMA API.

python manual_test_files/tools/global_eval.py --run-id <unique> [--phase cold|warm|all]
No answer key is read until a complete prediction snapshot has been saved.
Never approves, transforms or exports a mapping. Human answer actions are separate, reviewed requests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from global_score import score

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / "manual_test_files/acommerce_global_v1"


def read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    temp.replace(path)


def request(c: httpx.Client, method: str, path: str, **kw: Any) -> Any:
    response = c.request(method, path, **kw)
    response.raise_for_status()
    return response.json()


def wait(c: httpx.Client, job: str, timeout: int = 1500) -> dict[str, Any]:
    start = time.monotonic()
    while True:
        result = request(c, "GET", f"/jobs/{job}")
        if result["status"] in ("done", "failed", "cancelled"):
            return result
        if time.monotonic() - start > timeout:
            raise TimeoutError(
                f"Job {job} still running; resume polls the same job, never duplicates it"
            )
        time.sleep(3)


def snapshot(c: httpx.Client, rid: str) -> dict[str, Any]:
    r = request(c, "GET", f"/rounds/{rid}")
    mappings = r.get("mappings") or []
    if not mappings:
        return {
            "round": r,
            "mapping": None,
            "questions": {},
            "preview": {"records": []},
        }
    m = request(
        c, "GET", f"/mappings/{max(mappings, key=lambda m: m.get('version', 0))['id']}"
    )
    questions = request(c, "GET", f"/mappings/{m['id']}/questions")
    preview = request(
        c,
        "POST",
        f"/rounds/{rid}/mapping-preview",
        json={"document": m["document"], "limit": 500},
    )
    return {"round": r, "mapping": m, "questions": questions, "preview": preview}


def evaluate_snapshot(before: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    # The mapper has already finished before the evaluator accesses these files.
    expected = read(ROOT / item["expected"])
    schema = read(ROOT / item["schema"])
    review = read(ROOT / item["review"])
    result = score(before["preview"].get("records", []), expected, schema)
    result["phase"] = (
        "before clarification; gold-dependent unresolved fields are not necessarily mapper errors"
        if review["questions"]
        else "before human assistance"
    )
    result["expected_questions"] = review["questions"]
    return result


def prepare_schema(c: httpx.Client, item: dict[str, Any], run_id: str) -> str:
    schema = read(ROOT / item["schema"])
    sid = ("eval_" + run_id + "_" + item["case"]).lower()
    schemas = request(c, "GET", "/schemas")
    existing = next((s for s in schemas if s.get("ref") == sid + "@1"), None)
    if existing:
        return existing["ref"]
    # Audit entity IDs include @version and have a bounded database column.
    if len(sid) > 35:
        sid = "eval_" + hashlib.sha256(sid.encode()).hexdigest()[:24]
    schema["$id"] = "https://forma.local/eval/" + sid
    schema.setdefault("x-forma", {}).update({"schema_id": sid, "version": 1})
    schema["x-forma"].setdefault("identity_keys", ["/" + schema["required"][0]])
    schemas = request(c, "GET", "/schemas")
    found = next((s for s in schemas if s.get("ref") == sid + "@1"), None)
    if not found:
        found = request(c, "POST", "/schemas", json={"document": schema})
    return found.get("ref") or sid + "@1"


def run_case(
    c: httpx.Client, item: dict[str, Any], folder: Path, run_id: str
) -> dict[str, Any]:
    dest = folder / item["id"]
    dest.mkdir(exist_ok=True)
    state_path = dest / "state.json"
    state = (
        read(state_path)
        if state_path.exists()
        else {"id": item["id"], "started_at": datetime.now(UTC).isoformat()}
    )
    if state.get("complete"):
        return state
    started = time.monotonic()
    task = (ROOT / item["task"]).read_text(encoding="utf-8")
    task_hash = hashlib.sha256(task.encode()).hexdigest()
    if state.get("task_hash") and state["task_hash"] != task_hash:
        raise RuntimeError("Task changed during run")
    state["task_hash"] = task_hash
    for f in item["files"]:
        if hashlib.sha256((ROOT / f["path"]).read_bytes()).hexdigest() != f["sha256"]:
            raise RuntimeError("Input hash mismatch: " + f["path"])
    if not state.get("customer_id"):
        code = f"EVAL-{run_id}-{item['case']}"
        customers = request(c, "GET", "/customers")
        customer = next((x for x in customers if x["code"] == code), None)
        if not customer:
            customer = request(
                c,
                "POST",
                "/customers",
                json={
                    "code": code,
                    "name": code,
                    "notes": "Isolated international evaluation; no exports",
                },
            )
        state["customer_id"] = customer["id"]
        save(state_path, state)
    if not state.get("schema_ref"):
        baseline_path = folder / (item["case"] + "_r1") / "state.json"
        baseline = read(baseline_path) if baseline_path.exists() else {}
        state["schema_ref"] = baseline.get("schema_ref") or prepare_schema(
            c, item, run_id
        )
        save(state_path, state)
        # A job must read the newly committed schema without a client-side TTL sleep.
    if not state.get("round_id"):
        # Recover a created round if the HTTP response was lost before checkpointing.
        label = f"EVAL {run_id} {item['id']}"
        available = request(
            c, "GET", "/rounds", params={"customer_id": state["customer_id"]}
        )
        old = next(
            (
                r
                for r in available
                if r["customer_id"] == state["customer_id"] and r["label"] == label
            ),
            None,
        )
        r = old or request(
            c,
            "POST",
            "/rounds",
            json={
                "customer_id": state["customer_id"],
                "label": label,
                "target_schema": state["schema_ref"],
                "instructions": task,
            },
        )
        if r.get("notes") != task:
            raise RuntimeError("Round API did not persist task instructions")
        state["round_id"] = r["id"]
        save(state_path, state)
    rid = state["round_id"]
    if not state.get("uploaded"):
        r = request(c, "GET", f"/rounds/{rid}")
        present = {f.get("sha256") for f in r.get("files", [])}
        for source in item["files"]:
            if source["sha256"] in present:
                continue
            p = ROOT / source["path"]
            request(
                c,
                "POST",
                f"/rounds/{rid}/files",
                files=[("files", (p.name, p.read_bytes(), "application/octet-stream"))],
            )
        state["uploaded"] = True
        save(state_path, state)
    for stage in ("profile", "propose"):
        if state.get(stage + "_done"):
            continue
        if not state.get(stage + "_job"):
            current = request(c, "GET", f"/rounds/{rid}")
            kind = "profile_round" if stage == "profile" else "propose_mapping"
            prior = next(
                (j for j in current.get("jobs", []) if j.get("kind") == kind), None
            )
            response = (
                {"job_id": prior["id"]}
                if prior
                else request(
                    c,
                    "POST",
                    f"/rounds/{rid}/{stage}",
                    **({"json": {"use_llm": True}} if stage == "propose" else {}),
                )
            )
            state[stage + "_job"] = response["job_id"]
            save(state_path, state)
        job = wait(c, state[stage + "_job"])
        save(dest / (stage + "-job.json"), job)
        state[stage + "_status"] = job["status"]
        save(state_path, state)
        if job["status"] != "done":
            state["failed_stage"] = stage
            state["complete"] = True
            save(state_path, state)
            return state
        state[stage + "_done"] = True
        save(state_path, state)
    before = snapshot(c, rid)
    save(dest / "before.json", before)
    log = (before.get("mapping") or {}).get("agent_log") or []
    evidence = next((e for e in log if e.get("agent") == "operator_requirements"), None)
    if not evidence or evidence.get("sha256") != task_hash:
        raise RuntimeError("Missing or mismatched task delivery evidence")
    result = evaluate_snapshot(before, item)
    save(dest / "score-before.json", result)
    state.update(
        {
            "complete": True,
            "elapsed_active_seconds": round(time.monotonic() - started, 2),
            "finished_at": datetime.now(UTC).isoformat(),
            "mapping_id": (before.get("mapping") or {}).get("id"),
            "field_accuracy": result["field_accuracy"],
            "exact_record_accuracy": result["exact_record_accuracy"],
            "questions": len(before["questions"].get("questions", [])),
            "decision": (before.get("mapping") or {}).get("policy", {}).get("decision"),
        }
    )
    save(state_path, state)
    return state


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--phase", choices=["cold", "warm", "all"], default="cold")
    parser.add_argument("--only", nargs="*")
    args = parser.parse_args()
    if not args.run_id.replace("-", "").replace("_", "").isalnum():
        raise ValueError("Invalid run ID")
    folder = ROOT / "runs" / args.run_id
    folder.mkdir(parents=True, exist_ok=True)
    manifest = read(ROOT / "manifest.json")
    items = list(manifest["rounds"])
    alt = manifest["alternate_contract"]
    original = next(
        i for i in items if i["case"] == "G05_sg_currency" and i["round"] == "r1"
    )
    items.append(
        {
            **original,
            "id": "G05_sg_currency_alt",
            "case": "G05_sg_currency_alt",
            "task": alt["task"],
            "schema": alt["schema"],
            "expected": alt["expected"],
        }
    )
    with httpx.Client(base_url="http://127.0.0.1/api", timeout=180) as c:
        ready = httpx.get("http://127.0.0.1/ready").json()
        if ready.get("status") != "ok":
            raise RuntimeError("Stack not ready")
        settings = request(c, "GET", "/settings/ai")
        # Store only public metadata, never API keys or endpoint credentials.
        model = settings.get("model") or (settings.get("primary") or {}).get("model")
        if model != "deepseek-flash":
            raise RuntimeError("Expected deepseek-flash, got " + str(model))
        metadata = {
            "model": model,
            "run_id": args.run_id,
            "commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
            ).strip(),
            "manifest_sha256": hashlib.sha256(
                (ROOT / "manifest.json").read_bytes()
            ).hexdigest(),
            "backend_files_sha256": {str(p.relative_to(REPO)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((REPO / "backend/src/forma").rglob("*.py"))},
            "worker_image": subprocess.check_output(["docker", "inspect", "--format", "{{.Image}}", "forma-worker-1"], cwd=REPO, text=True).strip(),
            "notes": "No approve/export; source schema refs isolated per case; instruction bridge is an uncommitted change.",
        }
        save(folder / "metadata.json", metadata)
        for item in items:
            if args.phase == "cold" and item["round"] != "r1":
                continue
            if args.phase == "warm" and item["round"] == "r1":
                continue
            if args.only and item["id"] not in args.only:
                continue
            try:
                state = run_case(c, item, folder, args.run_id)
                print(
                    item["id"],
                    state.get("decision"),
                    state.get("field_accuracy"),
                    state.get("exact_record_accuracy"),
                    "questions",
                    state.get("questions"),
                    "failed",
                    state.get("failed_stage"),
                    flush=True,
                )
            except Exception as e:  # noqa: BLE001 -- retain per-case evidence and continue the suite
                save(
                    folder / item["id"] / "error.json",
                    {"error": str(e), "type": type(e).__name__},
                )
                print(item["id"], "ERROR", type(e).__name__, str(e)[:180], flush=True)


if __name__ == "__main__":
    main()
