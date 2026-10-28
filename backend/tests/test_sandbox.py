"""Mapping simulation sandbox (ADR-018): a new mapping runs next to the approved one on the same real
files; the policy blocks what would make the data worse and puts changed prices in front of a human."""

from __future__ import annotations

import copy
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.test_api import FILE_NAME, _run, _upload, make_workbook


@pytest.fixture
def client(isolated_env: Path) -> Iterator[TestClient]:
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def _customer_with_approved_mapping(c: TestClient, root: Path) -> tuple[str, str]:
    cust = c.post("/api/customers", json={"code": "S1", "name": "sandbox"}).json()["id"]
    r1 = c.post("/api/rounds", json={"customer_id": cust}).json()["id"]
    _upload(c, r1, make_workbook(root / FILE_NAME))
    mv = _run(c, r1)["result"]["mapping_version_id"]
    assert c.post(f"/api/mappings/{mv}/approve", json={}).status_code == 200
    return cust, mv


def _edit(c: TestClient, mapping_id: str, change: Any) -> dict[str, Any]:
    doc = copy.deepcopy(c.get(f"/api/mappings/{mapping_id}").json()["document"])
    change(doc)
    r = c.post(f"/api/mappings/{mapping_id}/edit", json={"document": doc})
    assert r.status_code == 201, r.text
    return c.get(f"/api/mappings/{r.json()['id']}").json()  # type: ignore[no-any-return]


def test_drifted_round_is_compared_with_the_approved_mapping(client: TestClient, isolated_env: Path) -> None:
    c = client
    cust, approved = _customer_with_approved_mapping(c, isolated_env)
    r2 = c.post("/api/rounds", json={"customer_id": cust}).json()["id"]
    _upload(c, r2, make_workbook(isolated_env / "renamed.xlsx", rename_qty=True), name="สินค้า_พย69_C001.xlsx")
    mv = c.get(f"/api/mappings/{_run(c, r2)['result']['mapping_version_id']}").json()
    sb = mv["policy"]["sandbox"]
    assert sb["baseline_id"] == approved and sb["verdict"] == "pass" and sb["problems"] == []
    # the approved mapping no longer finds the renamed stock column: the new one repairs it
    assert (
        sb["old"]["errors"] > sb["new"]["errors"] == 0
        and sb["old"]["rows_out"] == sb["new"]["rows_out"] == 12
    )
    assert sb["delta"]["lost"] == 0 and sb["delta"]["changed_records"] == 12
    page = c.get(f"/api/mappings/{mv['id']}/sandbox", params={"list": "changed", "limit": 5}).json()
    assert page["total"] == 12 and len(page["items"]) == 5
    assert any("/inventory/" in d["pointer"] for d in page["items"][0]["changes"])
    assert c.get(f"/api/mappings/{mv['id']}/sandbox", params={"list": "nope"}).status_code == 422


def test_an_edit_that_makes_data_worse_is_blocked(client: TestClient, isolated_env: Path) -> None:
    c = client
    cust, _approved = _customer_with_approved_mapping(c, isolated_env)
    r2 = c.post("/api/rounds", json={"customer_id": cust}).json()["id"]
    _upload(c, r2, make_workbook(isolated_env / FILE_NAME))
    reused = _run(c, r2)["result"]["mapping_version_id"]
    assert (
        c.get(f"/api/mappings/{reused}").json()["policy"]["sandbox"] is None
    )  # verbatim reuse: nothing to compare

    # dropping the prices: every record loses its price → blocked, with the reason and the evidence
    no_prices = _edit(
        c,
        reused,
        lambda d: d.__setitem__("collections", [x for x in d["collections"] if x["target"] != "/prices"]),
    )
    sb = no_prices["policy"]["sandbox"]
    assert sb["verdict"] == "fail" and sb["delta"]["new_invalid_prices"] == 12
    assert no_prices["policy"]["decision"] == "block"
    assert any("without a valid price" in r for r in no_prices["policy"]["reasons"])
    refused = c.post(f"/api/mappings/{no_prices['id']}/approve", json={"comment": "ok"})
    assert refused.status_code == 403 and refused.json()["error"] == "policy_blocked"

    # a filter that drops records the approved mapping produced is blocked too
    def drop_some(d: dict[str, Any]) -> None:
        sku = next(f for f in d["fields"] if f["target"] == "/sku")["expr"]
        d["record"]["filters"] = [{"op": "neq", "args": [sku, {"op": "const", "value": "A001"}]}]

    lossy = _edit(c, reused, drop_some)["policy"]["sandbox"]
    assert lossy["verdict"] == "fail" and lossy["delta"]["lost"] == 1
    lost = c.get(f"/api/mappings/{_edit(c, reused, drop_some)['id']}/sandbox", params={"list": "lost"}).json()
    assert lost["items"] == [{"identity": "A001"}]


def test_compare_unsaved_edits_shows_every_changed_price(client: TestClient, isolated_env: Path) -> None:
    c = client
    _cust, approved = _customer_with_approved_mapping(c, isolated_env)
    doc = copy.deepcopy(c.get(f"/api/mappings/{approved}").json()["document"])
    prices = next(x for x in doc["collections"] if x["target"] == "/prices")
    amount = next(f for f in prices["fields"] if f["target"] == "/prices/*/amount")
    amount["expr"] = {"op": "vat_adjust", "args": [amount["expr"]], "params": {"mode": "add", "rate": 0.07}}
    out = c.post(f"/api/mappings/{approved}/compare", json={"document": doc}).json()
    assert out["baseline_id"] == approved and out["verdict"] == "pass"  # nothing lost or broken …
    assert out["delta"]["changed_prices"] == 12  # … but every price moves, and the reviewer sees each one
    first = out["details"]["price_changes"][0]
    assert first["pct"] == pytest.approx(7.0, abs=0.01) and first["after"] > first["before"]
    # policy: changed prices never pass automatically
    from forma.ai.policy import PolicyEngine

    assert PolicyEngine().sandbox_problems(out) == []
    assert c.post(f"/api/mappings/{approved}/compare", json={"document": {"nope": 1}}).status_code == 422


def test_policy_owns_the_thresholds(client: TestClient, isolated_env: Path) -> None:
    c = client
    cust, _approved = _customer_with_approved_mapping(c, isolated_env)
    policy = copy.deepcopy(c.get("/api/settings/policy").json()["effective"])
    assert policy["sandbox"]["enabled"] is True
    bad = copy.deepcopy(policy)
    bad["sandbox"]["max_lost_records"] = -1
    assert c.put("/api/settings/policy", json={"policy": bad}).status_code == 422
    policy["sandbox"]["enabled"] = False
    assert c.put("/api/settings/policy", json={"policy": policy}).status_code == 200
    r2 = c.post("/api/rounds", json={"customer_id": cust}).json()["id"]
    _upload(c, r2, make_workbook(isolated_env / FILE_NAME))
    reused = _run(c, r2)["result"]["mapping_version_id"]
    edited = _edit(c, reused, lambda d: d.__setitem__("collections", []))
    assert edited["policy"]["sandbox"] is None and edited["policy"]["decision"] != "block"
