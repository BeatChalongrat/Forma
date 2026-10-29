"""Policy calibration from outcomes (ADR-037): the report, the proposals and applying one."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.test_api import _run, _upload, make_workbook

from forma.ai.policy import DEFAULT_POLICY, PolicyEngine, _data_identical
from forma.services import calibration


@pytest.fixture
def client(isolated_env: Path) -> Iterator[TestClient]:
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def _published_customer(c: TestClient, root: Path, code: str) -> str:
    cust = c.post("/api/customers", json={"code": code, "name": code}).json()["id"]
    r1 = c.post("/api/rounds", json={"customer_id": cust}).json()["id"]
    _upload(c, r1, make_workbook(root / f"{code}_r1.xlsx"))
    mv = _run(c, r1)["result"]["mapping_version_id"]
    assert c.post(f"/api/mappings/{mv}/approve", json={}).status_code == 200
    job = c.post(f"/api/rounds/{r1}/transform", params={"sync": "true"}).json()
    assert c.get(f"/api/jobs/{job['job_id']}").json()["status"] == "done"
    ds = c.get(f"/api/rounds/{r1}").json()["datasets"][0]["id"]
    assert c.post(f"/api/datasets/{ds}/approve", json={"acknowledge_anomalies": True}).status_code == 200
    assert c.post(f"/api/datasets/{ds}/export", params={"fmt": "json"}).status_code == 201
    return str(cust)


def _renamed_round(c: TestClient, root: Path, cust: str, code: str) -> dict[str, Any]:
    r2 = c.post("/api/rounds", json={"customer_id": cust}).json()["id"]
    _upload(c, r2, make_workbook(root / f"{code}_r2.xlsx", rename_qty=True))
    _run(c, r2)
    return c.get(f"/api/rounds/{r2}").json()  # type: ignore[no-any-return]


def test_data_identical_rule() -> None:
    same = [{"pointer": "/p", "risk": "critical", "verdict": "same", "pairs": 40}]
    assert _data_identical(same)
    assert not _data_identical(
        [*same, {"pointer": "/q", "risk": "high", "verdict": "different", "pairs": 40}]
    )
    assert not _data_identical([{"pointer": "/p", "risk": "critical", "verdict": "same", "pairs": 2}])
    assert not _data_identical([{"pointer": "/p", "risk": "low", "verdict": "same", "pairs": 40}])
    assert not _data_identical([])


def test_customer_overrides_sit_on_top_of_the_policy() -> None:
    engine = PolicyEngine({"auto_approve": {"max_row_change_ratio": 0.3}})
    assert engine.for_customer(None) is engine
    own = engine.for_customer({"auto_approve": {"max_row_change_ratio": 0.8}})
    assert own.policy["auto_approve"]["max_row_change_ratio"] == 0.8
    assert engine.policy["auto_approve"]["max_row_change_ratio"] == 0.3
    assert own.policy["sandbox"] == engine.policy["sandbox"]


def test_renamed_layouts_accepted_unchanged_become_a_proposal_and_then_automatic(
    client: TestClient, isolated_env: Path
) -> None:
    c = client
    for code in ("K1", "K2", "K3"):
        cust = _published_customer(c, isolated_env, code)
        r2 = _renamed_round(c, isolated_env, cust, code)
        assert r2["status"] == "review", r2["summary"].get("decision")
        v = c.get(f"/api/rounds/{r2['id']}/verdict").json()["verdict"]
        assert v["rules_fired"] == ["rename_only"] and v["outcome"] == "accept", v
        res = c.post(f"/api/rounds/{r2['id']}/verdict/accept", json={"digest": v["digest"]})
        assert res.status_code == 200, res.text
    view = c.get("/api/calibration").json()
    rep = view["report"]
    assert rep["per_rule"]["rename_only"]["n"] == 3 and rep["per_rule"]["rename_only"]["precision"] == 1.0
    assert rep["per_decision"]["review"]["accepted_unchanged"] == 6  # 3 first rounds + 3 renamed layouts
    prop = next(p for p in view["proposals"] if p["id"] == "trust_data_checks")
    assert (
        prop["saves"] == 3
        and prop["scope"] == "policy"
        and prop["current"] is False
        and prop["value"] is True
    )
    # a stale digest is refused; the real one applies the change through the settings service (audited)
    assert c.post("/api/calibration/apply", json={"id": prop["id"], "digest": "0" * 16}).status_code == 422
    applied = c.post("/api/calibration/apply", json={"id": prop["id"], "digest": prop["digest"]})
    assert applied.status_code == 200, applied.text
    policy = c.get("/api/settings/policy").json()["effective"]
    assert policy["sandbox"]["trust_data_checks"] is True
    assert not any(p["id"] == "trust_data_checks" for p in c.get("/api/calibration").json()["proposals"])
    audit = [a for a in c.get("/api/audit").json() if a["action"] == "policy.calibrate"]
    assert audit and audit[0]["details"]["path"] == "sandbox.trust_data_checks"
    # the fourth customer's renamed layout now passes without a person
    cust = _published_customer(c, isolated_env, "K4")
    r2 = _renamed_round(c, isolated_env, cust, "K4")
    mv = c.get(f"/api/mappings/{r2['summary']['mapping_version_id']}").json()
    assert r2["summary"]["decision"] == "auto_approve", mv["policy"]["reasons"]
    assert mv["approved_by"] == "policy" and r2["status"] in ("auto_approved", "transformed")
    assert mv["policy"]["checks"]["data_checks_trusted"] is True
    assert mv["policy"]["verdict"]["rules_fired"] == ["clean"]
    # and its outcome is tracked as an automatic decision
    rep2 = c.get("/api/calibration").json()["report"]
    assert rep2["per_decision"]["auto"]["n"] >= 1 and rep2["per_decision"]["auto"].get("auto_ok", 0) >= 1


def test_customer_row_change_proposal_from_outcomes(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = client
    cust = c.post("/api/customers", json={"code": "R1", "name": "R1"}).json()["id"]
    rows = [
        {
            "mapping_id": f"map_{i}",
            "round_id": None,
            "customer_id": cust,
            "decision": "review",
            "policy_decision": "review",
            "rules": ["row_shift"],
            "reasons": [],
            "drift": None,
            "row_change": 0.45,
            "low_high": [],
            "outcome": "accepted_unchanged",
            "at": None,
        }
        for i in range(2)
    ]
    monkeypatch.setattr(calibration, "outcomes", lambda session, since=None: rows)
    view = c.get("/api/calibration").json()
    prop = next(p for p in view["proposals"] if p["id"] == f"row_change:{cust}")
    assert (
        prop["scope"] == "customer"
        and prop["customer_code"] == "R1"
        and prop["value"] == 0.55
        and prop["current"] == 0.3
    )
    assert (
        c.post("/api/calibration/apply", json={"id": prop["id"], "digest": prop["digest"]}).status_code == 200
    )
    me = c.get(f"/api/customers/{cust}").json()
    assert me["policy_overrides"] == {"auto_approve": {"max_row_change_ratio": 0.55}}
    # invalid overrides are refused through the customer API; valid ones are accepted
    assert c.patch(f"/api/customers/{cust}", json={"policy_overrides": {"nope": 1}}).status_code == 422
    assert c.patch(f"/api/customers/{cust}", json={"policy_overrides": None}).status_code == 200
    assert c.get(f"/api/customers/{cust}").json()["policy_overrides"] is None
    # a corrected outcome blocks the proposal
    rows.append({**rows[0], "outcome": "corrected"})
    assert not any(p["id"].startswith("row_change") for p in c.get("/api/calibration").json()["proposals"])
    assert DEFAULT_POLICY["sandbox"]["trust_data_checks"] is False
