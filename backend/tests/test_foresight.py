"""Proactive intelligence (ADR-039): expected deliveries, the watchlist, per-customer anomaly baselines."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from forma.db.models import ImportRound
from forma.services import foresight


def _round(created: str, **summary: Any) -> ImportRound:
    r = ImportRound(customer_id="cus_1", label=created, summary=summary)
    r.created_at = dt.datetime.fromisoformat(created).replace(tzinfo=dt.UTC)
    return r


def test_cadence_from_round_dates() -> None:
    rounds = [_round("2026-09-01"), _round("2026-09-15"), _round("2026-09-29"), _round("2026-10-13")]
    cad = foresight.cadence(rounds, today=dt.date(2026, 10, 27))
    assert cad["every_days"] == 14 and cad["expected"] == "2026-10-27" and cad["status"] == "due"
    assert foresight.cadence(rounds, today=dt.date(2026, 11, 2))["status"] == "overdue"
    assert foresight.cadence(rounds, today=dt.date(2026, 10, 20))["status"] == "ahead"
    assert foresight.cadence(rounds[:1])["status"] == "unknown"
    # two uploads on the same day are one delivery
    same_day = [_round("2026-09-01"), _round("2026-09-01T10:00"), _round("2026-09-15")]
    assert foresight.cadence(same_day, today=dt.date(2026, 9, 29))["every_days"] == 14


@pytest.fixture
def client(isolated_env: Path) -> Iterator[TestClient]:
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def test_expected_and_watchlist_through_the_api(client: TestClient) -> None:
    from forma.db.session import session_scope

    c = client
    late = c.post("/api/customers", json={"code": "LATE", "name": "Late Co"}).json()["id"]
    shaky = c.post("/api/customers", json={"code": "SHAKY", "name": "Shaky Co"}).json()["id"]
    fresh = c.post("/api/customers", json={"code": "NEW", "name": "New Co"}).json()["id"]
    today = dt.datetime.now(dt.UTC).date()
    with session_scope() as s:
        for d in (60, 46, 32):  # every 14 days, last one 32 days ago → overdue by 18
            r = ImportRound(
                customer_id=late, label=f"r{d}", summary={"drift": "identical", "decision": "auto_approve"}
            )
            r.created_at = dt.datetime.combine(today - dt.timedelta(days=d), dt.time(9), tzinfo=dt.UTC)
            s.add(r)
        for d, drift, decision, status in (
            (30, "identical", "auto_approve", "published"),
            (16, "columns_renamed", "review", "published"),
            (2, "columns_removed", "review", "review"),
        ):
            r = ImportRound(
                customer_id=shaky,
                label=f"r{d}",
                status=status,
                summary={"drift": drift, "decision": decision},
            )
            r.created_at = dt.datetime.combine(today - dt.timedelta(days=d), dt.time(9), tzinfo=dt.UTC)
            s.add(r)
        r = ImportRound(customer_id=fresh, label="only", summary={})
        r.created_at = dt.datetime.combine(today - dt.timedelta(days=40), dt.time(9), tzinfo=dt.UTC)
        s.add(r)
    view = c.get("/api/foresight").json()
    codes = [x["code"] for x in view["expected"]]
    assert codes[0] == "LATE"
    item = view["expected"][0]
    assert item["status"] == "overdue" and item["every_days"] == 14 and item["late_days"] == 18
    assert "LATE" in item["text"]["th"] and "18" in item["text"]["th"]
    assert "NEW" not in codes  # one round: no cadence yet
    assert view["counts"]["unknown"] >= 1
    watch = {x["code"]: x for x in view["watchlist"]}
    assert "SHAKY" in watch and watch["SHAKY"]["reasons"][0]["en"].startswith(
        "layout changed in 2 of the last 3"
    )
    assert "LATE" not in watch
    # tenant scope: a principal limited to NEW sees nothing of the others
    scoped = c.get("/api/foresight", headers={"X-Forma-User": "tenant", "X-Forma-Customers": fresh}).json()
    assert scoped["expected"] == [] and scoped["watchlist"] == []


def test_price_move_baseline_proposal_from_acknowledged_rounds(client: TestClient) -> None:
    """Two rounds whose price jumps a person acknowledged → a per-customer threshold proposal (ADR-039 §3)."""
    from forma.db.models import AuditLog, DatasetVersion
    from forma.db.session import session_scope

    c = client
    cust = c.post("/api/customers", json={"code": "MOVE", "name": "Move Co"}).json()["id"]
    with session_scope() as s:
        for i, pcts in enumerate(([31.0, 35.0, 40.0, 44.0], [33.0, 38.0, 52.0])):
            r = ImportRound(customer_id=cust, label=f"r{i}", status="published", summary={})
            s.add(r)
            s.flush()
            dv = DatasetVersion(
                round_id=r.id,
                customer_id=cust,
                mapping_version_id="map_x",
                version=1,
                status="published",
                row_count=10,
                records_key=f"datasets/{cust}/{i}.jsonl",
            )
            s.add(dv)
            s.flush()
            r.summary = {
                "anomaly_summary": {
                    "dataset_id": dv.id,
                    "high": len(pcts),
                    "by_kind": {"price_jump": len(pcts)},
                    "price_jump_pcts": pcts,
                }
            }
            s.add(
                AuditLog(
                    actor="ops",
                    action="dataset.approve",
                    entity="dataset",
                    entity_id=dv.id,
                    details={"acknowledged_anomalies": len(pcts)},
                )
            )
    view = c.get("/api/calibration").json()
    prop = next(p for p in view["proposals"] if p["id"] == f"price_move:{cust}")
    assert (
        prop["path"] == "anomaly.max_price_change_pct" and prop["current"] == 30.0 and prop["value"] == 57.0
    )  # p90 = 52 → +5
    assert (
        c.post("/api/calibration/apply", json={"id": prop["id"], "digest": prop["digest"]}).status_code == 200
    )
    assert c.get(f"/api/customers/{cust}").json()["policy_overrides"] == {
        "anomaly": {"max_price_change_pct": 57.0}
    }
    assert not any(p["id"] == f"price_move:{cust}" for p in c.get("/api/calibration").json()["proposals"])
