"""Evidence-question flow on an owned, mock-locked pilot. No dev reads or writes."""

from __future__ import annotations

import argparse
import csv
import io
import json
import time
import uuid

import httpx
import repro


def run(name: str) -> dict:
    path, marker, config = repro.load(name)
    repro.local_daemon()
    repro.owned_resources(marker, config)
    assert marker.get("pilot"), "Requires a separate owned pilot"
    results = []
    with httpx.Client(
        base_url=f"http://127.0.0.1:{marker['ports'][0]}",
        timeout=120,
        trust_env=False,
        follow_redirects=True,
    ) as c:

        def call(method, url, **kwargs):
            response = c.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json()

        def wait(jid):
            until = time.monotonic() + 180
            while time.monotonic() < until:
                job = call("GET", f"/api/jobs/{jid}")
                if job["status"] == "done":
                    return job
                assert job["status"] not in ("failed", "cancelled"), job
                time.sleep(1)
            raise AssertionError("Mock job timeout")

        ai = call("GET", "/api/settings/ai")
        assert ai["mock_only"] and ai["active_provider"] == "mock"
        assert call("GET", "/api/me")["pilot_mode"]
        for mode in ("conflicting", "renamed", "authoritative"):
            renamed = mode == "renamed"
            tag = uuid.uuid4().hex[:10]
            left, right = ("X7", "Y9") if renamed else ("Currency", "Invoice Currency")
            target = "settlement" if renamed else "currency"
            contract = {
                "title": "Synthetic evidence rehearsal",
                "type": "object",
                "required": ["sku", "price"],
                "properties": {
                    "sku": {
                        "type": "string",
                        "x-forma": {"semantic": "sku", "risk": "critical"},
                    },
                    "price": {"type": "number", "x-forma": {"risk": "critical"}},
                    target: {
                        "type": ["string", "null"],
                        "x-forma": {"semantic": "currency", "risk": "critical"},
                    },
                },
                "x-forma": {
                    "schema_id": "synthetic-evidence-" + tag,
                    "identity_keys": ["/sku"],
                    "currency_sources": {f"/{target}": {"columns": [left, right]}},
                },
            }
            if mode == "authoritative":
                contract["x-forma"]["currency_sources"][f"/{target}"][
                    "authoritative"
                ] = right
            schema = call("POST", "/api/schemas", json={"document": contract})
            customer = call(
                "POST",
                "/api/customers",
                json={
                    "code": "EVIDENCE-" + tag,
                    "name": "Synthetic evidence",
                    "notes": "Synthetic, not production knowledge",
                },
            )
            rnd = call(
                "POST",
                "/api/rounds",
                json={
                    "customer_id": customer["id"],
                    "target_schema": schema["ref"],
                    "label": "Renamed sources" if renamed else "Conflicting scopes",
                },
            )
            rid = rnd["id"]
            contents = io.StringIO(newline="")
            writer = csv.writer(contents)
            writer.writerow(
                [right, "Price", "SKU", left]
                if renamed
                else ["SKU", "Price", left, right]
            )
            for i in range(1, 13):
                values = {
                    "SKU": f"A{i:03}",
                    "Price": i * 10,
                    left: "THB",
                    right: "USD" if i % 2 else "EUR",
                }
                writer.writerow(
                    [
                        values[h]
                        for h in (
                            [right, "Price", "SKU", left]
                            if renamed
                            else ["SKU", "Price", left, right]
                        )
                    ]
                )
            call(
                "POST",
                f"/api/rounds/{rid}/files",
                files=[
                    (
                        "files",
                        ("synthetic.csv", contents.getvalue().encode(), "text/csv"),
                    )
                ],
            )
            wait(call("POST", f"/api/rounds/{rid}/run", json={"sync": False})["job_id"])
            mapping = call("GET", f"/api/rounds/{rid}")["mappings"][0]
            mid = mapping["id"]
            questions = call("GET", f"/api/mappings/{mid}/questions")
            matching = [
                q
                for q in questions["questions"]
                if q["field"]["pointer"] == f"/{target}"
            ]
            assert len(matching) == 1 and matching[0]["kind"] == "choice", questions
            question = matching[0]
            assert set(question["options"]) == (
                {right} if mode == "authoritative" else {left, right}
            )
            if mode == "authoritative":
                wrong = c.post(
                    f"/api/mappings/{mid}/answers/preview",
                    json={"answers": {question["id"]: left}},
                )
                assert wrong.status_code == 422
            assert (
                c.post(
                    f"/api/mappings/{mid}/approve",
                    json={"override": True, "note": "Synthetic pre-answer check"},
                ).status_code
                == 422
            )
            payload = {"answers": {question["id"]: right}}
            preview = call("POST", f"/api/mappings/{mid}/answers/preview", json=payload)
            assert len(preview["preview"]["records"]) == 12
            expected = ["USD" if i % 2 else "EUR" for i in range(1, 13)]
            assert [r[target] for r in preview["preview"]["records"]] == expected
            body = payload | {"digest": preview["digest"]}
            saved = call("POST", f"/api/mappings/{mid}/answers/apply", json=body)
            assert (
                c.post(f"/api/mappings/{mid}/answers/apply", json=body).status_code
                == 409
            )
            assert saved["status"] == "proposed"
            new_id = saved["id"]
            pending = call("GET", f"/api/mappings/{new_id}/questions")
            assert not [
                q for q in pending["questions"] if q["field"]["pointer"] == f"/{target}"
            ], pending
            # Saving a choice is not export approval. Finish the existing review workflow.
            review_body = {"answers": {}, "acknowledged": pending["open_questions"]}
            proof = call(
                "POST", f"/api/mappings/{new_id}/review/preview", json=review_body
            )
            confirmed = call(
                "POST",
                f"/api/mappings/{new_id}/review/confirm",
                json=review_body | {"digest": proof["digest"]},
            )
            wait(confirmed["job_id"])
            dsid = call("GET", f"/api/rounds/{rid}")["summary"]["dataset_version_id"]
            assert c.post(f"/api/datasets/{dsid}/export?fmt=json").status_code == 409
            call(
                "POST",
                f"/api/datasets/{dsid}/approve",
                json={"comment": "Synthetic source comparison checked"},
            )
            artifact = call("POST", f"/api/datasets/{dsid}/export?fmt=json")
            output = call("GET", f"/api/exports/{artifact['id']}/download")
            assert len(output) == 12
            assert [r["sku"] for r in output] == [f"A{i:03}" for i in range(1, 13)]
            assert [r[target] for r in output] == expected
            assert [r["price"] for r in output] == [i * 10 for i in range(1, 13)]
            results.append(
                {
                    "renamed": renamed,
                    "mode": mode,
                    "round_id": rid,
                    "mapping_id": new_id,
                    "question": question["question"],
                    "question_count": len(matching),
                    "rows": len(output),
                    "currencies_preserved": True,
                    "repeat_save": 409,
                    "human_export_gate": True,
                    "downloaded": True,
                }
            )
    result = {
        "instance": name,
        "mock_only": True,
        "cost_usd": 0,
        "cases": results,
        "passed": True,
    }
    (path / "source-evidence-result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    print(json.dumps(run(parser.parse_args().name), ensure_ascii=True, indent=2))
