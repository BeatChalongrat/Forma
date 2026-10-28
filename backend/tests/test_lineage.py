"""Explainable lineage (services/lineage.py, ADR-020): every value back to its Excel cell, and why it changed."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import openpyxl
import polars as pl
import pytest
from fastapi.testclient import TestClient
from tests.test_api import _run, _upload, make_workbook

from forma.mapping_dsl.models import TableSpec
from forma.profiling.tables import SHEET_ROW, extract_table


@pytest.fixture
def client(isolated_env: Path) -> Iterator[TestClient]:
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def _job(c: TestClient, url: str) -> None:
    jid = c.post(url, params={"sync": "true"}).json()["job_id"]
    assert c.get(f"/api/jobs/{jid}").json()["status"] == "done"


def published_round(
    c: TestClient, root: Path, code: str, change: Callable[[openpyxl.Workbook], None] | None = None
) -> tuple[str, str]:
    """Round 1 approved and published, then round 2 (optionally edited) transformed: (customer, dataset 2)."""
    cust = c.post("/api/customers", json={"code": code, "name": code}).json()["id"]
    r1 = c.post("/api/rounds", json={"customer_id": cust, "label": "R1"}).json()["id"]
    _upload(c, r1, make_workbook(root / f"{code}1.xlsx"), name="ERP.xlsx")
    mv = _run(c, r1)["result"]["mapping_version_id"]
    assert c.post(f"/api/mappings/{mv}/approve", json={}).status_code == 200
    _job(c, f"/api/rounds/{r1}/transform")
    ds1 = c.get(f"/api/rounds/{r1}").json()["datasets"][0]["id"]
    assert c.post(f"/api/datasets/{ds1}/approve", json={}).status_code == 200
    p2 = make_workbook(root / f"{code}2.xlsx")
    if change is not None:
        wb = openpyxl.load_workbook(p2)
        change(wb)
        wb.save(p2)
    r2 = c.post("/api/rounds", json={"customer_id": cust, "label": "R2"}).json()["id"]
    _upload(c, r2, p2, name="ERP.xlsx")
    _run(c, r2)
    if not c.get(f"/api/rounds/{r2}").json()["datasets"]:
        _job(c, f"/api/rounds/{r2}/transform")
    return cust, c.get(f"/api/rounds/{r2}").json()["datasets"][0]["id"]


def _price_pointer(c: TestClient, ds: str) -> str:
    cols = c.get(f"/api/datasets/{ds}/rows", params={"limit": 1}).json()["columns"]
    return str(next(x["pointer"] for x in cols if x.get("semantic") == "price.amount")).replace("/*/", "/0/")


def test_extract_table_keeps_sheet_rows_and_columns_only_when_asked(grid: Any) -> None:
    raw = grid(
        [
            ["Report"],
            [None],
            ["code", "name", "price"],
            ["A1", "x", "10"],
            [None, None, None],
            ["A2", "y", "20"],
            ["รวม", None, "30"],
        ]
    )
    spec = TableSpec(header_row=2)
    plain = extract_table(raw, spec)
    assert plain.columns == ["code", "name", "price"] and plain.height == 2
    pos: dict[str, int] = {}
    located = extract_table(raw, spec, row_numbers=True, positions=pos)
    assert located.drop(SHEET_ROW).equals(plain)  # same data, plus where each row came from
    assert located[SHEET_ROW].to_list() == [3, 5] and pos == {"code": 0, "name": 1, "price": 2}
    assert isinstance(located, pl.DataFrame)


def test_every_value_traces_to_its_cell(client: TestClient, isolated_env: Path) -> None:
    def bump(wb: openpyxl.Workbook) -> None:
        wb["สินค้า"]["G5"] = "999.00"  # A002's price in the ERP file

    _, ds = published_round(client, isolated_env, "L1", bump)
    ptr = _price_pointer(client, ds)
    t = client.get(f"/api/datasets/{ds}/trace/A002", params={"pointer": ptr}).json()
    assert t["source"] == {"file": "ERP.xlsx", "sheet": "สินค้า", "row": 5, "source": t["source"]["source"]}
    f = t["focus"]
    assert f["value"] == 999.0 and f["mapped"] == 999.0
    assert [(x["sheet"], x["column"], x["row"], x["raw"], x["verified"]) for x in f["cells"]] == [
        ("สินค้า", "G", 5, "999.00", True)
    ]
    assert f["cause"]["kind"] == "source_changed" and f["previous"]["value"] == 214.0
    assert any("G5" in e and "214.00" in e for e in f["cause"]["evidence"])
    # collection items trace to their own sheet rows
    stock = [x for x in t["fields"] if x["pointer"].startswith("/inventory/0/")]
    assert stock and all(c["sheet"] == "Stock" and c["row"] == 4 for x in stock for c in x["cells"])
    unchanged = client.get(f"/api/datasets/{ds}/trace/A004", params={"pointer": ptr}).json()["focus"]
    assert unchanged["cause"]["kind"] == "unchanged"
    assert client.get(f"/api/datasets/{ds}/trace/NOPE").status_code == 404


def test_manual_edits_show_in_history_and_cause(client: TestClient, isolated_env: Path) -> None:
    _, ds = published_round(client, isolated_env, "L2")
    ptr = _price_pointer(client, ds)
    r = client.post(
        f"/api/datasets/{ds}/changes",
        json={
            "operations": [{"kind": "set_cell", "identity": "A003", "target": ptr, "value": 333}],
            "note": "buyer",
        },
    )
    ds2 = r.json()["dataset"]["id"]
    t = client.get(f"/api/datasets/{ds2}/trace/A003", params={"pointer": ptr}).json()
    f = t["focus"]
    assert f["value"] == 333 and f["mapped"] == 321.0
    assert (
        f["cause"]["kind"] == "manual"
        and f["edits"][0]["before"] == 321.0
        and f["edits"][0]["note"] == "buyer"
    )
    assert [(h["version"], h["kind"]) for h in t["history"]] == [(1, "transform"), (2, "one_time")]
    assert t["history"][1]["changes"] == [{"pointer": ptr, "before": 321.0, "after": 333}]
    first = client.get(f"/api/datasets/{ds}/trace/A001").json()
    assert first["focus"] is None and first["history"][0]["kind"] == "transform"


def test_first_round_has_no_baseline(client: TestClient, isolated_env: Path) -> None:
    cust = client.post("/api/customers", json={"code": "L3", "name": "x"}).json()["id"]
    r1 = client.post("/api/rounds", json={"customer_id": cust}).json()["id"]
    _upload(client, r1, make_workbook(isolated_env / "l3.xlsx"))
    mv = _run(client, r1)["result"]["mapping_version_id"]
    client.post(f"/api/mappings/{mv}/approve", json={})
    _job(client, f"/api/rounds/{r1}/transform")
    ds = client.get(f"/api/rounds/{r1}").json()["datasets"][0]["id"]
    f = client.get(f"/api/datasets/{ds}/trace/A001", params={"pointer": "/product_name"}).json()["focus"]
    assert f["cause"]["kind"] == "no_baseline" and f["cells"][0]["column"] == "B"
