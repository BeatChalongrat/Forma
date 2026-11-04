"""A file correction preserves sibling sources and invalidates the old delivery's output."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from forma.auth import Principal, current_principal
from forma.db.models import Batch, BatchItem, ImportRound, Job
from forma.db.session import session_scope


@pytest.fixture
def client(isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from forma.api.app import create_app

    # Check API orchestration without spending tokens or letting workers race the assertions.
    def enqueue(kind: str, payload: dict[str, Any], **kwargs: Any) -> str:
        assert kind == "run_pipeline" and payload["round_id"] == kwargs["round_id"]
        return f"job_{payload['round_id']}"

    monkeypatch.setattr("forma.api.routes.runner.enqueue", enqueue)
    monkeypatch.setattr("forma.api.routes.runner.deliver_jobs", lambda *a, **kw: None)
    with TestClient(create_app()) as c:
        yield c


def delivery(c: TestClient, *, count: int = 2) -> tuple[str, str, list[dict[str, Any]]]:
    cid = c.post("/api/customers", json={"code": "FILES", "name": "Files"}).json()["id"]
    rid = c.post("/api/rounds", json={"customer_id": cid, "label": "May"}).json()["id"]
    for index in range(count):
        response = c.post(
            f"/api/rounds/{rid}/files",
            files=[("files", (f"source-{index}.csv", f"sku,qty\n001,{index + 1}\n".encode(), "text/csv"))],
        )
        assert response.status_code == 201
    with session_scope() as s:
        r = s.get(ImportRound, rid)
        assert r is not None
        r.notes = "Keep leading zeros. Ask if the unit is unclear."
    return cid, rid, c.get(f"/api/rounds/{rid}").json()["files"]


@pytest.mark.parametrize("action", ["replace", "move", "cancel"])
def test_one_file_change_keeps_siblings_and_history(client: TestClient, action: str) -> None:
    c = client
    cid, rid, files = delivery(c)
    target = c.post("/api/customers", json={"code": "RIGHT", "name": "Right"}).json()["id"]
    url = f"/api/rounds/{rid}/files/{files[0]['id']}/{action}"
    if action == "replace":
        response = c.post(
            url,
            data={"reason": "corrected quantity"},
            files={"file": ("fixed.csv", b"sku,qty\n001,50\n", "text/csv")},
        )
    else:
        response = c.post(url, json={"reason": "wrong file", "customer_id": target})
    assert response.status_code in (200, 201), response.text
    out = response.json()
    old = c.get(f"/api/rounds/{rid}").json()
    assert old["status"] == "superseded"
    assert [(f["id"], f["sha256"]) for f in old["files"]] == [(f["id"], f["sha256"]) for f in files]
    remaining = c.get(f"/api/rounds/{old['successor_id']}").json()
    assert remaining["customer_id"] == cid
    names = {f["name"] for f in remaining["files"]}
    assert names == ({"source-1.csv", "fixed.csv"} if action == "replace" else {"source-1.csv"})
    assert next(f for f in remaining["files"] if f["name"] == "source-1.csv")["sha256"] == files[1]["sha256"]
    with session_scope() as s:
        kept = s.get(ImportRound, remaining["id"])
        assert kept is not None and kept.notes == "Keep leading zeros. Ask if the unit is unclear."
    if action == "move":
        moved = c.get(f"/api/rounds/{out['id']}").json()
        assert moved["customer_id"] == target and len(moved["files"]) == 1
        assert moved["files"][0]["sha256"] == files[0]["sha256"]
    assert len(out["job_ids"]) == (2 if action == "move" else 1)
    actions = c.get("/api/audit", params={"entity_id": rid}).json()
    assert any(a["action"] == f"file.{action}" for a in actions)
    if action == "cancel":
        event = next(a for a in actions if a["action"] == "file.cancel")
        detached = c.get(f"/api/rounds/{event['details']['detached_round_id']}").json()
        assert detached["status"] == "cancelled"
        assert detached["files"][0]["sha256"] == files[0]["sha256"]
    assert c.post(url, json={"reason": "again", "customer_id": target}).status_code in (409, 422)


def test_last_file_cancellation_can_be_reopened(client: TestClient) -> None:
    c = client
    _, rid, files = delivery(c, count=1)
    result = c.post(f"/api/rounds/{rid}/files/{files[0]['id']}/cancel", json={"reason": "wrong upload"})
    assert result.status_code == 200 and result.json()["status"] == "cancelled"
    assert result.json()["job_ids"] == []
    assert c.post(f"/api/rounds/{rid}/reopen").status_code == 200


def test_invalid_replacement_rolls_back_and_active_job_blocks_changes(client: TestClient) -> None:
    c = client
    _, rid, files = delivery(c)
    url = f"/api/rounds/{rid}/files/{files[0]['id']}"
    bad = c.post(
        url + "/replace", data={"reason": "bad"}, files={"file": ("bad.png", b"not a file", "image/png")}
    )
    assert bad.status_code in (415, 422)
    assert c.get(f"/api/rounds/{rid}").json()["status"] == "uploaded"
    with session_scope() as s:
        s.add(Job(round_id=rid, kind="run_pipeline", status="running"))
    assert c.post(url + "/cancel", json={"reason": "x"}).status_code == 409
    assert len(c.get(f"/api/rounds/{rid}").json()["files"]) == 2


def test_file_membership_and_customer_scope_are_enforced(client: TestClient) -> None:
    c = client
    cid, rid, files = delivery(c)
    assert c.post(f"/api/rounds/{rid}/files/missing/cancel", json={"reason": "x"}).status_code == 404
    c.app.dependency_overrides[current_principal] = lambda: Principal(  # type: ignore[union-attr]
        subject="restricted", roles=frozenset({"importer"}), customers=frozenset({cid})
    )
    assert (
        c.post(
            f"/api/rounds/{rid}/files/{files[0]['id']}/move", json={"reason": "x", "customer_id": "other"}
        ).status_code
        == 403
    )
    c.app.dependency_overrides[current_principal] = lambda: Principal(subject="viewer")  # type: ignore[union-attr]
    assert c.post(f"/api/rounds/{rid}/files/{files[0]['id']}/cancel", json={"reason": "x"}).status_code == 403


def test_batch_row_follows_replacement_but_keeps_original_receipt(client: TestClient) -> None:
    c = client
    cid, rid, files = delivery(c, count=1)
    from forma.db.models import SourceFile

    with session_scope() as s:
        f = s.get(SourceFile, files[0]["id"])
        assert f is not None
        b = Batch(label="May", created_by="operator")
        s.add(b)
        s.flush()
        bid = b.id
        s.add(
            BatchItem(
                batch_id=bid,
                round_id=rid,
                customer_id=cid,
                file_name=f.original_name,
                sha256=f.sha256,
                size=f.size,
                kind=f.kind,
                storage_key=f.storage_key,
                status="attached",
            )
        )
    response = c.post(
        f"/api/rounds/{rid}/files/{files[0]['id']}/replace",
        data={"reason": "corrected"},
        files={"file": ("fixed.csv", b"sku,qty\n001,99\n", "text/csv")},
    )
    assert response.status_code == 201
    view = c.get(f"/api/batches/{bid}").json()
    assert view["total"] == 1
    row = view["items"][0]
    assert row["file_name"] == "source-0.csv" and row["current_file_name"] == "fixed.csv"
    assert row["round_id"] == response.json()["id"] and row["source_file_id"] != files[0]["id"]
