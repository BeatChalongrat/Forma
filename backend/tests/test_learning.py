"""Learning from human decisions (ADR-038): an approval teaches, the next proposal reads it."""

from __future__ import annotations

import copy
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import openpyxl
import pytest
from fastapi.testclient import TestClient
from tests.test_api import _run, _upload


@pytest.fixture
def client(isolated_env: Path) -> Iterator[TestClient]:
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


TAUGHT = "col_17"  # the real price lives in a column whose header says nothing


def _book(path: Path, *, n: int = 10, extra: bool = False) -> Path:
    """A product sheet with a recognisable price column (a list price) and an opaque one (the real price).
    ``extra`` adds a column so the next round's layout drifts and the mapper proposes again (no memory reuse)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Products"
    ws.append(["รหัสสินค้า", "ชื่อสินค้า", "ยี่ห้อ", "ราคาขาย", TAUGHT, "สถานะ", *(["หมายเหตุ"] if extra else [])])
    for i in range(1, n + 1):
        ws.append([f"A{i:03d}", f"สินค้า {i}", "Siam", i * 100, i * 90, "ขาย", *(["-"] if extra else [])])
    wb.save(path)
    return path


def _round(c: TestClient, customer_id: str, path: Path) -> dict[str, Any]:
    r = c.post("/api/rounds", json={"customer_id": customer_id}).json()
    _upload(c, r["id"], path, path.name)
    _run(c, r["id"])
    return c.get(f"/api/rounds/{r['id']}").json()  # type: ignore[no-any-return]


def _mapping(c: TestClient, r: dict[str, Any]) -> dict[str, Any]:
    return c.get(f"/api/mappings/{r['summary']['mapping_version_id']}").json()  # type: ignore[no-any-return]


def _field(doc: dict[str, Any], semantic_pointer_suffix: str) -> dict[str, Any] | None:
    fields = [*doc["fields"], *(f for coll in doc["collections"] for f in coll["fields"])]
    return next((f for f in fields if f["target"].endswith(semantic_pointer_suffix)), None)


def _teach_price(c: TestClient, r: dict[str, Any], header: str) -> dict[str, Any]:
    """Point the price field at the unknown header in the Studio and approve: FORMA learns the alias."""
    m = _mapping(c, r)
    doc = copy.deepcopy(m["document"])
    price = _field(doc, "/amount")
    if price is None:  # the heuristic placed no price at all: add it to the pricing collection
        coll = next(x for x in doc["collections"] if "/prices" in x["target"])
        coll["fields"].append(
            {
                "target": f"{coll['target']}/*/amount",
                "expr": {"op": "parse_number_th", "args": [{"op": "col", "name": header}]},
                "confidence": 1.0,
                "evidence": [],
            }
        )
    else:
        price["expr"] = {
            "op": "parse_number_th",
            "args": [
                {
                    "op": "col",
                    "name": header,
                    "source": price["expr"].get("source")
                    or (price["expr"].get("args") or [{}])[0].get("source"),
                }
            ],
        }
        price["confidence"] = 1.0
    edited = c.post(f"/api/mappings/{m['id']}/edit", json={"document": doc})
    assert edited.status_code in (200, 201), edited.text
    approved = c.post(f"/api/mappings/{edited.json()['id']}/approve", json={})
    assert approved.status_code == 200, approved.text
    return approved.json()  # type: ignore[no-any-return]


def test_a_correction_is_learned_and_used_next_round(client: TestClient, isolated_env: Path) -> None:
    c = client
    cust = c.post("/api/customers", json={"code": "L1", "name": "L1"}).json()["id"]
    r1 = _round(c, cust, _book(isolated_env / "l1_r1.xlsx"))
    price_before = _field(_mapping(c, r1)["document"], "/amount")
    assert price_before is not None and "ราคาขาย" in str(price_before["expr"])  # the synonym wins at first
    _teach_price(c, r1, TAUGHT)
    view = c.get("/api/learning").json()
    kinds = {i["kind"] for i in view["learned"]["items"]}
    assert "alias" in kinds and view["stats"]["aliases"] >= 1
    taught = next(i for i in view["learned"]["items"] if i["kind"] == "alias")
    assert (
        taught["column"] == TAUGHT and taught["target"].endswith("/amount") and taught["customer_id"] == cust
    )
    # the next round of the same customer maps the header straight away, and says why
    r2 = _round(
        c, cust, _book(isolated_env / "l1_r2.xlsx", extra=True)
    )  # drifted: proposed again, not reused
    m2 = _mapping(c, r2)
    price = _field(m2["document"], "/amount")
    assert price is not None and TAUGHT in str(price["expr"])
    assert price["confidence"] == 1.0 and any("learned:" in e for e in price["evidence"])
    assert m2["policy"]["learning"]["used"] and taught["target"] in m2["policy"]["learning"]["used"]
    assert c.get("/api/learning").json()["stats"]["uses"] >= 1
    v = c.get(f"/api/rounds/{r2['id']}/verdict").json()["verdict"]
    assert any(e["kind"] == "learned" for e in v["evidence"])
    # another customer does not inherit it
    other = c.post("/api/customers", json={"code": "L2", "name": "L2"}).json()["id"]
    r3 = _round(c, other, _book(isolated_env / "l2_r1.xlsx"))
    p3 = _field(_mapping(c, r3)["document"], "/amount")
    assert p3 is None or not any("learned:" in e for e in p3["evidence"])


def test_a_confirmed_constant_is_reused(client: TestClient, isolated_env: Path) -> None:
    from tests.test_api import make_workbook

    c = client
    cust = c.post("/api/customers", json={"code": "L3", "name": "L3"}).json()["id"]
    r1 = _round(c, cust, make_workbook(isolated_env / "l3_r1.xlsx"))
    m1 = _mapping(c, r1)
    vat_ptr = _field(m1["document"], "/vat_included")
    assert vat_ptr is not None
    doc = copy.deepcopy(m1["document"])
    f = _field(doc, "/vat_included")
    assert f is not None
    f["expr"] = {"op": "const", "value": False}
    f["confidence"] = 1.0
    edited = c.post(f"/api/mappings/{m1['id']}/edit", json={"document": doc}).json()
    assert c.post(f"/api/mappings/{edited['id']}/approve", json={}).status_code == 200
    learned = c.get("/api/learning").json()["learned"]["items"]
    const = next(i for i in learned if i["kind"] == "constant")
    assert const["target"].endswith("/vat_included") and const["value"] is False
    r2 = _round(c, cust, make_workbook(isolated_env / "l3_r2.xlsx"))
    f2 = _field(_mapping(c, r2)["document"], "/vat_included")
    assert f2 is not None and f2["expr"] == {"op": "const", "value": False} and f2["confidence"] == 1.0
    assert not any("VAT" in q for q in _mapping(c, r2)["open_questions"])


def test_accepted_as_proposed_is_recorded_and_promotion_shares_an_alias(
    client: TestClient, isolated_env: Path
) -> None:
    from tests.test_api import make_workbook

    c = client
    cust = c.post("/api/customers", json={"code": "L4", "name": "L4"}).json()["id"]
    r = _round(c, cust, make_workbook(isolated_env / "l4.xlsx"))
    assert c.post(f"/api/mappings/{r['summary']['mapping_version_id']}/approve", json={}).status_code == 200
    stats = c.get("/api/learning").json()["stats"]
    assert stats["series"] and stats["series"][-1]["accepted_as_proposed"] >= 1
    # three customers teach the same header → suggested → promoted → a fourth customer gets it on round 1
    for code in ("P1", "P2", "P3"):
        cid = c.post("/api/customers", json={"code": code, "name": code}).json()["id"]
        _teach_price(c, _round(c, cid, _book(isolated_env / f"{code}.xlsx")), TAUGHT)
    view = c.get("/api/learning").json()
    sug = next((s for s in view["suggestions"] if s["header"] == TAUGHT.replace("_", " ")), None)
    assert sug is not None and sug["customers"] >= 3
    assert (
        c.post("/api/learning/promote", json={"header": sug["header"], "target": sug["target"]}).status_code
        == 201
    )
    assert not any(
        s["header"] == TAUGHT.replace("_", " ") for s in c.get("/api/learning").json()["suggestions"]
    )
    new = c.post("/api/customers", json={"code": "P4", "name": "P4"}).json()["id"]
    r4 = _round(c, new, _book(isolated_env / "P4.xlsx"))
    price = _field(_mapping(c, r4)["document"], "/amount")
    assert (
        price is not None and TAUGHT in str(price["expr"]) and any("everyone" in e for e in price["evidence"])
    )
    # forgetting retires it
    promoted = next(i for i in c.get("/api/learning").json()["learned"]["items"] if i["kind"] == "promoted")
    assert c.delete(f"/api/learning/{promoted['id']}").status_code == 204
    assert not any(i["kind"] == "promoted" for i in c.get("/api/learning").json()["learned"]["items"])


def test_learning_is_customer_scoped(client: TestClient, isolated_env: Path) -> None:
    c = client
    cust = c.post("/api/customers", json={"code": "S1", "name": "S1"}).json()["id"]
    _teach_price(c, _round(c, cust, _book(isolated_env / "s1.xlsx")), TAUGHT)
    other = c.post("/api/customers", json={"code": "S2", "name": "S2"}).json()["id"]
    scoped = {"X-Forma-User": "tenant", "X-Forma-Roles": "importer", "X-Forma-Customers": other}
    view = c.get("/api/learning", headers=scoped).json()
    assert view["learned"]["items"] == [] and view["suggestions"] == []
    assert (
        c.post("/api/learning/promote", json={"header": "x", "target": "/sku"}, headers=scoped).status_code
        == 403
    )
