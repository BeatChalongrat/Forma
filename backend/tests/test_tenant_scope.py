"""Tenant scope beyond customer-owned rows (ADR-047): jobs, figures, learning and the all-customer views."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.test_api import _run, _upload, make_workbook

from forma.auth import Principal
from forma.db.models import Correction, LLMCall
from forma.db.session import session_scope
from forma.services.context import get_context
from forma.services.workspace_assistant import run_tool


@pytest.fixture
def client(isolated_env: Path) -> Iterator[TestClient]:
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def _round(c: TestClient, root: Path, code: str) -> dict[str, Any]:
    cust = c.post("/api/customers", json={"code": code, "name": code}).json()["id"]
    r = c.post("/api/rounds", json={"customer_id": cust}).json()["id"]
    _upload(c, r, make_workbook(root / f"{code}.xlsx"))
    job = _run(c, r)
    return {"customer": cust, "round": r, "job": job["id"]}


@pytest.fixture
def two(client: TestClient, isolated_env: Path) -> dict[str, Any]:
    a, b = _round(client, isolated_env, "SA"), _round(client, isolated_env, "SB")
    return {
        "a": a,
        "b": b,
        "scoped": {"X-Forma-User": "tenant", "X-Forma-Roles": "admin", "X-Forma-Customers": a["customer"]},
    }


def test_a_scoped_user_sees_only_its_own_jobs(client: TestClient, two: dict[str, Any]) -> None:
    c, a, b, h = client, two["a"], two["b"], two["scoped"]
    assert {j["round_id"] for j in c.get("/api/jobs", headers=h).json()} == {a["round"]}
    assert c.get(f"/api/jobs/{a['job']}", headers=h).status_code == 200
    assert c.get(f"/api/jobs/{b['job']}", headers=h).status_code == 404
    assert c.post(f"/api/jobs/{b['job']}/retry", headers=h).status_code == 404
    assert c.get("/api/jobs/stats", headers=h).status_code == 403
    assert c.get("/api/jobs/stats").status_code == 200
    # a group of jobs across customers shows the caller's part only
    run = c.post("/api/rounds/batch-run", json={"round_ids": [a["round"], b["round"]], "sync": True}).json()
    mine = c.get(f"/api/jobs/batch/{run['batch_id']}", headers=h).json()
    assert mine["total"] == 1 and mine["jobs"][0]["round_id"] == a["round"]
    other = {"X-Forma-User": "tenant", "X-Forma-Roles": "admin", "X-Forma-Customers": "cus_nobody"}
    assert c.get(f"/api/jobs/batch/{run['batch_id']}", headers=other).status_code == 404


def test_dashboard_figures_cover_the_callers_customers(client: TestClient, two: dict[str, Any]) -> None:
    c, a, b, h = client, two["a"], two["b"], two["scoped"]
    with session_scope() as s:
        for rid in (a["round"], b["round"], b["round"]):
            s.add(
                LLMCall(
                    round_id=rid,
                    agent="mapping",
                    model="m",
                    prompt_version="t",
                    input_tokens=10,
                    output_tokens=5,
                    cost_usd=1.0,
                )
            )
        s.add(Correction(customer_id=b["customer"], kind="alias", target="/sku", header="x", actor="t"))
    everyone, mine = c.get("/api/dashboard").json(), c.get("/api/dashboard", headers=h).json()
    assert everyone["mapping_versions"] == 2 and mine["mapping_versions"] == 1
    assert everyone["llm"]["calls"] == 3 and mine["llm"]["calls"] == 1 and mine["llm"]["cost_usd"] == 1.0
    assert everyone["learning"]["aliases"] == 1 and mine["learning"]["aliases"] == 0


def test_learning_is_scoped_for_reads_and_writes(client: TestClient, two: dict[str, Any]) -> None:
    c, a, b, h = client, two["a"], two["b"], two["scoped"]
    with session_scope() as s:
        for cid, kind, target in (
            (a["customer"], "alias", "/sku"),
            (b["customer"], "alias", "/brand"),
            (None, "promoted", "/barcode"),
        ):
            s.add(Correction(customer_id=cid, kind=kind, target=target, header="h", actor="t"))
    view = c.get("/api/learning", headers=h).json()
    assert view["stats"]["aliases"] == 1 and view["stats"]["promoted"] == 0
    assert [i["customer_id"] for i in view["learned"]["items"]] == [a["customer"]]
    assert view["suggestions"] == []
    rows = {i["target"]: i["id"] for i in c.get("/api/learning").json()["learned"]["items"]}
    assert c.delete(f"/api/learning/{rows['/brand']}", headers=h).status_code == 403
    assert c.delete(f"/api/learning/{rows['/barcode']}", headers=h).status_code == 403
    assert c.delete(f"/api/learning/{rows['/sku']}", headers=h).status_code == 204
    assert (
        c.post("/api/learning/promote", json={"header": "x", "target": "/sku"}, headers=h).status_code == 403
    )


def test_all_customer_views_need_all_customer_access(client: TestClient, two: dict[str, Any]) -> None:
    c, h = client, two["scoped"]
    for path in ("/api/calibration", "/api/audit", "/api/ops"):
        assert c.get(path, headers=h).status_code == 403, path
        assert c.get(path).status_code == 200, path
    assert (
        c.post("/api/calibration/apply", json={"id": "x", "digest": "0" * 16}, headers=h).status_code == 403
    )


def test_the_approval_policy_tool_needs_all_customer_access(client: TestClient, two: dict[str, Any]) -> None:
    with session_scope() as s:
        scoped = Principal("t", frozenset({"admin"}), frozenset({two["a"]["customer"]}))
        assert "approval_policy" not in run_tool(get_context(), s, scoped, "runtime_configuration", {})
        everyone = Principal("t", frozenset({"admin"}))
        assert "approval_policy" in run_tool(get_context(), s, everyone, "runtime_configuration", {})
