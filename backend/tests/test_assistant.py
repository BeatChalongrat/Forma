"""Copilot planning and evidence-based answers (services/assistant.py, ADR-023); bulk edits and override
collisions (services/datasets.py, services/sandbox.py)."""

from __future__ import annotations

import copy
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import openpyxl
import pytest
from fastapi.testclient import TestClient
from tests.test_api import _run, _upload, make_workbook
from tests.test_lineage import _job, _price_pointer

from forma.schema_registry import TargetSchema
from forma.services.assistant import is_question, rule_based_plan
from forma.services.datasets import apply_operations


def test_plan_with_conditions_across_collections(schema: TargetSchema) -> None:
    ops = rule_based_plan(
        "ลดราคาสินค้าหมวด Electronics ลง 5% เฉพาะรายการที่ Stock มากกว่า 100 และไม่รวม SKU ที่อยู่ใน Promotion", schema
    )
    assert ops is not None and len(ops) == 1
    op = ops[0]
    assert op["kind"] == "scale_field" and op["factor"] == 0.95 and op["target"] == "/prices/*/amount"
    assert op["where"] == {
        "op": "and",
        "args": [
            {
                "op": "contains",
                "args": [{"op": "col", "name": "/category"}],
                "params": {"pattern": "Electronics", "literal": True},
            },
            {
                "op": "gt",
                "args": [{"op": "col", "name": "sum(/inventory/*/qty)"}, {"op": "const", "value": 100.0}],
            },
            {"op": "lt", "args": [{"op": "col", "name": "count(/promotions)"}, {"op": "const", "value": 1}]},
        ],
    }
    assert "stock รวม > 100" in op["explanation"] and "ไม่อยู่ในโปรโมชั่น" in op["explanation"]
    up = rule_based_plan("ขึ้นราคา 10% ยี่ห้อ Siam ที่อยู่ในโปร", schema)
    assert up is not None and up[0]["factor"] == 1.1 and up[0]["where"]["args"][1]["op"] == "gte"
    assert rule_based_plan("ลดราคา 5%", schema) is None  # which records? never "all" by accident
    assert rule_based_plan("ทำอะไรสักอย่าง", schema) is None
    assert is_question("ทำไม join ไม่สำเร็จ") and is_question("why?") and not is_question("ลดราคา 5%")


def _records() -> list[dict[str, Any]]:
    return [
        {"sku": "A", "category": ["food"], "prices": [{"amount": 100}], "inventory": [{"qty": 150}]},
        {
            "sku": "B",
            "category": ["food"],
            "prices": [{"amount": 100}],
            "inventory": [{"qty": 60}, {"qty": 60}],
        },
        {
            "sku": "C",
            "category": ["food"],
            "prices": [{"amount": 100}],
            "inventory": [{"qty": 500}],
            "promotions": [{"type": "percent_off"}],
        },
        {"sku": "D", "category": ["toys"], "prices": [{"amount": 100}], "inventory": [{"qty": 500}]},
    ]


def test_conditions_and_picked_rows_select_the_right_records(schema: TargetSchema) -> None:
    ops = rule_based_plan("ลดราคาหมวด food 10% เฉพาะ stock มากกว่า 100 ไม่รวมสินค้าที่อยู่ในโปรโมชั่น", schema)
    assert ops is not None
    new, impact = apply_operations(_records(), ops, schema)
    assert [s["identity"] for s in impact["sample"]] == [
        "A",
        "B",
    ]  # B: 60 + 60 = 120 in stock; C is on promotion
    assert new[0]["prices"][0]["amount"] == 90.0 and new[3]["prices"][0]["amount"] == 100
    picked = [{"kind": "set_field", "target": "/status", "value": "inactive", "identities": ["B", "D"]}]
    out, imp = apply_operations(_records(), picked, schema)
    assert [r.get("status") for r in out] == [None, "inactive", None, "inactive"] and imp[
        "rows_affected"
    ] == 2
    gone, imp2 = apply_operations(_records(), [{"kind": "delete_rows", "identities": ["A"]}], schema)
    assert [r["sku"] for r in gone] == ["B", "C", "D"] and imp2["rows_deleted"] == 1


@pytest.fixture
def client(isolated_env: Path) -> Iterator[TestClient]:
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def _dataset(c: TestClient, root: Path, stock_extra: list[list[Any]] | None = None) -> tuple[str, str, str]:
    p = make_workbook(root / "erp.xlsx")
    if stock_extra:
        wb = openpyxl.load_workbook(p)
        for row in stock_extra:
            wb["Stock"].append(row)
        wb.save(p)
    cust = c.post("/api/customers", json={"code": "AS", "name": "assistant"}).json()["id"]
    r = c.post("/api/rounds", json={"customer_id": cust}).json()["id"]
    _upload(c, r, p)
    mv = _run(c, r)["result"]["mapping_version_id"]
    assert c.post(f"/api/mappings/{mv}/approve", json={}).status_code == 200
    _job(c, f"/api/rounds/{r}/transform")
    return cust, mv, c.get(f"/api/rounds/{r}").json()["datasets"][0]["id"]


def test_copilot_api_plans_conditions_without_an_llm(client: TestClient, isolated_env: Path) -> None:
    _, _, ds = _dataset(client, isolated_env)
    cp = client.post(
        f"/api/datasets/{ds}/copilot",
        json={
            "instruction": "ลดราคาสินค้าหมวด อาหาร ลง 5% เฉพาะรายการที่ Stock มากกว่า 20 และไม่รวม SKU ที่อยู่ใน Promotion"
        },
    ).json()
    assert cp["ai"] is False and cp["operations"][0]["where"]["op"] == "and"
    assert sorted({s["identity"] for s in cp["impact"]["sample"]}) == ["A006", "A009", "A012"]
    vague = client.post(f"/api/datasets/{ds}/copilot", json={"instruction": "ลดราคา 5%"}).json()
    assert vague["operations"] == [] and "ต้องระบุ" in vague["needs_clarification"]


def test_why_questions_are_answered_from_evidence(client: TestClient, isolated_env: Path) -> None:
    # stock rows whose product code is zero-padded, or does not exist at all
    _, _, ds = _dataset(client, isolated_env, [["0A001", "BKK", 1], ["ZZ9", "BKK", 1]])
    join = client.post(f"/api/datasets/{ds}/ask", json={"question": "ทำไม SKU บางรายการ join ไม่สำเร็จ"}).json()
    assert join["intent"] == "join" and "2 แถว" in join["answer"]
    ev = join["evidence"][0]
    assert ev["orphan_keys"] == ["0A001", "ZZ9"] and ev["orphan_rows"] == 2
    rec = client.post(f"/api/datasets/{ds}/ask", json={"question": "ทำไมราคา A002 เป็นแบบนี้"}).json()
    assert rec["intent"] == "record" and rec["trace"]["identity"] == "A002"
    assert any("G5" in e for e in rec["evidence"])
    an = client.post(f"/api/datasets/{ds}/ask", json={"question": "มีอะไรผิดปกติที่ควรตรวจก่อน"}).json()
    assert an["intent"] == "anomalies" and an["answer"]
    unknown = client.post(f"/api/datasets/{ds}/ask", json={"question": "สวัสดี"}).json()
    assert unknown["intent"] in ("unknown", "findings")


def test_overrides_that_collide_with_a_new_mapping(client: TestClient, isolated_env: Path) -> None:
    _, mv, ds = _dataset(client, isolated_env)
    ptr = _price_pointer(client, ds)
    # pin two values on every future import, one by identity, one for a picked row set
    ops = [
        {"kind": "set_cell", "identity": "A002", "target": "/product_name", "value": "ชื่อที่ล็อกไว้"},
        {"kind": "set_field", "target": "/brand", "value": "House", "identities": ["A003", "A004"]},
    ]
    r = client.post(f"/api/datasets/{ds}/changes", json={"operations": ops, "kind": "persistent"})
    assert r.status_code == 201
    cust = client.get(f"/api/datasets/{ds}").json()["customer_id"]
    overrides = client.get(f"/api/customers/{cust}").json()["overrides"]
    assert sorted((o["target"], o["where"].get("identity")) for o in overrides) == [
        ("/brand", "A003"),
        ("/brand", "A004"),
        ("/product_name", "A002"),
    ]
    # a new mapping reads product_name from another column: the override now hides a real change
    doc = copy.deepcopy(client.get(f"/api/mappings/{mv}").json()["document"])
    for f in doc["fields"]:
        if f["target"] == "/product_name":
            f["expr"] = {"op": "col", "name": "หมวด"}
    cmp = client.post(f"/api/mappings/{mv}/compare", json={"document": doc}).json()
    conflicts = cmp["override_conflicts"]
    assert [c["kind"] for c in conflicts] == ["value_changed"] and conflicts[0]["identity"] == "A002"
    assert cmp["delta"]["override_conflicts"] == 1
    assert ptr  # the price pointer stays where it was
