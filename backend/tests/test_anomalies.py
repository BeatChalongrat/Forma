"""Change intelligence & anomaly detection (services/anomalies.py, ADR-022)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import openpyxl
import pytest
from fastapi.testclient import TestClient
from tests.test_lineage import published_round

from forma.ai.policy import DEFAULT_POLICY, validate_policy
from forma.schema_registry import TargetSchema
from forma.services.anomalies import detect

CFG = DEFAULT_POLICY["anomaly"]


def rec(
    sku: str,
    price: float | None = 100.0,
    *,
    cat: str = "food",
    qty: float = 50,
    promo: dict[str, Any] | None = None,
) -> dict[str, Any]:
    r: dict[str, Any] = {"sku": sku, "category": [cat], "inventory": [{"warehouse_code": "BKK", "qty": qty}]}
    if price is not None:
        r["prices"] = [{"price_list": "standard", "amount": price}]
    if promo:
        r["promotions"] = [promo]
    return r


def kinds(rep: dict[str, Any]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for a in rep["items"]:
        out.setdefault(a["kind"], []).append(a["identity"])
    return out


def test_within_one_round(schema: TargetSchema) -> None:
    records = [rec(f"S{i:02d}", 100 + i) for i in range(12)]
    records += [
        rec("ZERO", 0),
        rec("OUT", 5000),
        rec("DISC", promo={"discount_percent": 80}),
        rec("BACK", promo={"start_date": "2026-10-20", "end_date": "2026-10-01"}),
        rec("NEG", qty=-3),
        rec("BIGOFF", 100, promo={"discount_amount": 150}),
    ]
    rep = detect(schema, records, CFG)
    k = kinds(rep)
    assert k["price_not_positive"] == ["ZERO"] and k["price_outlier"] == ["OUT"]
    assert k["discount_too_high"] == ["DISC"] and k["promo_dates_reversed"] == ["BACK"]
    assert k["stock_negative"] == ["NEG"] and k["discount_exceeds_price"] == ["BIGOFF"]
    assert rep["records"] == 18 and rep["anomalous_records"] == 6 and rep["normal_pct"] == 66.7
    assert rep["items"][0]["severity"] == "high" and rep["high"] == 5 and rep["medium"] == 1
    # deterministic: same input, same report
    assert detect(schema, records, CFG) == rep


def test_against_last_round_and_usual_size(schema: TargetSchema) -> None:
    old = [rec(f"S{i:02d}", 100, qty=100) for i in range(20)]
    new = [rec(f"S{i:02d}", 100, qty=100) for i in range(16)]  # 4 of 20 SKUs gone
    new[0] = rec("S00", 140, qty=100)  # +40 % → medium
    new[1] = rec("S01", 200, qty=100)  # +100 % → high
    new[2] = rec("S02", 100, qty=5)  # stock −95 %
    rep = detect(schema, new, CFG, previous=old, history_rows=[20, 21, 19])
    k = kinds(rep)
    assert k["price_jump"] == ["S01", "S00"]  # high first
    assert [a["severity"] for a in rep["items"] if a["kind"] == "price_jump"] == ["high", "medium"]
    assert k["stock_drop"] == ["S02"]
    assert [a["kind"] for a in rep["dataset"]] == ["skus_removed"]  # 16 vs ~20 is within 30 %
    assert rep["baseline"] == {"rounds": 3, "avg_rows": 20.0, "current_rows": 16}
    small = detect(schema, new[:5], CFG, history_rows=[20, 21, 19])
    assert [a["kind"] for a in small["dataset"]] == ["row_count"]
    quiet = detect(schema, old, CFG, previous=old)
    assert quiet["items"] == [] and quiet["normal_pct"] == 100.0 and "nothing unusual" in quiet["headline"]


def test_policy_validates_anomaly_settings() -> None:
    assert validate_policy({"anomaly": {"max_price_change_pct": 25}}) == []
    assert validate_policy({"anomaly": {"max_price_change_pct": -1}})
    assert validate_policy({"anomaly": {"block_approval_on_high": "yes"}})
    assert validate_policy({"anomaly": {"nope": 1}})


@pytest.fixture
def client(isolated_env: Path) -> Iterator[TestClient]:
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def test_approval_waits_for_an_acknowledgement(client: TestClient, isolated_env: Path) -> None:
    def zero(wb: openpyxl.Workbook) -> None:
        wb["สินค้า"]["G6"] = "0"  # A003 price 0

    _, ds = published_round(client, isolated_env, "AN1", zero)
    rep = client.get(f"/api/datasets/{ds}/changes-report").json()["anomalies"]
    assert {a["kind"] for a in rep["items"]} >= {"price_not_positive", "price_jump"} and rep["high"] >= 2
    held = client.post(f"/api/datasets/{ds}/approve", json={})
    assert held.status_code == 409 and held.json()["details"]["high"] == rep["high"]
    ok = client.post(
        f"/api/datasets/{ds}/approve", json={"acknowledge_anomalies": True, "comment": "confirmed"}
    )
    assert ok.status_code == 200 and ok.json()["status"] == "approved"
    audit = client.get("/api/audit", params={"entity_id": ds}).json()
    assert any(e["action"] == "dataset.approve" and "acknowledged_anomalies" in str(e) for e in audit)
