"""Review journey on an owned mock-only stack. Never connects to a paid model or dev data."""

from __future__ import annotations

import argparse
import io
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx
import repro
from openpyxl import Workbook


def run(name: str, prepare_only: bool = False) -> dict:
    path, marker, config = repro.load(name)
    repro.local_daemon()
    repro.owned_resources(marker, config)
    base = f"http://127.0.0.1:{marker['ports'][0]}"
    with httpx.Client(
        base_url=base, timeout=120, trust_env=False, follow_redirects=True
    ) as c:

        def call(method, url, **kwargs):
            r = c.request(method, url, **kwargs)
            r.raise_for_status()
            return r.json()

        def job(jid):
            until = time.monotonic() + 180
            while time.monotonic() < until:
                j = call("GET", f"/api/jobs/{jid}")
                if j["status"] == "done":
                    return j
                assert j["status"] not in ("failed", "cancelled"), j
                time.sleep(1)
            raise AssertionError("Mock job did not finish")

        ai = call("GET", "/api/settings/ai")
        assert ai["mock_only"] and ai["offline"] and ai["active_provider"] == "mock"
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
                "code": "REVIEW-" + uuid.uuid4().hex[:8],
                "name": "ทดสอบหน้าตรวจไฟล์ / ข้อมูลจำลอง",
                "erp_type": "express_th",
                "notes": "Synthetic review fixture. Not production knowledge.",
            },
        )
        r = call(
            "POST",
            "/api/rounds",
            json={"customer_id": customer["id"], "label": "ทดสอบย้อนกลับและเก็บคำตอบ"},
        )
        rid = r["id"]
        call(
            "POST",
            f"/api/rounds/{rid}/files",
            files=[
                (
                    "files",
                    (
                        "review-journey.xlsx",
                        data.getvalue(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    ),
                )
            ],
        )
        job(call("POST", f"/api/rounds/{rid}/run", json={"use_llm": False})["job_id"])
        r = call("GET", f"/api/rounds/{rid}")
        mapping = call("GET", f"/api/mappings/{r['summary']['mapping_version_id']}")
        document = mapping["document"]
        if not any(f["target"] == "/unit/pack_size" for f in document["fields"]):
            document["fields"].append(
                {
                    "target": "/unit/pack_size",
                    "expr": {"op": "const", "value": 12},
                    "confidence": 0.6,
                }
            )
        fields = document["fields"] + [
            f for coll in document["collections"] for f in coll["fields"]
        ]
        vat = next(f["target"] for f in fields if f["target"].endswith("/vat_included"))
        for f in fields:
            f["confidence"] = 1.0
            if f["target"] == vat:
                f["confidence"] = 0.6
                f["expr"] = {"op": "const", "value": True}
            if f["target"] == "/unit/pack_size":
                f["confidence"] = 0.6
        mapping = call(
            "POST", f"/api/mappings/{mapping['id']}/edit", json={"document": document}
        )
        mid = mapping["id"]
        result = {
            "url": base,
            "round_id": rid,
            "mapping_id": mid,
            "review_url": f"{base}/review?round={rid}",
            "mock_only": True,
            "cost_usd": 0,
        }
        if prepare_only:
            (path / "review-ui-fixture.json").write_text(
                json.dumps(result, indent=2), encoding="utf-8"
            )
            return result
        endpoint = f"/api/mappings/{mid}/review"
        payload = {"answers": {vat: False, "/unit/pack_size": 12}, "section": "finish"}
        saved = call(
            "PUT", endpoint + "/draft", json={"revision": 0, "payload": payload}
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(
                pool.map(
                    lambda _: c.put(
                        endpoint + "/draft",
                        json={"revision": saved["revision"], "payload": payload},
                    ),
                    range(2),
                )
            )
        assert sorted(x.status_code for x in responses) == [200, 409]
        assert call("GET", endpoint + "/draft")["payload"]["answers"][vat] is False
        q = call("GET", f"/api/mappings/{mid}/questions")
        body = {"answers": payload["answers"], "acknowledged": q["open_questions"]}
        proof = call("POST", endpoint + "/preview", json=body)
        assert proof["comparison"]["after_records"] == 12
        assert proof["comparison"]["validation"]["passed"]
        confirmed = call(
            "POST", endpoint + "/confirm", json={**body, "digest": proof["digest"]}
        )
        repeated = call(
            "POST", endpoint + "/confirm", json={**body, "digest": proof["digest"]}
        )
        assert confirmed == repeated
        assert confirmed["job_id"], confirmed
        finished = job(confirmed["job_id"])
        assert finished["kind"] == "finish_review"
        r = call("GET", f"/api/rounds/{rid}")
        dsid = r["summary"]["dataset_version_id"]
        ds = call("GET", f"/api/datasets/{dsid}")
        assert ds["row_count"] == 12 and ds["invalid_records"] == 0
        call(
            "POST",
            f"/api/datasets/{dsid}/approve",
            json={"comment": "Synthetic fixture checked by test operator"},
        )
        art = call("POST", f"/api/datasets/{dsid}/export?fmt=json")
        output = call("GET", f"/api/exports/{art['id']}/download")
        assert len(output) == 12
        assert all(
            price["vat_included"] is False for row in output for price in row["prices"]
        )
        result.update(
            dataset_id=dsid,
            rows=12,
            draft_race="200/409",
            repeated_confirm="same job",
            job=confirmed["job_id"],
            downloaded=True,
        )
        (path / "review-journey-result.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="local")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.name, args.prepare_only), ensure_ascii=True, indent=2))
