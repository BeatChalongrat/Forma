"""Mock provider, isolated database/storage, actual upload/review/export API."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def test_original_erp_workbook_review_to_download(isolated_env, monkeypatch):
    monkeypatch.setenv("FORMA_LLM_MOCK_ONLY", "true")
    monkeypatch.setenv("FORMA_JOB_BACKEND", "thread")
    monkeypatch.setenv("FORMA_AUTH", "dev")
    from forma.api.app import create_app
    from forma.jobs import runner

    deliver = runner.deliver_jobs
    monkeypatch.setattr(runner, "deliver_jobs", lambda ids, **kwargs: deliver(ids, sync=True))

    book = Path(__file__).parent / "fixtures/duplicates/erp.xlsx"
    with TestClient(create_app()) as c:
        assert ok(c.get("/api/settings/ai"))["active_provider"] == "mock"
        cust = ok(c.post("/api/customers", json={"code": "DUP-MOCK", "name": "Synthetic ERP"}), 201)
        rnd = ok(
            c.post("/api/rounds", json={"customer_id": cust["id"], "label": "Source identity regression"}),
            201,
        )
        rid = rnd["id"]
        ok(
            c.post(
                f"/api/rounds/{rid}/files",
                files=[("files", (book.name, book.read_bytes(), "application/octet-stream"))],
            ),
            201,
        )
        job = ok(c.post(f"/api/rounds/{rid}/run", json={"sync": True}))
        assert ok(c.get(f"/api/jobs/{job['job_id']}"))["status"] == "done"
        current = ok(c.get(f"/api/rounds/{rid}"))
        m = ok(c.get(f"/api/mappings/{current['mappings'][0]['id']}"))
        assert current["status"] == "review"
        assert any(
            q.get("action") == "source_identity"
            for q in ok(c.get(f"/api/mappings/{m['id']}/questions"))["questions"]
        )
        assert c.post(f"/api/mappings/{m['id']}/approve", json={"override": True}).status_code == 422

        # The mock/operator supplies additive semantics, without a source grain.
        # It must not silently produce 21 again, even when force-approving.
        doc = deepcopy(m["document"])
        inv = next(cm for cm in doc["collections"] if cm["target"] == "/inventory")
        inv["aggregate"] = {"group_by": ["/warehouse_code"], "sum": ["/qty"]}
        m = ok(c.post(f"/api/mappings/{m['id']}/edit", json={"document": doc}), 201)
        assert not m["policy"]["verification"]["passed"]
        assert c.post(f"/api/mappings/{m['id']}/approve", json={"override": True}).status_code == 422
        questions = ok(c.get(f"/api/mappings/{m['id']}/questions"))["questions"]
        (grain,) = [q for q in questions if q.get("action") == "source_identity"]
        # Values are selected from real source headers, just as the checkbox UI does.
        assert {"Warehouse Code", "Lot Number"} <= set(grain["available_columns"])
        assert any(e["column"] == "Lot Number" and e["values"] for e in grain["source_examples"])
        key_answer = {"answers": {grain["id"]: json.dumps(["Warehouse Code", "Lot Number"])}}
        invalid = c.post(
            f"/api/mappings/{m['id']}/answers/preview", json={"answers": {grain["id"]: '["invented"]'}}
        )
        assert invalid.status_code == 422
        preview = ok(c.post(f"/api/mappings/{m['id']}/answers/preview", json=key_answer))
        m = ok(
            c.post(
                f"/api/mappings/{m['id']}/answers/apply", json={**key_answer, "digest": preview["digest"]}
            ),
            201,
        )
        questions = ok(c.get(f"/api/mappings/{m['id']}/questions"))["questions"]
        assert not any(q.get("action") == "source_identity" for q in questions)
        (price,) = [q for q in questions if q.get("action") == "select_rows"]
        (selected,) = [k for k, label in price["option_labels"].items() if "amount: 125.0" in label]
        payload = {"answers": {price["id"]: selected, "/prices/*/vat_included": True}}
        # Exercise the same gate used by the browser, not the legacy answers/apply route.
        preview = ok(c.post(f"/api/mappings/{m['id']}/review/preview", json=payload))
        assert preview["comparison"]["reliable"], preview
        assert preview["comparison"]["validation"]["passed"], preview
        assert preview["preview"]["ok"], preview
        confirmed = ok(
            c.post(
                f"/api/mappings/{m['id']}/review/confirm",
                json={**payload, "digest": preview["digest"]},
            )
        )
        m = ok(c.get(f"/api/mappings/{confirmed['mapping_id']}"))
        assert m["policy"]["verification"]["passed"]
        assert confirmed["state"] == "processing"
        assert ok(c.get(f"/api/jobs/{confirmed['job_id']}"))["status"] == "done"
        did = ok(c.get(f"/api/rounds/{rid}"))["summary"]["dataset_version_id"]
        ok(c.post(f"/api/datasets/{did}/approve", json={"acknowledge_anomalies": True}))
        export = ok(c.post(f"/api/datasets/{did}/export?fmt=json"), 201)
        records = ok(c.get(f"/api/exports/{export['id']}/download"))
        assert [r["sku"] for r in records] == ["0001", "0002", "0003"]
        stocks = [{i["warehouse_code"]: i["qty"] for i in r["inventory"]} for r in records]
        assert stocks == [{"BKK": 14, "CNX": 3}, {"BKK": 4}, {"CNX": 0}]
        prices = {(p["price_list"], p["effective_from"]): p["amount"] for p in records[0]["prices"]}
        assert prices == {
            ("standard", "2026-10-01"): 125,
            ("wholesale", "2026-10-01"): 80,
            ("standard", "2026-11-01"): 110,
        }
        assert len(records[1]["prices"]) == 1


@pytest.mark.parametrize("case", ["conflict", "missing_lot", "separate_bins"])
def test_new_csv_cases_remain_blocked_until_resolved(isolated_env, monkeypatch, case):
    monkeypatch.setenv("FORMA_LLM_MOCK_ONLY", "true")
    monkeypatch.setenv("FORMA_JOB_BACKEND", "thread")
    monkeypatch.setenv("FORMA_AUTH", "dev")
    from forma.api.app import create_app
    from forma.jobs import runner

    deliver = runner.deliver_jobs
    monkeypatch.setattr(runner, "deliver_jobs", lambda ids, **kwargs: deliver(ids, sync=True))

    stock = {
        "conflict": "001,A,L1,7,X\n001,A,L1,7,X\n001,A,L1,9,X\n001,A,L2,2,X\n",
        "missing_lot": "001,A,,7,X\n001,A,L2,2,X\n",
        "separate_bins": "001,A,L1,7,X\n001,A,L1,7,Y\n",
    }[case]
    files = [
        ("files", ("master.csv", b"SKU,Product Name,Currency\n001,Tea,THB\n", "text/csv")),
        (
            "files",
            ("stock.csv", ("SKU,Warehouse Code,Lot Number,Stock Qty,Bin\n" + stock).encode(), "text/csv"),
        ),
    ]
    with TestClient(create_app()) as c:
        cust = ok(c.post("/api/customers", json={"code": case, "name": "Synthetic ERP"}), 201)
        rid = ok(c.post("/api/rounds", json={"customer_id": cust["id"]}), 201)["id"]
        ok(c.post(f"/api/rounds/{rid}/files", files=files), 201)
        ok(c.post(f"/api/rounds/{rid}/run", json={"sync": True}))
        mid = ok(c.get(f"/api/rounds/{rid}"))["mappings"][0]["id"]
        doc = {
            "meta": {"target_schema": "product@1"},
            "sources": [
                {"id": "master", "file": {"name_pattern": "^master.csv$"}, "table": {"header_row": 0}},
                {"id": "stock", "file": {"name_pattern": "^stock.csv$"}, "table": {"header_row": 0}},
            ],
            "record": {"root_source": "master"},
            "attributes_passthrough": False,
            "fields": [
                {"target": "/sku", "expr": {"op": "col", "name": "SKU"}, "confidence": 1},
                {"target": "/product_name", "expr": {"op": "col", "name": "Product Name"}, "confidence": 1},
                {"target": "/currency", "expr": {"op": "col", "name": "Currency"}, "confidence": 1},
            ],
            "collections": [
                {
                    "target": "/inventory",
                    "source": "stock",
                    "link": [{"left": "SKU", "right": "SKU"}],
                    "fields": [
                        {
                            "target": "/inventory/*/warehouse_code",
                            "expr": {"op": "col", "name": "Warehouse Code"},
                            "confidence": 1,
                        },
                        {
                            "target": "/inventory/*/qty",
                            "expr": {"op": "col", "name": "Stock Qty"},
                            "confidence": 1,
                        },
                    ],
                    "aggregate": {"group_by": ["/warehouse_code"], "sum": ["/qty"]},
                    "source_identity": {
                        "keys": ["Warehouse Code", "Lot Number"],
                        "evidence": ["Operator supplied balance grain; verify against all source rows"],
                    },
                }
            ],
        }
        if case in {"conflict", "missing_lot"}:
            # Replay the live fallback's loss of source keys and aggregation.
            fallback = deepcopy(doc)
            fallback["collections"][0].pop("source_identity")
            fallback["collections"][0].pop("aggregate")
            stale = ok(c.post(f"/api/mappings/{mid}/edit", json={"document": fallback}), 201)
            mid = stale["id"]
            catalogue = ok(c.get(f"/api/mappings/{mid}/questions"))["questions"]
            assert any(q["action"] == "source_identity" for q in catalogue)
            assert not any(q["action"] == "select_rows" for q in catalogue)
            assert c.post(f"/api/mappings/{mid}/approve", json={"override": True}).status_code == 422

            # Filtering at the source-table layer must not bypass the same check.
            filtered = deepcopy(doc)
            filtered["sources"][1]["table"]["filter"] = {
                "op": "eq",
                "args": [{"op": "col", "name": "Lot Number"}, {"op": "const", "value": "L2"}],
            }
            bad = ok(c.post(f"/api/mappings/{mid}/edit", json={"document": filtered}), 201)
            mid = bad["id"]
            assert not bad["policy"]["verification"]["passed"]
            assert c.post(f"/api/mappings/{mid}/approve", json={"override": True}).status_code == 422

        m = ok(c.post(f"/api/mappings/{mid}/edit", json={"document": doc}), 201)
        assert not m["policy"]["verification"]["passed"]
        assert c.post(f"/api/mappings/{m['id']}/approve", json={"override": True}).status_code == 422
        (q,) = [
            q
            for q in ok(c.get(f"/api/mappings/{m['id']}/questions"))["questions"]
            if q["id"].startswith("source-identity:")
        ]
        if case == "missing_lot":
            assert q["action"] == "request_source"
            assert q["source_key_columns"] == ["Lot Number"]
            assert "Lot Number" in q["question"]
            assert any(e["column"] == "Lot Number" for e in q["source_examples"])
            params = {"field": q["field"]["pointer"], "question": q["id"]}
            evidence = ok(c.get(f"/api/mappings/{m['id']}/questions/evidence", params=params))
            assert evidence["source_issue"] and not evidence["unavailable"]
            (example,) = evidence["examples"]
            (cell,) = example["cells"]
            assert cell["header"] == "Lot Number" and cell["raw"] in (None, "")
            assert (cell["file"], cell["row"], cell["column"]) == ("stock.csv", 2, "C")
            assert (
                c.get(
                    f"/api/mappings/{m['id']}/questions/evidence", params={**params, "question": "invented"}
                ).status_code
                == 422
            )
            proof = ok(c.post(f"/api/mappings/{m['id']}/review/preview", json={}))
            assert not proof["preview"]["ok"]
            assert (
                c.post(
                    f"/api/mappings/{m['id']}/review/confirm", json={"digest": proof["digest"]}
                ).status_code
                == 422
            )
            from forma.db.models import MappingVersion
            from forma.db.session import session_scope

            prompt = "Please fill the missing Lot Number before calculating stock"
            with session_scope() as session:
                saved = session.get(MappingVersion, m["id"])
                saved.policy = {
                    **saved.policy,
                    "clarifications": [
                        {
                            "target": "/inventory/*/qty",
                            "action": "request_source",
                            "kind": "confirm",
                            "question": prompt,
                            "why": "Lot Number is blank in the source",
                        }
                    ],
                }
                saved.open_questions = [*(saved.open_questions or []), prompt]
            refreshed = ok(c.get(f"/api/mappings/{m['id']}/questions"))["questions"]
            assert len([item for item in refreshed if item.get("action") == "request_source"]) == 1
            assert prompt in next(item for item in refreshed if item["id"] == q["id"])["related_questions"]
            assert (
                c.post(
                    f"/api/mappings/{m['id']}/answers/preview", json={"answers": {q["id"]: "confirm"}}
                ).status_code
                == 422
            )
            return
        if case == "conflict":
            assert q["action"] == "select_rows"
            (value,) = [k for k, label in q["option_labels"].items() if "Stock Qty: 9" in label]
            expected = 11
        else:
            assert q["action"] == "source_identity"
            value = json.dumps(["Warehouse Code", "Lot Number", "Bin"])
            expected = 14
        payload = {"answers": {q["id"]: value}}
        # Exercise the same gate used by the browser, not the legacy answers/apply route.
        preview = ok(c.post(f"/api/mappings/{m['id']}/review/preview", json=payload))
        assert preview["comparison"]["reliable"], preview
        assert preview["comparison"]["validation"]["passed"], preview
        assert preview["preview"]["ok"], preview
        confirmed = ok(
            c.post(
                f"/api/mappings/{m['id']}/review/confirm",
                json={**payload, "digest": preview["digest"]},
            )
        )
        m = ok(c.get(f"/api/mappings/{confirmed['mapping_id']}"))
        assert m["policy"]["verification"]["passed"]
        assert not any(
            q["id"].startswith("source-identity:")
            for q in ok(c.get(f"/api/mappings/{m['id']}/questions"))["questions"]
        )
        result = ok(c.post(f"/api/rounds/{rid}/mapping-preview", json={"document": m["document"]}))
        assert result["records"][0]["inventory"][0]["qty"] == expected
