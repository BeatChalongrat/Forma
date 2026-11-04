"""Small, sealed, sequential before/after comparison. No approval, export or provider switch.

Reuses source fixtures, not previous predictions. Oracle read happens only after snapshot save.
This is a regression/architecture comparison, not an independent holdout benchmark.
"""
import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
sys.path.insert(0, str(REPO / "manual_test_files/tools"))
import global_eval as h
from global_score import score

SELECTION = {
    "relationships_v1": ("manifest-neutral.json", {"T02", "T04", "T06", "T07", "T08", "T09", "T10", "T12"}),
    "acommerce_global_v1": ("manifest.json", {"G02_id_decimal", "G09_jp_utf16"}),
}


def evaluate(snapshot, item):
    contract = h.read(h.ROOT / item["schema"])
    if "inventory" in contract["properties"]:
        contract.setdefault("x-forma", {}).setdefault("collections", {}).setdefault("/inventory", {})["identity_keys"] = ["/warehouse_code"]
    result = score(snapshot["preview"].get("records", []), h.read(h.ROOT / item["expected"]), contract)
    result["expected_questions"] = h.read(h.ROOT / item["review"])["questions"]
    result["phase"] = "before human answers; unresolved business facts audited separately"
    return result


def deployed():
    script = "import json; from forma.core.settings import get_settings; from forma.services.context import get_context; c=get_context(); print(json.dumps({'mode':get_settings().mapping_workflow,'model':c.llm.model,'provider':c.llm.provider.name,'fallbacks':len(c.llm.fallbacks)}))"
    return json.loads(subprocess.check_output(["docker", "exec", "forma-worker-1", "python", "-c", script], cwd=REPO, text=True).strip().splitlines()[-1])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["legacy", "graph"])
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--case", action="append")
    parser.add_argument("--estimated-cap", type=float, default=2.0)
    args = parser.parse_args()
    folder = ROOT / "runs" / args.run_id
    folder.mkdir(parents=True, exist_ok=True)
    config = deployed()
    assert config["mode"] == args.mode and config["model"] == "deepseek-flash", config
    assert config["fallbacks"] == 0, "Comparison must use only the original model"
    ready = httpx.get("http://127.0.0.1/ready", timeout=30).json()
    assert ready["status"] == "ok"
    plan = []
    for suite, (manifest, selected) in SELECTION.items():
        suite_dir = REPO / "manual_test_files" / suite
        for item in h.read(suite_dir / manifest)["rounds"]:
            if item["case"] in selected and item["round"] == "r1":
                plan.append((suite_dir, item))
    metadata = {"configuration": config, "images": {
        name: subprocess.check_output(["docker", "inspect", "--format", "{{.Image}}", name], text=True).strip()
        for name in ["forma-api-1", "forma-worker-1"]},
        "sources": [{"suite": p.name, **i} for p, i in plan],
        "price_note": "cost_usd uses FORMA indicative pricing, not provider billing; cap is a checkpoint stop between cases, not a dollar guarantee."}
    meta_path = folder / "metadata.json"
    if meta_path.exists():
        assert h.read(meta_path)["configuration"] == metadata, "Deployment or inputs changed: new run ID required"
    else:
        h.save(meta_path, {"configuration": metadata, "started_at": datetime.now(UTC).isoformat()})
    h.evaluate_snapshot = evaluate
    with httpx.Client(base_url="http://127.0.0.1/api", timeout=180) as c:
        for suite, item in plan:
            if args.case and item["case"] not in args.case:
                continue
            spent = 0
            for path in folder.glob("*/before.json"):
                logs = (h.read(path).get("mapping") or {}).get("agent_log", [])
                spent += sum(e.get("cost_usd", (e.get("usage") or {}).get("cost_usd", 0)) or 0 for e in logs)
            if spent >= args.estimated_cap:
                print("STOP indicative cost checkpoint", spent, flush=True)
                break
            h.ROOT = suite
            if item.get("schema_sha256"):
                assert hashlib.sha256((suite / item["schema"]).read_bytes()).hexdigest() == item["schema_sha256"]
            print("START", item["id"], "indicative spent", round(spent, 4), flush=True)
            result = h.run_case(c, item, folder, args.run_id)
            print("DONE", item["id"], json.dumps({k: result.get(k) for k in ["field_accuracy", "questions", "decision", "failed_stage", "elapsed_active_seconds"]}), flush=True)


if __name__ == "__main__":
    main()
