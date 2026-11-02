"""Synthetic-only approved unit-rule replay through the live API; never exports."""
from __future__ import annotations

import csv
import json
import argparse

import httpx

from global_eval import ROOT, read, request, run_case, save
from global_score import score


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default="intl-dl4")
    parser.add_argument("--output", default="decision-memory-live")
    args = parser.parse_args()
    run = args.run_id
    item = next(i for i in read(ROOT / "manifest.json")["rounds"] if i["id"] == "G15_ask_unit_r1")
    source = read(ROOT / "runs" / run / item["id"] / "before.json")
    folder = ROOT / "runs" / args.output
    state = read(folder / "state.json") if (folder / "state.json").exists() else {}
    with (ROOT / item["files"][0]["path"]).open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 12
    assert {r["Tax included"] for r in rows} == {"No"}
    assert {r["Price list"] for r in rows} == {"standard"}
    with httpx.Client(base_url="http://127.0.0.1/api", timeout=180) as c:
        customer = request(c, "GET", "/customers")
        assert any(x["id"] == source["mapping"]["customer_id"] and x["code"] == f"EVAL-{run}-G15_ask_unit" for x in customer)
        if not state.get("mapping_id"):
            clone = request(c, "POST", f"/mappings/{source['mapping']['id']}/edit", json={"document": source["mapping"]["document"]})
            state["mapping_id"] = clone["id"]
            save(folder / "state.json", state)
        if not state.get("answered_id"):
            qs = request(c, "GET", f"/mappings/{state['mapping_id']}/questions")
            answers = {}
            for q in qs["questions"]:
                if q.get("action") == "value_map" and q.get("source_value") == "BT":
                    answers[q["id"]] = "BOTTLE"
                elif q["kind"] == "confirm" and q["field"]["semantic"] in ("price.vat_included", "price.list_name"):
                    answers[q["id"]] = "confirm"
                else:
                    raise RuntimeError("Unexpected decision needs inspection: " + json.dumps(q, ensure_ascii=False))
            body = {"answers": answers}
            preview = request(c, "POST", f"/mappings/{state['mapping_id']}/answers/preview", json=body)
            result = score(preview["preview"]["records"], read(ROOT / item["expected"]), read(ROOT / item["schema"]))
            save(folder / "preview.json", preview)
            assert result["field_accuracy"] == 1 and result["exact_record_accuracy"] == 1
            assert preview["comparison"]["validation"]["passed"]
            answer = request(c, "POST", f"/mappings/{state['mapping_id']}/answers/apply", json={**body, "digest": preview["digest"]})
            assert not answer["open_questions"], answer["open_questions"]
            state["answered_id"] = answer["id"]
            save(folder / "state.json", state)
        approved = request(c, "POST", f"/mappings/{state['answered_id']}/approve", json={"comment": "Synthetic evaluation only: source flags checked; owner answer BT=BOTTLE supplied from fixture after prediction. No export."})
        save(folder / "approved.json", approved)
        warm = {**item, "id": "G15_ask_unit_r2_memory", "round": "r2", "split": "warm"}
        result = run_case(c, warm, ROOT / "runs" / run, run)
        before = read(ROOT / "runs" / run / warm["id"] / "before.json")
        mapping = before["mapping"]
        summary = {
            "run": run, "warm_case": warm["id"],
            "field_accuracy": result["field_accuracy"],
            "exact_record_accuracy": result["exact_record_accuracy"],
            "source": mapping["policy"].get("source"),
            "questions": len(before["questions"]["questions"]),
            "open_questions": before["questions"]["open_questions"],
            "calls": sum(x.get("usage", {}).get("calls", 0) for x in mapping["agent_log"]),
            "approved_mapping_id": state["answered_id"],
        }
        save(folder / "summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        assert summary["source"] == "memory" and summary["calls"] == 0
        assert summary["field_accuracy"] == 1 and summary["questions"] == 0


if __name__ == "__main__":
    main()
