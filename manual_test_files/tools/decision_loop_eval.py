"""Replay frozen model candidates through the real human-answer API. Never approve or export."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx
from global_score import score

ROOT = Path(__file__).resolve().parents[1] / "acommerce_global_v1"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def request(c, method, url, **kwargs):
    r = c.request(method, url, **kwargs)
    r.raise_for_status()
    return r.json()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="intl-20261102-a")
    parser.add_argument("--output", default="decision-loop-replay")
    args = parser.parse_args()
    manifest = read(ROOT / "manifest.json")
    outcomes = []
    with httpx.Client(base_url="http://127.0.0.1/api", timeout=180) as c:
        for case, action, answer in [("G13_ask_date", "date_format", "DMY"), ("G14_ask_currency", "constant", "SGD"), ("G15_ask_unit", "value_map", "BOTTLE"), ("G16_ask_conflict", "select_rows", "0")]:
            source = read(ROOT / "runs" / args.source / (case + "_r1") / "before.json")
            folder = ROOT / "runs" / args.output / case
            state = read(folder / "state.json") if (folder / "state.json").exists() else {}
            if not state.get("mapping_id"):
                clone = request(c, "POST", f"/mappings/{source['mapping']['id']}/edit", json={"document": source["mapping"]["document"]})
                state["mapping_id"] = clone["id"]
                save(folder / "state.json", state)
            mid = state["mapping_id"]
            qs = request(c, "GET", f"/mappings/{mid}/questions")
            if state.get("after_mapping"):
                after = request(c, "GET", f"/mappings/{state['after_mapping']}")
                preview = read(folder / "preview.json")
            else:
                q = next(q for q in qs["questions"] if q.get("action") == action or (
                    case == "G14_ask_currency" and q.get("action") == "value_map"
                    and q["field"]["semantic"] == "currency"
                ))
                payload = {"answers": {q["id"]: answer}}
                preview = request(c, "POST", f"/mappings/{mid}/answers/preview", json=payload)
                save(folder / "questions.json", qs)
                save(folder / "preview.json", preview)
                if not preview["preview"]["ok"] or not preview["comparison"]["validation"]["passed"]:
                    raise RuntimeError(f"{case}: answer impact failed: {preview.get('comparison')}")
                after = request(c, "POST", f"/mappings/{mid}/answers/apply", json={**payload, "digest": preview["digest"]})
                state["after_mapping"] = after["id"]
                save(folder / "state.json", state)
            item = next(i for i in manifest["rounds"] if i["case"] == case and i["round"] == "r1")
            result = score(preview["preview"]["records"], read(ROOT / item["expected"]), read(ROOT / item["schema"]))
            save(folder / "score.json", result)
            save(folder / "after.json", after)
            outcomes.append({"case": case, "field_accuracy": result["field_accuracy"], "rows": len(preview["preview"]["records"]), "validation": preview["comparison"]["validation"], "remaining_questions": after["open_questions"], "policy": after["policy"]["decision"]})
            print(case, result["field_accuracy"], "remaining", len(after["open_questions"]), flush=True)
        save(ROOT / "runs" / args.output / "summary.json", outcomes)


if __name__ == "__main__":
    main()
