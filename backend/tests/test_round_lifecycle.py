"""Closing a round without deleting it: cancel, reopen, move, replace (ADR-052)."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.test_api import _run, _upload, make_workbook
from tests.test_batches import _batch, _by_name, _known_customer, _wait


@pytest.fixture
def client(isolated_env: Path) -> Iterator[TestClient]:
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def _round(c: TestClient, root: Path, code: str) -> tuple[str, str]:
    cust = c.post("/api/customers", json={"code": code, "name": code}).json()["id"]
    rid = c.post("/api/rounds", json={"customer_id": cust, "label": f"{code} delivery"}).json()["id"]
    _upload(c, rid, make_workbook(root / f"{code}.xlsx"))
    _run(c, rid)
    return cust, rid


def _history(c: TestClient, **q: Any) -> list[dict[str, Any]]:
    return c.get("/api/history", params=q).json()["items"]  # type: ignore[no-any-return]


def test_cancel_leaves_every_queue_and_reopen_brings_it_back(client: TestClient, isolated_env: Path) -> None:
    c = client
    _, rid = _round(c, isolated_env, "L1")
    before = c.get(f"/api/rounds/{rid}").json()
    assert before["status"] == "review" and c.get("/api/review").json()
    assert c.post(f"/api/rounds/{rid}/cancel", json={"reason": "  "}).status_code == 422

    r = c.post(f"/api/rounds/{rid}/cancel", json={"reason": "test file, uploaded by mistake"}).json()
    assert r["status"] == "cancelled" and r["closed"]["from"] == "review"
    assert r["closed"]["reason"] == "test file, uploaded by mistake" and r["closed"]["by"]
    assert c.get("/api/review").json() == []  # its question is set aside
    assert [x["id"] for x in _history(c)] == []  # hidden by default
    assert [x["id"] for x in _history(c, status="closed")] == [rid]
    assert c.post(f"/api/rounds/{rid}/cancel", json={"reason": "again"}).status_code == 409
    actions = [a["action"] for a in c.get("/api/audit", params={"entity_id": rid}).json()]
    assert "round.cancel" in actions

    r = c.post(f"/api/rounds/{rid}/reopen").json()
    assert r["status"] == "review" and "closed" not in r
    assert len(c.get("/api/review").json()) == 1
    assert [x["id"] for x in _history(c)] == [rid]


def test_only_a_cancelled_round_reopens(client: TestClient, isolated_env: Path) -> None:
    c = client
    _, rid = _round(c, isolated_env, "L2")
    assert c.post(f"/api/rounds/{rid}/reopen").status_code == 409


def test_move_gives_the_same_file_to_the_right_customer(client: TestClient, isolated_env: Path) -> None:
    c = client
    right = c.post("/api/customers", json={"code": "RIGHT", "name": "Right"}).json()["id"]
    wrong, rid = _round(c, isolated_env, "WRONG")
    assert c.post(f"/api/rounds/{rid}/move", json={"customer_id": wrong, "reason": "x"}).status_code == 422
    assert c.post(f"/api/rounds/{rid}/move", json={"customer_id": "nope", "reason": "x"}).status_code == 404

    new = c.post(
        f"/api/rounds/{rid}/move",
        params={"sync": "true"},
        json={"customer_id": right, "reason": "sent to the wrong mailbox"},
    ).json()
    assert new["customer_id"] == right and new["predecessor_id"] == rid and new["job_id"]
    old = c.get(f"/api/rounds/{rid}").json()
    assert old["status"] == "moved" and old["successor_id"] == new["id"]
    assert old["successor"]["customer_code"] == "RIGHT" and old["closed"]["from"] == "review"
    moved = c.get(f"/api/rounds/{new['id']}").json()
    assert moved["predecessor"]["id"] == rid and moved["status"] != "uploaded"  # the pipeline ran
    assert [f["sha256"] for f in moved["files"]] == [f["sha256"] for f in old["files"]]
    assert moved["files"][0]["duplicate_of"] == old["files"][0]["id"]
    assert all(t["round_id"] != rid for t in c.get("/api/review").json())
    assert c.post(f"/api/rounds/{rid}/reopen").status_code == 409


def test_replace_takes_the_old_rounds_place_in_the_zip(client: TestClient, isolated_env: Path) -> None:
    c = client
    _known_customer(c, isolated_env, "K9")
    same = make_workbook(isolated_env / "same.xlsx").read_bytes()
    view = _batch(c, [("K9 oct.xlsx", same)])
    first = _wait(c, view["id"], "K9 oct.xlsx", "ready")
    assert first["export_id"]
    fixed = make_workbook(isolated_env / "fixed.xlsx", n_products=14).read_bytes()

    assert c.post(f"/api/rounds/{first['round_id']}/replace", data={"reason": "x"}).status_code == 422
    new = c.post(
        f"/api/rounds/{first['round_id']}/replace",
        params={"sync": "true"},
        data={"reason": "two prices corrected"},
        files=[("files", ("K9 oct fixed.xlsx", fixed, "application/octet-stream"))],
    ).json()
    assert new["predecessor_id"] == first["round_id"] and new["label"].endswith("K9 oct.xlsx")
    old = c.get(f"/api/rounds/{first['round_id']}").json()
    assert old["status"] == "superseded" and old["closed"]["from"] == "published"

    item = _wait(c, view["id"], "K9 oct.xlsx", "ready")
    assert item["round_id"] == new["id"]  # the work round's row follows the file
    z = c.get(f"/api/batches/{view['id']}/download")
    with zipfile.ZipFile(io.BytesIO(z.content)) as zf:
        assert zf.namelist() == ["K9 oct.json"]
        assert len(zf.read("K9 oct.json")) > 0
    summary = c.get(f"/api/batches/{view['id']}/summary").json()
    assert summary["counts"] == {"ready": 1} and summary["total"] == 1
    assert c.get(f"/api/batches/{view['id']}").json()["items"][0]["round_status"] != "superseded"


def test_a_cancelled_work_round_file_counts_as_closed(client: TestClient, isolated_env: Path) -> None:
    c = client
    _known_customer(c, isolated_env, "K8")
    view = _batch(c, [("K8 oct.xlsx", make_workbook(isolated_env / "k8.xlsx").read_bytes())])
    item = _wait(c, view["id"], "K8 oct.xlsx", "ready")
    assert (
        c.post(f"/api/rounds/{item['round_id']}/cancel", json={"reason": "duplicate delivery"}).status_code
        == 200
    )
    view = c.get(f"/api/batches/{view['id']}").json()
    item = _by_name(view)["K8 oct.xlsx"]
    assert (
        item["state"] == "closed"
        and item["closed_as"] == "cancelled"
        and item["message"] == "duplicate delivery"
    )
    assert view["counts"] == {"closed": 1} and view["done"] is True
    assert c.get(f"/api/batches/{view['id']}/download").status_code == 409  # nothing current to zip
