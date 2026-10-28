"""Schema conformance gate (ADR-026): a record that breaks the target contract is never approved or exported,
and fixing invalid records one at a time is allowed."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import openpyxl
import pytest
from fastapi.testclient import TestClient
from tests.test_lineage import published_round


@pytest.fixture
def client(isolated_env: Path) -> Iterator[TestClient]:
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def _blank_names(rows: tuple[int, ...]) -> Any:
    def change(wb: openpyxl.Workbook) -> None:
        ws = wb["สินค้า"]
        for r in rows:
            ws.cell(row=r, column=2).value = None  # product name is required by the contract

    return change


def _invalid_dataset(c: TestClient, root: Path, code: str) -> tuple[str, list[str], str]:
    _, ds = published_round(c, root, code, _blank_names((4, 6, 8)))
    d = c.get(f"/api/datasets/{ds}").json()
    bad = sorted({f["identity"] for f in d["findings"] if f["kind"] == "validation" and f["identity"]})
    cols = c.get(f"/api/datasets/{ds}/rows", params={"limit": 1}).json()["columns"]
    name = next(x["pointer"] for x in cols if x.get("semantic") == "product_name")
    return ds, bad, name


def _identity(page: dict[str, Any]) -> Any:
    keys = [k.lstrip("/") for k in page["identity_keys"]]
    return lambda row: "|".join(str(row.get(k)) for k in keys)


def _fix(c: TestClient, ds: str, name: str, identity: str, value: Any = "fixed") -> Any:
    op = {"kind": "set_cell", "identity": identity, "target": name, "value": value}
    return c.post(f"/api/datasets/{ds}/changes", json={"operations": [op], "note": "fill the name"})


def test_invalid_records_block_approval_and_export(client: TestClient, isolated_env: Path) -> None:
    ds, bad, _ = _invalid_dataset(client, isolated_env, "SG1")
    d = client.get(f"/api/datasets/{ds}").json()
    assert d["invalid_records"] == 3 and len(bad) == 3
    held = client.post(f"/api/datasets/{ds}/approve", json={"acknowledge_anomalies": True})
    assert held.status_code == 409
    assert held.json()["details"]["invalid_records"] == 3 and held.json()["details"]["errors"]
    assert client.post(f"/api/datasets/{ds}/export", params={"fmt": "json"}).status_code == 409


def test_fix_one_row_at_a_time_then_approve(client: TestClient, isolated_env: Path) -> None:
    ds, bad, name = _invalid_dataset(client, isolated_env, "SG2")
    # one fix of three: allowed, although two records still break the contract
    preview = client.post(
        f"/api/datasets/{ds}/preview-changes",
        json={"operations": [{"kind": "set_cell", "identity": bad[0], "target": name, "value": "ok"}]},
    ).json()
    assert preview["validation_errors"] == [] and preview["invalid_before"] == 3
    assert preview["invalid_after"] == 2
    r = _fix(client, ds, name, bad[0])
    assert r.status_code == 201, r.text
    v2 = r.json()["dataset"]
    assert v2["invalid_records"] == 2
    left = {f["identity"] for f in v2["findings"] if f["kind"] == "validation"}
    assert left == set(bad[1:])  # the findings follow the records, not the version it came from
    # an edit that breaks a record which was valid is still refused
    page = client.get(f"/api/datasets/{v2['id']}/rows", params={"limit": 50}).json()
    valid = next(i for i in map(_identity(page), page["rows"]) if i not in bad)
    broken = _fix(client, v2["id"], name, valid, None)
    assert broken.status_code == 422 and broken.json()["details"]["errors"]
    assert client.post(f"/api/datasets/{v2['id']}/approve", json={}).status_code == 409
    # removing a record is a fix too
    v3 = _fix(client, v2["id"], name, bad[1]).json()["dataset"]
    rm = client.post(
        f"/api/datasets/{v3['id']}/changes", json={"operations": [{"kind": "delete_row", "identity": bad[2]}]}
    )
    v4 = rm.json()["dataset"]
    assert v4["invalid_records"] == 0 and not [f for f in v4["findings"] if f["kind"] == "validation"]
    ok = client.post(f"/api/datasets/{v4['id']}/approve", json={"acknowledge_anomalies": True})
    assert ok.status_code == 200, ok.text
    art = client.post(f"/api/datasets/{v4['id']}/export", params={"fmt": "json"})
    assert art.status_code == 201


def test_legacy_versions_count_their_validation_findings() -> None:
    from forma.db.models import DatasetVersion
    from forma.services.datasets import invalid_records

    dv = DatasetVersion(
        stats={},
        findings=[
            {"kind": "validation", "severity": "error", "message": "x", "row": 1},
            {"kind": "validation", "severity": "error", "message": "... 41 more schema validation errors"},
            {"kind": "dedupe", "severity": "error", "message": "dup", "row": 2},
        ],
    )
    assert invalid_records(dv) == 42
    assert invalid_records(DatasetVersion(stats={"invalid_records": 0}, findings=dv.findings)) == 0


def test_overrides_are_validated_after_they_apply(client: TestClient, isolated_env: Path) -> None:
    """A persistent override that no longer fits the contract shows as an invalid record next round."""
    from forma.db.models import Override
    from forma.db.session import session_scope

    cust, ds = published_round(client, isolated_env, "SG3")
    rows = client.get(f"/api/datasets/{ds}/rows", params={"limit": 1}).json()
    ident = _identity(rows)(rows["rows"][0])
    name = next(x["pointer"] for x in rows["columns"] if x.get("semantic") == "product_name")
    with session_scope() as s:
        s.add(Override(customer_id=cust, target=name, where={"identity": ident}, value=None, author="t"))
    rnd = client.get(f"/api/datasets/{ds}").json()["round_id"]
    job = client.post(f"/api/rounds/{rnd}/transform", params={"sync": "true"}).json()
    assert client.get(f"/api/jobs/{job['job_id']}").json()["status"] == "done"
    latest = client.get(f"/api/rounds/{rnd}").json()["datasets"][0]["id"]
    assert client.get(f"/api/datasets/{latest}").json()["invalid_records"] == 1
