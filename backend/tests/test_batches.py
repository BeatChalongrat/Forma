"""Bulk upload (ADR-027): many files at once, each matched to its customer by format, one JSON per file,
published without a person only when the policy's checks all pass."""

from __future__ import annotations

import io
import json
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.test_api import _run, _upload, make_workbook


@pytest.fixture
def client(isolated_env: Path) -> Iterator[TestClient]:
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def _known_customer(c: TestClient, root: Path, code: str) -> str:
    """A customer whose first round is approved and published: its format is now 'known'."""
    cust = c.post("/api/customers", json={"code": code, "name": code}).json()["id"]
    r1 = c.post("/api/rounds", json={"customer_id": cust, "label": "R1"}).json()["id"]
    _upload(c, r1, make_workbook(root / f"{code}_r1.xlsx"))
    mv = _run(c, r1)["result"]["mapping_version_id"]
    assert c.post(f"/api/mappings/{mv}/approve", json={}).status_code == 200
    job = c.post(f"/api/rounds/{r1}/transform", params={"sync": "true"}).json()
    assert c.get(f"/api/jobs/{job['job_id']}").json()["status"] == "done"
    ds1 = c.get(f"/api/rounds/{r1}").json()["datasets"][0]["id"]
    assert c.post(f"/api/datasets/{ds1}/approve", json={"acknowledge_anomalies": True}).status_code == 200
    return str(cust)


def _batch(c: TestClient, files: list[tuple[str, bytes]], **form: Any) -> dict[str, Any]:
    r = c.post(
        "/api/batches",
        params={"sync": "true"},
        files=[("files", (n, b, "application/octet-stream")) for n, b in files],
        data={k: str(v).lower() if isinstance(v, bool) else v for k, v in form.items()},
    )
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


def _by_name(view: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {i["file_name"]: i for i in view["items"]}


def test_work_round_date_append_and_retry(client: TestClient) -> None:
    first = _batch(
        client,
        [("one.csv", _unrelated_csv())],
        label="Monthly delivery",
        work_date="2026-09-15",
        auto_publish=False,
    )
    assert first["work_date"] == "2026-09-15"
    assert first["done"] is True and first["complete"] is False  # waiting for a person is not complete
    url = f"/api/batches/{first['id']}/files?sync=true"
    files = [("files", ("one.csv", _unrelated_csv())), ("files", ("two.csv", _product_csv()))]
    appended = client.post(url, files=files)
    assert appended.status_code == 200, appended.text
    result = appended.json()
    assert result["total"] == 2 and result["added"] == 1 and result["skipped"] == 1
    assert result["auto_publish"] is False and result["work_date"] == "2026-09-15"
    assert result["items"][0]["id"] == first["items"][0]["id"]
    retry = client.post(url, files=files).json()
    assert retry["total"] == 2 and retry["added"] == 0 and retry["skipped"] == 2
    changed = client.post(
        url, files=[("files", ("one.csv", _unrelated_csv().replace(b"4.5", b"5.5")))]
    ).json()
    assert changed["total"] == 3  # changed content is a new immutable file, not an overwrite
    assert (
        client.get("/api/batches", params={"start": "2026-09-15", "end": "2026-09-15"}).json()[0]["id"]
        == first["id"]
    )
    assert client.get("/api/batches", params={"start": "2026-10-01"}).json() == []
    assert client.get("/api/batches", params={"search": "Monthly"}).json()[0]["id"] == first["id"]
    assert client.get("/api/batches", params={"offset": 1}).json() == []
    edited = client.patch(
        f"/api/batches/{first['id']}", json={"label": "Revised label", "work_date": "2026-09-16"}
    )
    assert edited.status_code == 200 and edited.json()["work_date"] == "2026-09-16"
    assert edited.json()["created_at"] == first["created_at"]
    assert edited.json()["items"][0]["id"] == first["items"][0]["id"]


def test_append_scope_validation_and_audit(client: TestClient) -> None:
    from sqlalchemy import select

    from forma.db.models import AuditLog
    from forma.db.session import session_scope

    b = _batch(client, [("one.csv", _unrelated_csv())])
    headers = {"X-Forma-User": "scoped", "X-Forma-Roles": "importer", "X-Forma-Customers": "cus_x"}
    assert (
        client.post(
            f"/api/batches/{b['id']}/files", headers=headers, files=[("files", ("two.csv", _product_csv()))]
        ).status_code
        == 403
    )
    assert (
        client.post("/api/batches/missing/files", files=[("files", ("two.csv", _product_csv()))]).status_code
        == 404
    )
    assert (
        client.post(
            "/api/batches", data={"work_date": "bad-date"}, files=[("files", ("two.csv", _product_csv()))]
        ).status_code
        == 422
    )
    assert client.get("/api/batches", params={"start": "2026-10-02", "end": "2026-10-01"}).status_code == 422
    with session_scope() as s:
        events = list(s.scalars(select(AuditLog).where(AuditLog.action == "batch.append")))
        assert events and events[-1].actor == "operator"


def test_three_hundred_files_across_uploads(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from forma.api import batch_routes

    submitted: list[str] = []
    monkeypatch.setattr(batch_routes, "_process", lambda items, p, **kwargs: submitted.extend(items))

    def files(start: int) -> list[tuple[str, tuple[str, bytes]]]:
        return [("files", (f"file-{i:03}.csv", _unrelated_csv())) for i in range(start, start + 100)]

    first = client.post(
        "/api/batches", data={"label": "300 files", "work_date": "2026-10-01"}, files=files(0)
    )
    assert first.status_code == 201, first.text
    bid = first.json()["id"]
    for start in (100, 200):
        response = client.post(f"/api/batches/{bid}/files", files=files(start))
        assert response.status_code == 200, response.text
    result = client.get(f"/api/batches/{bid}").json()
    assert result["total"] == 300 and len(set(submitted)) == len(submitted) == 300
    retry = client.post(f"/api/batches/{bid}/files", files=files(100)).json()
    assert retry["total"] == 300 and retry["skipped"] == 100 and retry["added"] == 0
    assert len(submitted) == 300  # no duplicate jobs on resend


def test_expected_files_target_is_separate_from_received_and_ready(client: TestClient) -> None:
    b = _batch(client, [("one.csv", _unrelated_csv())], label="Target", expected_files=300)
    assert b["expected_files"] == 300 and b["total"] == 1 and not b["complete"]
    url = f"/api/batches/{b['id']}"
    edited = client.patch(url, json={"expected_files": 180}).json()
    assert edited["expected_files"] == 180 and edited["total"] == 1
    assert edited["label"] == b["label"] and edited["work_date"] is None
    assert edited["created_at"] == b["created_at"]
    assert client.get("/api/batches").json()[0]["expected_files"] == 180
    assert client.patch(url, json={"expected_files": None}).json()["expected_files"] is None
    for value in (-1, 0, 1.5, 1000001):
        assert client.patch(url, json={"expected_files": value}).status_code == 422
    assert (
        client.patch(url, json={"expected_files": 5}, headers={"X-Forma-Customers": "cus_x"}).status_code
        == 403
    )
    assert client.patch(url, json={}).status_code == 422


def _unrelated_csv() -> bytes:
    return b"warehouse_id,bin,temperature_c,checked_on\nW1,A-01,4.5,2026-10-01\nW1,A-02,4.7,2026-10-01\n"


def _product_csv() -> bytes:
    """A product list in a layout no customer has: matched to nobody, but it maps to the target format."""
    rows = ["Item Code,Item Name,Brand,Selling Price,Status"]
    rows += [f"P{i:03d},Product {i},Acme,{i * 10}.50,active" for i in range(1, 9)]
    return "".join(f"{r}\n" for r in rows).encode()


def _wait(c: TestClient, batch_id: str, name: str, state: str, timeout: float = 30) -> dict[str, Any]:
    end = time.monotonic() + timeout
    while True:
        item = _by_name(c.get(f"/api/batches/{batch_id}").json())[name]
        if item["state"] == state or time.monotonic() > end:
            return item
        time.sleep(0.25)


def test_known_files_publish_one_json_each_and_unknown_waits(client: TestClient, isolated_env: Path) -> None:
    c = client
    _known_customer(c, isolated_env, "K1")
    same = make_workbook(isolated_env / "same.xlsx").read_bytes()
    view = _batch(
        c,
        [("K1 oct.xlsx", same), ("mystery.csv", _unrelated_csv()), ("broken.xlsx", b"not a workbook")],
        label="Oct wave",
    )
    items = _by_name(view)
    known = items["K1 oct.xlsx"]
    assert known["match"]["kind"] == "known" and known["match"]["how"] == "exact"
    assert known["customer"]["code"] == "K1" and known["state"] == "ready" and known["export_id"]
    assert known["publish"]["decision"] == "publish" and known["round_status"] == "published"
    assert any("records match the target format" in r for r in known["publish"]["reasons"])
    assert items["mystery.csv"]["state"] == "needs_customer" and items["mystery.csv"]["round_id"] is None
    assert items["broken.xlsx"]["state"] == "failed"
    assert view["counts"] == {"ready": 1, "needs_customer": 1, "failed": 1} and view["total"] == 3

    z = c.get(f"/api/batches/{view['id']}/download")
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(z.content)) as zf:
        assert zf.namelist() == ["K1 oct.json", "_forma/manifest.json"]
        records = json.loads(zf.read("K1 oct.json"))
        manifest = json.loads(zf.read("_forma/manifest.json"))
        assert manifest["received_files"] == 3 and manifest["exported_files"] == 1
        assert manifest["omitted_files"] == 2 and manifest["complete"] is False
        included = [x for x in manifest["files"] if x["included"]]
        assert len(included) == 1 and included[0]["round_id"] == known["round_id"]
        assert included[0]["mapping_version_id"] and included[0]["dataset_version_id"]
        import hashlib

        assert included[0]["sha256"] == hashlib.sha256(zf.read("K1 oct.json")).hexdigest()
        assert all(x["reason"] for x in manifest["files"] if not x["included"])
    assert isinstance(records, list) and len(records) == known["rows"] and records[0]["sku"]
    assert any(b["id"] == view["id"] and b["total"] == 3 for b in c.get("/api/batches").json())


def test_unknown_file_gets_a_customer_then_finishes_after_review(
    client: TestClient, isolated_env: Path
) -> None:
    c = client
    _known_customer(c, isolated_env, "K2")
    view = _batch(c, [("unknown.csv", _product_csv())])
    item = view["items"][0]
    assert item["state"] == "needs_customer"
    assert c.post(f"/api/batches/{view['id']}/assign", json={"item_ids": [item["id"]]}).status_code == 422
    view = c.post(
        f"/api/batches/{view['id']}/assign",
        json={"item_ids": [item["id"]], "new_customer": {"code": "N1", "name": "New one"}, "sync": True},
    ).json()
    item = view["items"][0]
    assert item["customer"]["code"] == "N1" and item["round_id"]
    assert item["state"] == "needs_review" and item["step"] == 2  # a first-time customer's mapping is checked
    again = c.post(f"/api/batches/{view['id']}/assign", json={"item_ids": [item["id"]], "customer_id": "x"})
    assert again.status_code == 409
    # a person approves the mapping: the round finishes on its own (create data → policy → JSON)
    mv = c.get(f"/api/rounds/{item['round_id']}").json()["mappings"][0]["id"]
    assert c.post(f"/api/mappings/{mv}/approve", json={}).status_code == 200
    done = _wait(c, view["id"], "unknown.csv", "ready")
    assert done["state"] == "ready" and done["export_id"], done
    assert any("mapping approved by" in r for r in done["publish"]["reasons"])


def test_the_summary_counts_what_the_full_view_counts(client: TestClient, isolated_env: Path) -> None:
    _known_customer(client, isolated_env, "K1")
    same = make_workbook(isolated_env / "same.xlsx").read_bytes()
    view = _batch(client, [("K1 oct.xlsx", same), ("mystery.csv", _unrelated_csv())])
    brief = client.get(f"/api/batches/{view['id']}/summary").json()
    assert "items" not in brief
    assert {k: brief[k] for k in ("counts", "total", "done", "stuck", "mapped")} == {
        k: view[k] for k in ("counts", "total", "done", "stuck", "mapped")
    }
    assert brief["mapped"] == 1
    assert next(b for b in client.get("/api/batches").json() if b["id"] == view["id"])["mapped"] == 1
    assert client.get("/api/batches/nope/summary").status_code == 404


def test_the_policy_holds_data_back_and_a_person_finishes_it(client: TestClient, isolated_env: Path) -> None:
    c = client
    _known_customer(c, isolated_env, "K3")
    # turn automatic publishing off: the data is created, then waits for a person, with the reason
    policy = c.get("/api/settings/policy").json()["effective"]
    policy["publish"]["enabled"] = False
    assert c.put("/api/settings/policy", json={"policy": policy}).status_code == 200
    view = _batch(c, [("K3.xlsx", make_workbook(isolated_env / "k3.xlsx").read_bytes())])
    item = view["items"][0]
    assert item["state"] == "needs_data_check" and item["step"] == 4
    assert item["publish"]["decision"] == "review" and "turned off" in item["publish"]["reasons"][0]
    assert c.get(f"/api/batches/{view['id']}/download").status_code == 409
    # the person approves the data: its JSON is written at once
    ds = c.get(f"/api/rounds/{item['round_id']}").json()["datasets"][0]["id"]
    assert c.post(f"/api/datasets/{ds}/approve", json={"acknowledge_anomalies": True}).status_code == 200
    item = c.get(f"/api/batches/{view['id']}").json()["items"][0]
    assert item["state"] == "ready" and item["export_id"]
    bad = {**policy, "publish": {**policy["publish"], "max_high_anomalies": -1}}
    assert c.put("/api/settings/policy", json={"policy": bad}).status_code == 422


def test_evaluate_publish_names_every_reason() -> None:
    from forma.ai.policy import PolicyEngine

    eng = PolicyEngine()
    ok = eng.evaluate_publish(
        mapping_approved_by="policy", rows=10, invalid_records=0, error_findings=0, high_anomalies=0
    )
    assert ok.decision == "publish" and ok.reasons[0] == "mapping approved automatically"
    held = eng.evaluate_publish(
        mapping_approved_by=None, rows=0, invalid_records=2, error_findings=1, high_anomalies=3
    )
    assert held.decision == "review" and len(held.reasons) == 5
    lenient = PolicyEngine({"publish": {"max_high_anomalies": 5}})
    assert (
        lenient.evaluate_publish(
            mapping_approved_by="ann", rows=3, invalid_records=0, error_findings=0, high_anomalies=2
        ).decision
        == "publish"
    )


def test_customer_scoped_users_cannot_bulk_upload(client: TestClient) -> None:
    r = client.post(
        "/api/batches",
        files=[("files", ("a.csv", b"a,b\n1,2\n", "text/csv"))],
        headers={"X-Forma-User": "scoped", "X-Forma-Roles": "importer", "X-Forma-Customers": "cus_x"},
    )
    assert r.status_code in (403, 422), r.text


def test_a_customer_code_in_the_file_name_names_or_contradicts(
    client: TestClient, isolated_env: Path
) -> None:
    c = client
    _known_customer(c, isolated_env, "K4")
    c.post("/api/customers", json={"code": "Z9", "name": "Same ERP, other company"})
    same_format = make_workbook(isolated_env / "z9.xlsx").read_bytes()
    view = _batch(
        c,
        [("export_Z9.xlsx", same_format), ("products_Z9.csv", _product_csv()), ("K4-oct.xlsx", same_format)],
    )
    items = _by_name(view)
    clash = items["export_Z9.xlsx"]  # K4's format, but named Z9: the same template, another customer?
    assert clash["state"] == "needs_customer" and "says Z9" in clash["match"]["reason"]
    assert {x["customer_code"] for x in clash["match"]["candidates"]} == {"Z9", "K4"}
    named = items["products_Z9.csv"]  # no approved format fits; the name says Z9
    assert named["match"]["how"] == "file_name" and named["customer"]["code"] == "Z9"
    assert named["state"] == "needs_review"  # Z9's first mapping is checked by a person
    confirmed = items["K4-oct.xlsx"]
    assert confirmed["match"]["how"] == "exact" and confirmed["match"]["named"] is True
    assert confirmed["state"] == "ready"


def test_jobs_are_submitted_off_the_event_loop(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """The arq backend submits with asyncio.run, which fails inside a running loop (the async upload route)."""
    import asyncio

    from forma.jobs import runner

    seen: list[bool] = []

    def fake(*a: Any, **kw: Any) -> tuple[str, list[str]]:
        try:
            asyncio.get_running_loop()
            seen.append(True)
        except RuntimeError:
            seen.append(False)
        return "bat_x", []

    monkeypatch.setattr(runner, "deliver_jobs", fake)
    r = client.post("/api/batches", files=[("files", ("a.csv", _product_csv(), "text/csv"))])
    assert r.status_code == 201 and seen == [False]


def test_queue_full_rolls_back_files_and_jobs(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Admission failure cannot leave an accepted upload without its durable work."""
    from forma.core.errors import QueueFull
    from forma.jobs import runner

    def full(*a: Any, **kw: Any) -> None:
        raise QueueFull("job queue is full")

    monkeypatch.setattr(runner, "_capacity_in", full)
    r = client.post("/api/batches", files=[("files", ("x.csv", _product_csv(), "text/csv"))])
    assert r.status_code == 429
    assert client.get("/api/batches").json() == []
    from sqlalchemy import select

    from forma.db.models import BatchItem, Job
    from forma.db.session import session_scope

    with session_scope() as s:
        assert list(s.scalars(select(Job))) == []
        assert list(s.scalars(select(BatchItem))) == []


def test_same_name_size_and_upload_receipt(client: TestClient) -> None:
    first = _product_csv()
    second = first.replace(b"10.50", b"99.50")
    assert len(first) == len(second)
    files = [("same.csv", first), ("same.csv", second), ("same.csv", first)]
    view = _batch(client, files, upload_key="retry-request")
    assert view["total"] == view["added"] == 2 and view["skipped"] == 1
    retry = _batch(client, files, upload_key="retry-request")
    assert retry["id"] == view["id"] and retry["replayed"] and retry["added"] == 0
    assert len(client.get("/api/batches").json()) == 1
    conflict = client.post(
        "/api/batches", data={"upload_key": "retry-request"}, files=[("files", ("same.csv", second))]
    )
    assert conflict.status_code == 409


def test_dispatch_failure_keeps_durable_work(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import datetime as dt

    from sqlalchemy import select

    from forma.db.models import Job
    from forma.db.session import session_scope
    from forma.jobs import runner

    def down(*a: Any, **kw: Any) -> None:
        raise ConnectionError("broker unavailable")

    monkeypatch.setattr(runner, "dispatch_pending", down)
    r = client.post("/api/batches", files=[("files", ("x.csv", _product_csv()))])
    assert r.status_code == 201, r.text
    with session_scope() as s:
        jobs = list(s.scalars(select(Job)))
        assert len(jobs) == 1 and jobs[0].status == "queued"
        jid = jobs[0].id
        assert jobs[0].batch_id == r.json()["id"]
        jobs[0].created_at = dt.datetime.now(dt.UTC) - dt.timedelta(hours=2)
    assert r.json()["stuck"] == 0
    resumed = client.post(f"/api/batches/{r.json()['id']}/resume").json()
    assert resumed["stuck"] == 0
    with session_scope() as s:
        assert len(list(s.scalars(select(Job)))) == 1
    runner.run_job(jid)
    view = client.get(f"/api/batches/{r.json()['id']}").json()
    assert view["items"][0]["state"] == "needs_customer"


def test_append_assign_capacity_failure_rolls_back(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from forma.core.errors import QueueFull
    from forma.jobs import runner

    b = _batch(client, [("one.csv", _product_csv())])

    def full(*a: Any, **kw: Any) -> None:
        raise QueueFull("job queue is full")

    monkeypatch.setattr(runner, "_capacity_in", full)
    assert (
        client.post(
            f"/api/batches/{b['id']}/files", files=[("files", ("two.csv", _product_csv()))]
        ).status_code
        == 429
    )
    assert client.get(f"/api/batches/{b['id']}").json()["total"] == 1
    r = client.post(
        f"/api/batches/{b['id']}/assign",
        json={"item_ids": [b["items"][0]["id"]], "new_customer": {"code": "ROLLBACK", "name": "Rollback"}},
    )
    assert r.status_code == 429, r.text
    view = client.get(f"/api/batches/{b['id']}").json()
    assert view["items"][0]["state"] == "needs_customer" and view["items"][0]["round_id"] is None
    assert client.get("/api/customers").json() == []


def test_review_and_replacement_capacity_failure_rolls_back(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from forma.core.errors import QueueFull
    from forma.jobs import runner

    b = _batch(client, [("one.csv", _product_csv())])
    b = client.post(
        f"/api/batches/{b['id']}/assign",
        json={
            "item_ids": [b["items"][0]["id"]],
            "new_customer": {"code": "N1", "name": "New"},
            "sync": True,
        },
    ).json()
    rid = b["items"][0]["round_id"]
    before = client.get(f"/api/rounds/{rid}").json()
    mid = before["mappings"][0]["id"]

    def full(*a: Any, **kw: Any) -> None:
        raise QueueFull("job queue is full")

    monkeypatch.setattr(runner, "_capacity_in", full)
    assert client.post(f"/api/mappings/{mid}/approve", json={}).status_code == 429
    assert client.get(f"/api/mappings/{mid}").json()["status"] == "proposed"
    replaced = client.post(
        f"/api/rounds/{rid}/replace",
        data={"reason": "Corrected file"},
        files=[("files", ("fixed.csv", _product_csv()))],
    )
    assert replaced.status_code == 429, replaced.text
    after = client.get(f"/api/rounds/{rid}").json()
    assert after["status"] == before["status"] and after["successor_id"] is None
    assert client.get(f"/api/batches/{b['id']}").json()["items"][0]["round_id"] == rid


def test_resume_attached_legacy_gap_but_not_active_pipeline(client: TestClient) -> None:
    from forma.db.models import ImportRound, Job
    from forma.db.session import session_scope

    b = _batch(client, [("one.csv", _product_csv())])
    b = client.post(
        f"/api/batches/{b['id']}/assign",
        json={
            "item_ids": [b["items"][0]["id"]],
            "new_customer": {"code": "LEGACY", "name": "Legacy"},
            "sync": True,
        },
    ).json()
    rid = b["items"][0]["round_id"]
    with session_scope() as s:
        r = s.get(ImportRound, rid)
        assert r
        r.status = "uploaded"
        job = Job(kind="run_pipeline", round_id=rid, payload={"round_id": rid}, status="running")
        s.add(job)
        s.flush()
        jid = job.id
    assert client.get(f"/api/batches/{b['id']}").json()["stuck"] == 0
    assert client.post(f"/api/batches/{b['id']}/resume?sync=true").json()["stuck"] == 0
    with session_scope() as s:
        s.delete(s.get(Job, jid))
    assert client.get(f"/api/batches/{b['id']}").json()["stuck"] == 1
    resumed = client.post(f"/api/batches/{b['id']}/resume?sync=true").json()
    assert resumed["stuck"] == 0 and resumed["items"][0]["state"] == "needs_review"
