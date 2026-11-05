"""Free, repeatable upload -> queued mapping -> review -> JSON download on an OWNED repro only.

Run with backend/.venv Python. Adds one synthetic customer/round each run.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import httpx
import repro
from openpyxl import Workbook


def run(name: str) -> dict:
    path, marker, config = repro.load(name)
    repro.local_daemon()
    repro.owned_resources(marker, config)
    # Confirm both API and worker environment before any workload.
    for service in ("api", "worker"):
        repro.compose(
            path,
            marker,
            "exec",
            "-T",
            service,
            "python",
            "-c",
            "from forma.core.settings import get_settings; from forma.services.context import get_context; "
            "assert get_settings().llm_mock_only; assert get_context().llm.is_mock",
            capture=True,
        )
    url = f"http://127.0.0.1:{marker['ports'][0]}"
    with httpx.Client(
        base_url=url, timeout=60, follow_redirects=True, trust_env=False
    ) as client:

        def call(method: str, endpoint: str, **kwargs):
            result = client.request(method, endpoint, **kwargs)
            result.raise_for_status()
            return result.json()

        def job(job_id: str) -> dict:
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                result = call("GET", f"/api/jobs/{job_id}")
                if result["status"] == "done":
                    return result
                if result["status"] in ("failed", "cancelled"):
                    raise AssertionError(result)
                time.sleep(1)
            raise AssertionError("Mock job did not finish in 180 seconds")

        assert call("GET", "/ready")["status"] == "ok"
        ai = call("GET", "/api/settings/ai")
        assert ai["mock_only"] and ai["offline"] and ai["active_provider"] == "mock"
        assert (
            client.post(
                "/api/settings/ai/connect", json={"api_key": "test-disabled"}
            ).status_code
            == 422
        )
        book = Workbook()
        products = book.active
        products.title = "สินค้า"
        products.append(
            [
                "รหัสสินค้า",
                "ชื่อสินค้า",
                "หมวด",
                "ยี่ห้อ",
                "บาร์โค้ด",
                "หน่วย",
                "ราคาขาย (รวม VAT)",
                "สถานะ",
            ]
        )
        stock = book.create_sheet("Stock")
        stock.append(["รหัสสินค้า", "คลัง", "ยอดคงเหลือ"])
        promo = book.create_sheet("Promotions")
        promo.append(["รหัสสินค้า", "รหัสโปร", "ประเภทโปร", "ส่วนลด %", "เริ่ม", "สิ้นสุด"])
        for i in range(1, 13):
            products.append(
                [
                    f"A{i:03}",
                    f"สินค้า {i}",
                    "อาหาร",
                    "Siam",
                    f"885{i:010}",
                    "ชิ้น",
                    i * 107,
                    "ขาย",
                ]
            )
            stock.append([f"A{i:03}", "BKK", i * 3])
            stock.append([f"A{i:03}", "CNX", i])
        promo.append(["A001", "PR1", "ลด%", "10%", "2026-10-01", "2026-10-31"])
        data = io.BytesIO()
        book.save(data)
        customer = call(
            "POST",
            "/api/customers",
            json={
                "code": "REPRO-" + uuid.uuid4().hex[:8],
                "name": "ข้อมูลจำลอง / isolated smoke test",
                "erp_type": "express_th",
                "notes": "Synthetic test only. Do not promote as production knowledge.",
            },
        )
        rnd = call(
            "POST",
            "/api/rounds",
            json={"customer_id": customer["id"], "label": "repro synthetic smoke"},
        )
        rid = rnd["id"]
        call(
            "POST",
            f"/api/rounds/{rid}/files",
            files=[
                (
                    "files",
                    (
                        "repro.xlsx",
                        data.getvalue(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    ),
                )
            ],
        )
        mapped = job(
            call("POST", f"/api/rounds/{rid}/run", json={"sync": False})["job_id"]
        )
        rnd = call("GET", f"/api/rounds/{rid}")
        assert rnd["files"][0]["tables"] == 3
        mid = rnd["mappings"][-1]["id"]
        mapping = call("GET", f"/api/mappings/{mid}")
        assert mapping["validation"]["ok"]
        # This is explicit test-operator approval; not a claim that mock AI understands arbitrary files.
        call(
            "POST",
            f"/api/mappings/{mid}/approve",
            json={"comment": "Synthetic smoke fixture checked by test operator"},
        )
        transformed = job(
            call("POST", f"/api/rounds/{rid}/transform", params={"sync": "false"})[
                "job_id"
            ]
        )
        dsid = transformed["result"]["dataset_version_id"]
        assert transformed["result"]["rows"] == 12
        operation = {
            "operations": [
                {
                    "kind": "set_cell",
                    "identity": "A001",
                    "target": "/product_name",
                    "value": "Edited in repro",
                }
            ]
        }
        preview = call("POST", f"/api/datasets/{dsid}/preview-changes", json=operation)
        assert not preview["validation_errors"]
        gate = Barrier(2)

        def edit(_number: int):
            gate.wait(timeout=10)
            return client.post(f"/api/datasets/{dsid}/changes", json=operation)

        with ThreadPoolExecutor(2) as pool:
            saved = list(pool.map(edit, range(2)))
        assert sorted(response.status_code for response in saved) == [201, 409], [
            r.text for r in saved
        ]
        dsid = next(
            response.json()["dataset"]["id"]
            for response in saved
            if response.status_code == 201
        )
        history = call("GET", f"/api/datasets/{dsid}")["history"]
        assert [entry["version"] for entry in history] == [1, 2]
        call(
            "POST",
            f"/api/datasets/{dsid}/approve",
            json={"comment": "Synthetic smoke only"},
        )
        exported = call("POST", f"/api/datasets/{dsid}/export", params={"fmt": "jsonl"})
        response = client.get(f"/api/exports/{exported['id']}/download")
        response.raise_for_status()
        rows = [json.loads(line) for line in response.text.splitlines() if line.strip()]
        assert len(rows) == 12 and {r["sku"] for r in rows} == {
            f"A{i:03}" for i in range(1, 13)
        }
        first = next(r for r in rows if r["sku"] == "A001")
        assert first["prices"][0]["amount"] == 107
        assert first["product_name"] == "Edited in repro"
        assert {r["warehouse_code"]: r["qty"] for r in first["inventory"]} == {
            "BKK": 3,
            "CNX": 1,
        }
        assert call("GET", f"/api/rounds/{rid}")["status"] == "published"
        digest = hashlib.sha256(response.content).hexdigest()
        repro.compose(path, marker, "restart", "api", "worker", capture=True)
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            try:
                ready = client.get("/ready")
                if ready.status_code == 200 and ready.json()["status"] == "ok":
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        else:
            raise AssertionError("Repro did not recover after restart")
        restarted = client.get(f"/api/exports/{exported['id']}/download")
        restarted.raise_for_status()
        assert hashlib.sha256(restarted.content).hexdigest() == digest
        recovery_source = (
            Path(__file__).with_name("recovery_check.py").read_text(encoding="utf-8")
        )
        recovery_output = repro.compose(
            path,
            marker,
            "exec",
            "-T",
            "api",
            "python",
            "-c",
            recovery_source,
            dsid,
            exported["id"],
            digest,
            capture=True,
        )
        recovery = json.loads(
            next(
                line.removeprefix("RECOVERY_RESULT=")
                for line in recovery_output.splitlines()
                if line.startswith("RECOVERY_RESULT=")
            )
        )
        result = {
            "url": url,
            "round_id": rid,
            "customer_id": customer["id"],
            "sheets": 3,
            "rows": len(rows),
            "mapping_job": mapped["id"],
            "status": "published",
            "mock_only": True,
            "cost_usd": 0,
            "export_id": exported["id"],
            "dataset_id": dsid,
            "concurrent_edit_statuses": sorted(r.status_code for r in saved),
            "restart_download_sha256": digest,
            "recovery": recovery,
        }
        (path / "smoke-result.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        print(json.dumps(result, indent=2))
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="local")
    name = parser.parse_args().name
    with repro.instance_lock(name):
        run(name)
