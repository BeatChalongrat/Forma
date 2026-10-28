"""REST API flow on an isolated SQLite database + data directory (jobs run synchronously)."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import openpyxl
import pytest
from fastapi.testclient import TestClient

FILE_NAME = "สินค้า_ตค69_C001.xlsx"


def make_workbook(path: Path, *, rename_qty: bool = False, n_products: int = 12) -> Path:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)  # type: ignore[arg-type]
    ws = wb.create_sheet("สินค้า")
    ws.append(["รายงานสินค้า ประจำเดือน ต.ค. 2569"])
    ws.append([None])
    ws.append(["รหัสสินค้า", "ชื่อสินค้า", "หมวด", "ยี่ห้อ", "บาร์โค้ด", "หน่วย", "ราคาขาย (รวม VAT)", "สถานะ"])
    cats = ["อาหาร", "เครื่องดื่ม", "ของใช้"]
    for i in range(1, n_products + 1):
        ws.append(
            [
                f"A{i:03d}",
                f"สินค้า {i}",
                cats[i % 3],
                "Siam",
                f"885{i:010d}",
                "ชิ้น" if i % 2 else "ลัง(12)",
                f"{i * 107:,}.00",
                "ขาย" if i % 5 else "เลิกขาย",
            ]
        )
    ws.append(["รวม", None, None, None, None, None, "9,999.00", None])
    st = wb.create_sheet("Stock")
    st.append(["รหัสสินค้า", "คลัง", "จำนวนคงเหลือ" if rename_qty else "ยอดคงเหลือ"])
    for i in range(1, n_products + 1):
        st.append([f"A{i:03d}", "BKK", i * 3])
        st.append([f"A{i:03d}", "CNX", i])
    pr = wb.create_sheet("Promotions")
    pr.append(["รหัสสินค้า", "รหัสโปร", "ประเภทโปร", "ส่วนลด %", "เริ่ม", "สิ้นสุด"])
    pr.append(["A001", "PR1", "ลด%", "10%", "1/10/2569", "31 ต.ค. 69"])
    pr.append(["A002", "PR2", "ลด%", "5", "5/10/2569", "15/10/69"])
    pr.append(["A003", "PR3", "ลดบาท", None, "5/10/2569", "15/10/69"])
    wb.save(path)
    return path


@pytest.fixture
def client(isolated_env: Path) -> Iterator[TestClient]:
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def _upload(c: TestClient, round_id: str, path: Path, name: str = FILE_NAME) -> list[dict[str, Any]]:
    r = c.post(
        f"/api/rounds/{round_id}/files",
        files=[("files", (name, path.read_bytes(), "application/octet-stream"))],
    )
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


def _run(c: TestClient, round_id: str) -> dict[str, Any]:
    job_id = c.post(f"/api/rounds/{round_id}/run", json={"sync": True}).json()["job_id"]
    job = c.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "done", job["error"]
    return job  # type: ignore[no-any-return]


def test_full_flow(client: TestClient, isolated_env: Path) -> None:
    c = client
    xlsx = make_workbook(isolated_env / FILE_NAME)

    assert c.get("/health").json()["status"] == "ok"

    # customer + round -------------------------------------------------------------------------
    cust = c.post(
        "/api/customers", json={"code": "C001", "name": "ร้านทดสอบ", "erp_type": "express_th"}
    ).json()
    assert cust["code"] == "C001" and cust["id"].startswith("cus_")
    assert c.post("/api/customers", json={"code": "C001", "name": "dup"}).status_code == 422
    assert c.get("/api/customers/cus_nope").status_code == 404
    rnd = c.post("/api/rounds", json={"customer_id": cust["id"], "label": "r1"}).json()
    assert rnd["status"] == "uploaded"
    assert c.post("/api/rounds", json={"customer_id": "cus_nope"}).status_code == 404

    # upload + one-click pipeline ------------------------------------------------------------
    files = _upload(c, rnd["id"], xlsx)
    assert files[0]["kind"] == "excel" and files[0]["duplicate_of"] is None
    assert (
        c.post(
            f"/api/rounds/{rnd['id']}/files",
            files=[("files", ("empty.xlsx", b"", "application/octet-stream"))],
        ).status_code
        == 422
    )
    job = _run(c, rnd["id"])
    assert job["result"]["mapping_status"] == "proposed"  # new customer → review

    round_ = c.get(f"/api/rounds/{rnd['id']}").json()
    assert round_["status"] == "review"
    assert round_["summary"]["decision"] == "review" and round_["summary"]["has_previous_approval"] is False
    assert round_["files"][0]["status"] == "profiled" and round_["files"][0]["tables"] == 3
    assert set(round_["files"][0]["sheets"]) == {"สินค้า", "Stock", "Promotions"}
    assert len(round_["mappings"]) == 1 and len(round_["review_tasks"]) == 1 and round_["jobs"]
    f = c.get(f"/api/rounds/{rnd['id']}/files/{files[0]['id']}").json()
    assert f["profile"]["tables"] and f["fingerprint_full"]["strict"]
    assert c.get(f"/api/rounds/{rnd['id']}/files/fil_nope").status_code == 404
    grid = c.get(
        f"/api/rounds/{rnd['id']}/files/{files[0]['id']}/grid", params={"sheet": 0, "rows": 5}
    ).json()
    assert grid["n_cols"] == 8 and len(grid["rows"]) == 5 and grid["rows"][2][0] == "รหัสสินค้า"

    # mapping ---------------------------------------------------------------------------------------
    mid = round_["mappings"][0]["id"]
    mapping = c.get(f"/api/mappings/{mid}").json()
    assert mapping["status"] == "proposed" and mapping["validation"]["ok"] is True
    assert mapping["policy"]["decision"] == "review" and "first time" in " ".join(
        mapping["policy"]["reasons"]
    )
    assert mapping["policy"]["simulation"]["rows_out"] == 12
    assert {fm["target"] for fm in mapping["document"]["fields"]} >= {
        "/sku",
        "/product_name",
        "/category",
        "/unit/sell_unit",
        "/status",
        "/currency",
    }
    assert {cm["target"] for cm in mapping["document"]["collections"]} == {
        "/prices",
        "/inventory",
        "/promotions",
    }
    assert len(mapping["sample"]) == 5 and mapping["sample"][0]["sku"] == "A001"
    assert c.get("/api/mappings/map_nope").status_code == 404

    preview = c.post(
        f"/api/rounds/{rnd['id']}/mapping-preview", json={"document": mapping["document"], "limit": 3}
    ).json()
    assert preview["ok"] is True and len(preview["records"]) == 3
    assert (
        preview["records"][0]["prices"][0]["amount"] == 107.0
        and preview["records"][0]["prices"][0]["vat_included"] is True
    )
    assert preview["records"][0]["promotions"][0]["start_date"] == "2026-10-01"
    bad = dict(mapping["document"])
    bad["fields"] = [fm for fm in bad["fields"] if fm["target"] != "/sku"]
    pbad = c.post(f"/api/rounds/{rnd['id']}/mapping-preview", json={"document": bad}).json()
    assert pbad["ok"] is False and any(i["code"] == "missing_required" for i in pbad["validation"]["issues"])
    assert (
        c.post(f"/api/rounds/{rnd['id']}/mapping-preview", json={"document": {"nonsense": 1}}).json()["ok"]
        is False
    )

    src = c.post(
        f"/api/rounds/{rnd['id']}/source-preview",
        json={"source": mapping["document"]["sources"][0], "limit": 4},
    ).json()
    assert src["file"] == FILE_NAME and src["n_rows_preview"] == 4 and "รหัสสินค้า" in src["columns"]

    # human edit → new proposed version, then approve the edited one -------------------------------
    edited = dict(mapping["document"])
    edited["quality"] = [
        *edited["quality"],
        {"target": "/barcode", "rule": "not_null", "severity": "warning"},
    ]
    ev = c.post(f"/api/mappings/{mid}/edit", json={"document": edited, "actor": "qa"})
    assert ev.status_code == 201
    edited_mv = ev.json()
    assert edited_mv["version"] == 2 and edited_mv["created_by"] == "human" and edited_mv["parent_id"] == mid
    assert c.get(f"/api/mappings/{mid}").json()["status"] == "superseded"
    assert c.post(f"/api/mappings/{mid}/edit", json={"document": {"meta": {}}}).status_code == 422
    assert c.post(f"/api/mappings/{mid}/edit", json={"document": bad}).status_code == 422

    queue = c.get("/api/review").json()
    assert (
        len(queue) == 1
        and queue[0]["mapping_version_id"] == edited_mv["id"]
        and queue[0]["customer"]["code"] == "C001"
    )

    approved = c.post(
        f"/api/mappings/{edited_mv['id']}/approve", json={"actor": "operator", "comment": "ok"}
    ).json()
    assert approved["status"] == "approved" and approved["approved_by"] == "operator"
    assert c.get("/api/review").json() == []
    assert c.get(f"/api/rounds/{rnd['id']}").json()["status"] == "approved"
    assert c.post(f"/api/mappings/{mid}/approve", json={}).status_code == 409  # superseded

    # transform ------------------------------------------------------------------------------------
    jid = c.post(f"/api/rounds/{rnd['id']}/transform", params={"sync": "true"}).json()["job_id"]
    tjob = c.get(f"/api/jobs/{jid}").json()
    assert tjob["status"] == "done" and tjob["result"]["rows"] == 12
    dsid = tjob["result"]["dataset_version_id"]
    ds = c.get(f"/api/datasets/{dsid}").json()
    assert ds["version"] == 1 and ds["row_count"] == 12 and ds["status"] == "draft"
    assert ds["history"][0]["id"] == dsid
    assert c.get("/api/datasets/dsv_nope").status_code == 404

    rows = c.get(f"/api/datasets/{dsid}/rows", params={"limit": 5, "sort": "/sku", "desc": "true"}).json()
    assert rows["total"] == 12 and len(rows["rows"]) == 5 and rows["rows"][0]["sku"] == "A012"
    assert rows["identity_keys"] == ["/sku"] and any(col["pointer"] == "/sku" for col in rows["columns"])
    search = c.get(f"/api/datasets/{dsid}/rows", params={"q": "สินค้า 7"}).json()
    assert search["total"] == 1 and search["rows"][0]["sku"] == "A007"
    a001 = next(
        r
        for r in c.get(f"/api/datasets/{dsid}/rows", params={"limit": 100}).json()["rows"]
        if r["sku"] == "A001"
    )
    assert a001["source_meta"]["dataset_version_id"] == dsid

    # edits: preview, apply (set_cell + scale_field with where), rollback --------------------------
    where = {
        "op": "contains",
        "args": [{"op": "col", "name": "/category"}],
        "params": {"pattern": "อาหาร", "literal": True},
    }
    ops = [
        {"kind": "set_cell", "identity": "A001", "target": "/product_name", "value": "น้ำดื่มพิเศษ"},
        {"kind": "scale_field", "target": "/prices/*/amount", "factor": 0.9, "where": where},
    ]
    prev = c.post(f"/api/datasets/{dsid}/preview-changes", json={"operations": ops}).json()
    assert prev["validation_errors"] == [] and prev["rows_after"] == 12
    assert prev["impact"]["rows_affected"] == 5 and prev["impact"]["cells_changed"] == 5
    assert set(prev["impact"]["fields"]) == {"/product_name", "/prices/0/amount"}

    applied = c.post(
        f"/api/datasets/{dsid}/changes",
        json={"operations": ops, "kind": "persistent", "actor": "qa", "note": "promo"},
    )
    assert applied.status_code == 201
    v2 = applied.json()["dataset"]
    cs = applied.json()["change_set"]
    assert (
        v2["version"] == 2
        and v2["parent_id"] == dsid
        and cs["kind"] == "persistent"
        and cs["impact"]["rows_affected"] == 5
    )
    v2_rows = {
        r["sku"]: r for r in c.get(f"/api/datasets/{v2['id']}/rows", params={"limit": 100}).json()["rows"]
    }
    assert v2_rows["A001"]["product_name"] == "น้ำดื่มพิเศษ"
    assert v2_rows["A003"]["prices"][0]["amount"] == pytest.approx(
        3 * 107 * 0.9
    )  # category อาหาร (i % 3 == 0)
    assert v2_rows["A004"]["prices"][0]["amount"] == 4 * 107.0  # untouched
    assert c.get(f"/api/datasets/{dsid}").json()["status"] == "superseded"
    assert len(c.get(f"/api/datasets/{dsid}").json()["change_sets"]) == 1
    cust_view = c.get(f"/api/customers/{cust['id']}").json()
    assert len(cust_view["overrides"]) == 1 and cust_view["overrides"][0]["target"] == "/product_name"
    assert len(cust_view["rounds"]) == 1 and len(cust_view["mappings"]) == 2

    invalid = c.post(
        f"/api/datasets/{v2['id']}/changes",
        json={"operations": [{"kind": "set_field", "target": "/status", "value": "weird"}]},
    )
    assert invalid.status_code == 422
    assert (
        c.post(f"/api/datasets/{v2['id']}/changes", json={"operations": [{"kind": "teleport"}]}).status_code
        == 422
    )

    rb = c.post(f"/api/datasets/{v2['id']}/rollback", json={"actor": "qa"})
    assert rb.status_code == 201
    v3 = rb.json()
    assert v3["version"] == 3 and v3["note"] == "rollback to v1"
    v3_rows = {
        r["sku"]: r for r in c.get(f"/api/datasets/{v3['id']}/rows", params={"limit": 100}).json()["rows"]
    }
    assert v3_rows["A001"]["product_name"] == "สินค้า 1" and v3_rows["A003"]["prices"][0]["amount"] == 3 * 107.0
    assert c.post(f"/api/datasets/{dsid}/rollback", json={}).status_code == 409  # v1 has no parent
    assert c.get(f"/api/datasets/{dsid}/changes-report").json()["has_previous"] is False

    # approve + export + download ---------------------------------------------------------------------
    assert c.post(f"/api/datasets/{v3['id']}/approve", json={"actor": "qa"}).json()["status"] == "approved"
    exp = c.post(f"/api/datasets/{v3['id']}/export", params={"fmt": "csv"})
    assert exp.status_code == 201
    art = exp.json()
    assert art["fmt"] == "csv" and art["row_count"] == 12 and len(art["sha256"]) == 64
    assert c.post(f"/api/datasets/{v3['id']}/export", params={"fmt": "xml"}).status_code == 422
    dl = c.get(f"/api/exports/{art['id']}/download")
    assert dl.status_code == 200 and dl.headers["content-type"].startswith("text/csv")
    assert dl.content.startswith("﻿".encode())
    table = list(csv.DictReader(io.StringIO(dl.content.decode("utf-8-sig"))))
    assert len(table) == 12 and table[0]["sku"] == "A001" and table[0]["prices.amount"] == "107.0"
    assert c.get("/api/exports/exp_nope/download").status_code == 404
    assert c.get(f"/api/datasets/{v3['id']}").json()["status"] == "published"
    assert c.get(f"/api/rounds/{rnd['id']}").json()["status"] == "published"
    jl = c.post(f"/api/datasets/{v3['id']}/export", params={"fmt": "jsonl"}).json()
    assert c.get(f"/api/exports/{jl['id']}/download").content.count(b"\n") == 12

    # lineage ------------------------------------------------------------------------------------------
    lin = c.get(f"/api/datasets/{v2['id']}/lineage/A001").json()
    assert lin["record"]["sku"] == "A001" and lin["mapping_version"] == edited_mv["id"]
    assert any(fld["target"] == "/sku" for fld in lin["fields"]) and any(
        col["target"] == "/prices" for col in lin["collections"]
    )
    assert lin["edits"] and lin["edits"][0]["change_set"] == cs["id"]
    assert lin["edits"][0]["changes"][0]["after"] == "น้ำดื่มพิเศษ"
    assert c.get(f"/api/datasets/{v2['id']}/lineage/NOPE").status_code == 404

    # AI endpoints with the mock LLM -----------------------------------------------------------------
    diag = c.post(f"/api/mappings/{edited_mv['id']}/diagnose").json()
    assert diag["ai"] is False and "LLM" in diag["summary_th"] and isinstance(diag["root_causes"], list)
    cp = c.post(f"/api/datasets/{v3['id']}/copilot", json={"instruction": "ลดราคาหมวด อาหาร 5%"}).json()
    assert cp["ai"] is False and cp["needs_clarification"] is None
    assert cp["operations"][0]["kind"] == "scale_field" and cp["operations"][0]["factor"] == 0.95
    assert cp["operations"][0]["target"] == "/prices/*/amount"
    assert cp["impact"]["rows_affected"] == 4 and cp["impact"]["cells_changed"] == 4
    cp2 = c.post(
        f"/api/datasets/{v3['id']}/copilot", json={"instruction": "ตั้งสถานะ inactive ให้หมวด ของใช้"}
    ).json()
    assert cp2["operations"][0] == {
        "kind": "set_field",
        "target": "/status",
        "value": "inactive",
        "where": {
            "op": "contains",
            "args": [{"op": "col", "name": "/category"}],
            "params": {"pattern": "ของใช้", "literal": True},
        },
        "explanation": "ตั้งสถานะ inactive ให้หมวด ของใช้",
    }
    unclear = c.post(f"/api/datasets/{v3['id']}/copilot", json={"instruction": "ทำอะไรสักอย่าง"}).json()
    assert unclear["operations"] == [] and unclear["needs_clarification"]

    # read models -----------------------------------------------------------------------------------------
    dash = c.get("/api/dashboard").json()
    assert dash["customers"] == 1 and dash["approved_mappings"] == 1 and dash["published_rows"] == 12
    assert dash["llm"]["provider"] == "mock" and dash["rounds_by_status"] == {"published": 1}
    assert dash["recent_rounds"][0]["customer"]["code"] == "C001"
    assert c.get("/api/review", params={"status": "approved"}).json()[0]["resolver"] == "operator"
    audit = c.get("/api/audit").json()
    assert {a["action"] for a in audit} >= {
        "customer.create",
        "round.create",
        "file.add",
        "mapping.propose",
        "mapping.approve",
        "dataset.transform",
        "dataset.edit",
        "dataset.export",
    }
    assert all(a["entity_id"] == dsid for a in c.get("/api/audit", params={"entity_id": dsid}).json())
    schemas = c.get("/api/schemas").json()
    assert schemas[0]["ref"] == "product@1" and schemas[0]["identity_keys"] == ["/sku"]
    detail = c.get("/api/schemas/product").json()
    assert (
        any(fld["pointer"] == "/prices/*/amount" for fld in detail["fields"])
        and detail["document"]["title"] == "Product"
    )
    assert c.get("/api/schemas/nope").status_code == 404
    diff = c.get("/api/schemas/product@1/diff/product@1").json()
    assert diff["added"] == [] and diff["identity_keys_changed"] is False
    dsl = c.get("/api/dsl").json()
    assert "parse_date_th" in dsl["ops"] and dsl["json_schema"]["title"] == "MappingDocument"
    assert c.get("/api/policy").json()["auto_approve"]["min_mapping_confidence"] == 0.95
    customers = c.get("/api/customers").json()
    assert (
        customers[0]["rounds"] == 1
        and customers[0]["has_approved_mapping"] is True
        and customers[0]["last_round_at"]
    )
    assert (
        c.get("/api/rounds", params={"status": "published", "customer_id": cust["id"]}).json()[0]["id"]
        == rnd["id"]
    )
    assert c.get("/api/jobs/job_nope").status_code == 404
    assert c.delete(f"/api/customers/{cust['id']}/overrides/{cust_view['overrides'][0]['id']}").json() == {
        "ok": True
    }
    assert c.delete(f"/api/customers/{cust['id']}/overrides/ovr_nope").status_code == 404

    # second round, same file → mapping memory reuse → auto-approve → dataset -------------------------
    rnd2 = c.post("/api/rounds", json={"customer_id": cust["id"], "label": "r2"}).json()
    files2 = _upload(c, rnd2["id"], xlsx)
    assert files2[0]["duplicate_of"] == files[0]["id"]
    job2 = _run(c, rnd2["id"])
    assert job2["result"]["mapping_status"] == "approved" and job2["result"]["rows"] == 12
    r2 = c.get(f"/api/rounds/{rnd2['id']}").json()
    assert r2["summary"]["drift"] == "identical" and r2["summary"]["has_previous_approval"] is True
    assert r2["summary"]["decision"] == "auto_approve"
    mv2 = c.get(f"/api/mappings/{r2['mappings'][0]['id']}").json()
    assert (
        mv2["status"] == "approved" and mv2["approved_by"] == "policy" and mv2["policy"]["source"] == "memory"
    )
    assert mv2["confidence"] == 1.0 and mv2["version"] == 3 and mv2["agent_log"][0]["agent"] == "memory"
    assert c.get(f"/api/mappings/{edited_mv['id']}").json()["status"] == "superseded"
    assert len(r2["datasets"]) == 1 and r2["datasets"][0]["row_count"] == 12 and r2["status"] == "transformed"
    report = c.get(f"/api/datasets/{r2['datasets'][0]['id']}/changes-report").json()
    assert report["has_previous"] is True and report["previous_dataset_id"] == v3["id"]
    assert report["added_count"] == 0 and report["removed_count"] == 0 and report["changed_count"] == 0
    dash2 = c.get("/api/dashboard").json()
    assert (
        dash2["auto_approved_mappings"] == 1
        and dash2["reused_mappings"] == 1
        and dash2["automation_rate"] == 0.333
    )

    # third round: renamed column → drift → review (no silent reuse) -----------------------------------
    renamed = make_workbook(isolated_env / "renamed.xlsx", rename_qty=True)
    rnd3 = c.post("/api/rounds", json={"customer_id": cust["id"], "label": "r3"}).json()
    _upload(c, rnd3["id"], renamed, name="สินค้า_พย69_C001.xlsx")
    job3 = _run(c, rnd3["id"])
    assert job3["result"]["mapping_status"] == "proposed"
    r3 = c.get(f"/api/rounds/{rnd3['id']}").json()
    assert r3["summary"]["drift"] in ("columns_renamed", "columns_removed")
    assert r3["status"] == "review" and r3["review_tasks"][0]["kind"] == "drift"


def test_file_matching_another_customer_goes_to_review(client: TestClient, isolated_env: Path) -> None:
    c = client
    xlsx = make_workbook(isolated_env / FILE_NAME)
    a = c.post("/api/customers", json={"code": "A", "name": "A"}).json()
    b = c.post("/api/customers", json={"code": "B", "name": "B"}).json()
    ra = c.post("/api/rounds", json={"customer_id": a["id"]}).json()
    _upload(c, ra["id"], xlsx)
    _run(c, ra["id"])
    mid = c.get(f"/api/rounds/{ra['id']}").json()["mappings"][0]["id"]
    c.post(f"/api/mappings/{mid}/approve", json={})
    # customer B uploads a file whose structure is identical to A's approved format → named in review
    rb = c.post("/api/rounds", json={"customer_id": b["id"]}).json()
    _upload(c, rb["id"], xlsx)
    job = _run(c, rb["id"])
    assert job["result"]["mapping_status"] == "proposed"
    r = c.get(f"/api/rounds/{rb['id']}").json()
    assert r["status"] == "review" and r["summary"]["decision"] == "review"
    assert r["summary"]["matches_other_customer"]["customer_code"] == "A"
    assert r["files"][0]["drift"]["matches_other_customer"]["customer_code"] == "A"
    mv = c.get(f"/api/mappings/{r['mappings'][0]['id']}").json()
    assert any("customer A" in reason for reason in mv["policy"]["reasons"])
    task = c.get("/api/review").json()[0]
    assert task["kind"] == "mapping" and task["priority"] >= 50
    rej = c.post(f"/api/mappings/{r['mappings'][0]['id']}/reject", json={"comment": "wrong file"}).json()
    assert rej["status"] == "rejected"
    assert c.get(f"/api/rounds/{rb['id']}").json()["status"] == "rejected"
    # transform without an approved mapping is refused
    assert c.post(f"/api/rounds/{rb['id']}/transform", params={"sync": "true"}).status_code == 200
    failed = c.get(f"/api/rounds/{rb['id']}").json()
    assert failed["status"] == "failed" and "approve a mapping" in failed["summary"]["error"]
    assert c.get("/api/dashboard").json()["failed_jobs"] == 1


class _FakeProvider:
    """A non-mock provider so the API takes its real-LLM branches without any network."""

    name = "fake"

    def __init__(self) -> None:
        self.answers: list[dict[str, Any]] = []

    def complete_json(
        self, *, system: str, user: str, schema: dict[str, Any], model: str, temperature: float
    ) -> tuple[str, Any]:
        import json

        from forma.ai.llm import Usage

        return json.dumps(self.answers.pop(0), ensure_ascii=False), Usage(100, 20, 1, 2.0, model)


def test_ai_endpoints_with_llm(client: TestClient, isolated_env: Path) -> None:
    from forma.ai.llm import LLMClient
    from forma.services.context import get_context

    c = client
    xlsx = make_workbook(isolated_env / FILE_NAME)
    cust = c.post("/api/customers", json={"code": "C9", "name": "AI"}).json()
    rnd = c.post("/api/rounds", json={"customer_id": cust["id"]}).json()
    _upload(c, rnd["id"], xlsx)
    _run(c, rnd["id"])  # heuristic only (mock LLM at this point)
    mid = c.get(f"/api/rounds/{rnd['id']}").json()["mappings"][0]["id"]
    c.post(f"/api/mappings/{mid}/approve", json={})
    dsid = c.post(f"/api/rounds/{rnd['id']}/transform", params={"sync": "true"}).json()
    dsid = c.get(f"/api/jobs/{dsid['job_id']}").json()["result"]["dataset_version_id"]

    fake = _FakeProvider()
    get_context().llm = LLMClient(fake, model="fake-model")  # type: ignore[arg-type]

    fake.answers = [
        {
            "root_causes": [{"finding": "f", "cause": "c", "fix": "x"}],
            "patched_mapping": None,
            "summary_th": "เรียบร้อย",
        }
    ]
    diag = c.post(f"/api/mappings/{mid}/diagnose").json()
    assert diag["ai"] is True and diag["summary_th"] == "เรียบร้อย" and diag["patched_mapping"] is None
    assert diag["findings"] == []

    fake.answers = [
        {
            "operations": [
                {
                    "kind": "set_status",
                    "target": "/status",
                    "value": "inactive",
                    "where": None,
                    "explanation_th": "ปิดทั้งหมด",
                },
                {
                    "kind": "scale_field",
                    "target": "/prices/*/amount",
                    "factor": 1.1,
                    "explanation_th": "ขึ้น 10%",
                },
                {
                    "kind": "delete_rows",
                    "target": "",
                    "where": {
                        "op": "eq",
                        "args": [{"op": "col", "name": "/sku"}, {"op": "const", "value": "A001"}],
                    },
                    "explanation_th": "ลบ",
                },
                # unknown kinds are dropped by the API; the target must still be a schema pointer or the
                # output guard (ADR-013) rejects the whole answer
                {"kind": "unknown_kind", "target": "/status", "explanation_th": "?"},
            ]
        }
    ]
    cp = c.post(f"/api/datasets/{dsid}/copilot", json={"instruction": "ปิดการขายทั้งหมดและขึ้นราคา 10%"}).json()
    assert cp["ai"] is True
    assert [o["kind"] for o in cp["operations"]] == ["set_field", "scale_field", "delete_rows"]
    assert cp["operations"][0]["target"] == "/status" and cp["operations"][0]["explanation"] == "ปิดทั้งหมด"
    assert cp["impact"]["rows_deleted"] == 1 and cp["impact"]["rows_affected"] == 12

    fake.answers = [{"operations": [], "needs_clarification": "หมวดไหน?"}]
    cp2 = c.post(f"/api/datasets/{dsid}/copilot", json={"instruction": "ลดราคา"}).json()
    assert cp2 == {"ai": True, "operations": [], "needs_clarification": "หมวดไหน?"}

    dash = c.get("/api/dashboard").json()
    assert dash["llm"]["provider"] == "fake" and dash["llm"]["calls"] == 3 and dash["llm"]["tokens"] == 360

    # AI-refined proposal on a fresh round: schema agent + mapping agent are both called
    rnd2 = c.post("/api/rounds", json={"customer_id": cust["id"], "label": "ai"}).json()
    _upload(c, rnd2["id"], make_workbook(isolated_env / "renamed.xlsx", rename_qty=True), name="renamed.xlsx")
    heuristic_doc = c.get(f"/api/mappings/{mid}").json()["document"]
    fake.answers = [
        {"tables": [{"table_index": 0, "role": "products"}]},
        {
            "mapping": heuristic_doc,
            "confidence": 0.5,
            "open_questions": ["AI asks: VAT?"],
            "changes_from_draft": [],
        },
    ]
    pjob = c.post(f"/api/rounds/{rnd2['id']}/profile", params={"sync": "true"}).json()["job_id"]
    assert c.get(f"/api/jobs/{pjob}").json()["status"] == "done"
    job_id = c.post(f"/api/rounds/{rnd2['id']}/propose", json={"sync": True, "use_llm": True}).json()[
        "job_id"
    ]
    job = c.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "done", job["error"]
    mv = c.get(f"/api/mappings/{job['result']['mapping_version_id']}").json()
    assert (
        mv["created_by"] == "ai" and mv["model"] == "fake-model" and mv["policy"]["source"] == "ai_adaptation"
    )
    assert "AI asks: VAT?" in mv["open_questions"]
    assert {entry["agent"] for entry in mv["agent_log"]} >= {"heuristic", "adaptation"}
