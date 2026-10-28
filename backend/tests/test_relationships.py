"""Relationship discovery between the tables of a round (services/relationships.py, ADR-019)."""

from __future__ import annotations

import copy
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import openpyxl
import polars as pl
import pytest
from fastapi.testclient import TestClient
from tests.test_api import FILE_NAME, _run, _upload, make_workbook

from forma.services.relationships import measure


@pytest.fixture
def client(isolated_env: Path) -> Iterator[TestClient]:
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def _round_with_warehouses(c: TestClient, root: Path) -> tuple[str, str]:
    """The standard workbook plus a warehouse list the mapping does not read (HKT has no stock)."""
    path = make_workbook(root / FILE_NAME)
    wb = openpyxl.load_workbook(path)
    wh = wb.create_sheet("Warehouses")
    wh.append(["คลัง", "ชื่อคลัง", "จังหวัด"])
    for row in (
        ("BKK", "คลังกรุงเทพ", "กรุงเทพฯ"),
        ("CNX", "คลังเชียงใหม่", "เชียงใหม่"),
        ("HKT", "คลังภูเก็ต", "ภูเก็ต"),
    ):
        wh.append(list(row))
    wb.save(path)
    cust = c.post("/api/customers", json={"code": "R1", "name": "relationships"}).json()["id"]
    r = c.post("/api/rounds", json={"customer_id": cust}).json()["id"]
    _upload(c, r, path)
    return r, _run(c, r)["result"]["mapping_version_id"]


def _graph(c: TestClient, round_id: str, doc: dict[str, Any] | None = None) -> dict[str, Any]:
    res = c.post(f"/api/rounds/{round_id}/relationships", json={"document": doc} if doc else {})
    assert res.status_code == 200, res.text
    return res.json()  # type: ignore[no-any-return]


def _edge(g: dict[str, Any], a: str, b: str, kind: str | None = None) -> dict[str, Any]:
    found = [
        e
        for e in g["edges"]
        if {e["from"]["node"], e["to"]["node"]} == {a, b} and (kind is None or e["kind"] == kind)
    ]
    assert len(found) == 1, (a, b, g["edges"])
    return found[0]


def test_measure_counts_on_normalized_keys() -> None:
    a = pl.DataFrame({"k": ["a1", " A2 ", "a3", None]})
    b = pl.DataFrame({"k": ["A1", "A1", "a2", "x9"]})
    m = measure(a, "k", b, "k")
    assert m["overlap"] == 2 and m["cardinality"] == "1:N"
    assert m["from_unique"] and not m["to_unique"]
    assert m["from_match"] == m["to_match"] == round(2 / 3, 4)
    assert m["from_orphan_rows"] == 1 and m["to_orphan_rows"] == 1  # a3 and x9


def test_used_links_are_measured_and_unused_tables_suggested(client: TestClient, isolated_env: Path) -> None:
    r, _ = _round_with_warehouses(client, isolated_env)
    g = _graph(client, r)
    sheets = {n["sheet"]: n for n in g["nodes"]}
    main, stock, promos, wh = sheets["สินค้า"], sheets["Stock"], sheets["Promotions"], sheets["Warehouses"]
    assert main["root"] and main["id"] == g["root"] and main["rows"] == 12
    # the mapper lists the warehouse sheet as a source but reads nothing from it
    assert stock["used"] and promos["used"] and not wh["used"] and "source_spec" not in wh
    assert any(col["key"] for col in main["columns"])

    link = _edge(g, main["id"], stock["id"], "link")
    assert link["used"] and link["from"]["node"] == main["id"]
    assert link["cardinality"] == "1:N" and link["to_match"] == 1.0 and link["to_orphan_rows"] == 0
    assert link["warnings"] == []
    assert _edge(g, main["id"], promos["id"], "link")["from_match"] == 0.25  # 3 of 12 products

    hint = _edge(g, wh["id"], stock["id"], "suggested")
    assert hint["from"] == {"node": wh["id"], "column": "คลัง"}
    assert hint["to"] == {"node": stock["id"], "column": "คลัง"}
    assert hint["cardinality"] == "1:N" and hint["to_match"] == 1.0 and hint["from_match"] < 1  # HKT
    assert not any({e["from"]["node"], e["to"]["node"]} == {stock["id"], promos["id"]} for e in g["edges"]), (
        "two tables hanging off the main table by the same key are siblings, not related"
    )


def test_accepting_a_suggestion_as_a_join_runs(client: TestClient, isolated_env: Path) -> None:
    r, mv = _round_with_warehouses(client, isolated_env)
    doc = client.get(f"/api/mappings/{mv}").json()["document"]
    doc["sources"] = [s for s in doc["sources"] if (s.get("sheet") or {}).get("name") != "Warehouses"]
    g = _graph(client, r, doc)
    sheets = {n["sheet"]: n for n in g["nodes"]}
    stock, wh = sheets["Stock"], sheets["Warehouses"]
    assert not wh["used"] and wh["source_spec"]["sheet"]["name"] == "Warehouses"  # ready to add
    assert _edge(g, wh["id"], stock["id"], "suggested")["to_match"] == 1.0
    doc["sources"].append(wh["source_spec"])
    doc["joins"].append(
        {
            "left": stock["id"],
            "right": wh["id"],
            "on": [{"left": "คลัง", "right": "คลัง"}],
            "cardinality": "N:1",
        }
    )
    g2 = _graph(client, r, doc)
    join = _edge(g2, stock["id"], wh["id"], "join")
    assert join["used"] and join["expected"] == "N:1" and join["to_unique"] and join["warnings"] == []
    assert next(n for n in g2["nodes"] if n["sheet"] == "Warehouses")["used"]
    pv = client.post(f"/api/rounds/{r}/mapping-preview", json={"document": doc}).json()
    assert pv["ok"], pv
    assert not [f for f in pv["findings"] if f["kind"] == "join" and f["severity"] == "error"]


def test_broken_links_and_multiplying_joins_are_flagged(client: TestClient, isolated_env: Path) -> None:
    r, mv = _round_with_warehouses(client, isolated_env)
    g = _graph(client, r)
    stock = next(n for n in g["nodes"] if n["sheet"] == "Stock")
    doc = client.get(f"/api/mappings/{mv}").json()["document"]
    coll = next(c for c in doc["collections"] if c["source"] == stock["id"])
    key = coll["link"][0]

    missing = copy.deepcopy(doc)
    next(c for c in missing["collections"] if c["source"] == stock["id"])["link"][0]["right"] = "ไม่มีคอลัมน์นี้"
    e = _edge(_graph(client, r, missing), g["root"], stock["id"], "link")
    assert e["missing"] and "not in the file" in e["warnings"][0]

    many = copy.deepcopy(doc)  # stock has two rows per product: joining it 'one per record' multiplies rows
    many["joins"].append(
        {"left": g["root"], "right": stock["id"], "on": [{"left": key["left"], "right": key["right"]}]}
    )
    j = _edge(_graph(client, r, many), g["root"], stock["id"], "join")
    assert j["cardinality"] == "1:N" and any("multiply" in w for w in j["warnings"])


def test_relationships_of_unknown_round(client: TestClient) -> None:
    assert client.post("/api/rounds/nope/relationships", json={}).status_code == 404


def test_a_drawn_connection_is_measured_before_it_is_added(client: TestClient, isolated_env: Path) -> None:
    r, _ = _round_with_warehouses(client, isolated_env)
    g = _graph(client, r)
    stock = next(n for n in g["nodes"] if n["sheet"] == "Stock")

    def probe(a: str, b: str) -> dict[str, Any]:
        body = {"probe": {"from": {"node": g["root"], "column": a}, "to": {"node": stock["id"], "column": b}}}
        return client.post(f"/api/rounds/{r}/relationships", json=body).json()["probe"]  # type: ignore[no-any-return]

    wrong = probe("หมวด", "คลัง")
    assert wrong["overlap"] == 0 and wrong["to_orphan_rows"] == 24
    assert wrong["warnings"] == ["no value of สินค้า.หมวด appears in Stock.คลัง — these columns do not connect"]
    right = probe("รหัสสินค้า", "รหัสสินค้า")
    assert right["overlap"] == 12 and right["cardinality"] == "1:N" and right["warnings"] == []
    assert probe("รหัสสินค้า", "ไม่มี")["missing"]
