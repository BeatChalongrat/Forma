"""The review queue's clock and questions parked for the customer (ADR-053)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from tests.test_api import _run, _upload, make_workbook


@pytest.fixture
def client(isolated_env: Path) -> Iterator[TestClient]:
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def _task(c: TestClient, root: Path, code: str = "Q1") -> dict[str, Any]:
    cust = c.post("/api/customers", json={"code": code, "name": code}).json()["id"]
    rid = c.post("/api/rounds", json={"customer_id": cust, "label": f"{code} delivery"}).json()["id"]
    _upload(c, rid, make_workbook(root / f"{code}.xlsx"))
    _run(c, rid)
    return c.get("/api/review").json()[-1]  # type: ignore[no-any-return]


def _age(task_id: str, days: int) -> None:
    from forma.db.models import ReviewTask
    from forma.db.session import session_scope

    with session_scope() as s:
        t = s.get(ReviewTask, task_id)
        assert t is not None
        t.created_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)


def _set_until(task_id: str, day: dt.date) -> None:
    from forma.db.models import ReviewTask
    from forma.db.session import session_scope

    with session_scope() as s:
        row = s.get(ReviewTask, task_id)
        assert row is not None
        row.waiting_until = day


def _today() -> dt.date:
    return dt.datetime.now(dt.UTC).date()


def _soon(days: int = 7) -> str:
    return (_today() + dt.timedelta(days=days)).isoformat()


def test_a_question_says_how_long_it_has_waited(client: TestClient, isolated_env: Path) -> None:
    c = client
    t = _task(c, isolated_env)
    assert t["age_days"] == 0
    assert c.get("/api/dashboard").json()["review"]["stale"] == 0
    _age(t["id"], 9)
    assert c.get("/api/review").json()[0]["age_days"] == 9
    review = c.get("/api/dashboard").json()["review"]
    assert review["open"] == 1 and review["stale"] == 1 and review["oldest_days"] == 9
    notes = c.get("/api/foresight").json()["attention"]
    assert notes[0]["kind"] == "stale_question" and notes[0]["days"] == 9
    assert "9 days" in notes[0]["text"]["en"]


def test_waiting_for_the_customer_leaves_the_queue_and_comes_back(
    client: TestClient, isolated_env: Path
) -> None:
    c = client
    t = _task(c, isolated_env)
    url = f"/api/review/{t['id']}"
    assert c.post(f"{url}/wait", json={"until": "2020-01-01", "note": "x"}).status_code == 422
    assert c.post(f"{url}/wait", json={"until": _soon(), "note": "   "}).status_code == 422

    r = c.post(f"{url}/wait", json={"until": _soon(), "note": "asked about VAT"}).json()
    assert r["status"] == "waiting" and r["waiting_note"] == "asked about VAT"
    assert c.get("/api/review").json() == []
    assert [x["id"] for x in c.get("/api/review", params={"status": "waiting"}).json()] == [t["id"]]
    dash = c.get("/api/dashboard").json()
    assert dash["open_reviews"] == 0 and dash["review"]["waiting"] == 1
    assert c.post(f"{url}/wait", json={"until": _soon(), "note": "again"}).status_code == 409

    r = c.post(f"{url}/resume").json()
    assert r["status"] == "open" and r["waiting_until"] is None and r["waiting_note"] is None
    assert c.post(f"{url}/resume").status_code == 409
    actions = {a["action"] for a in c.get("/api/audit", params={"entity_id": t["id"]}).json()}
    assert {"review.wait", "review.resume"} <= actions


def test_a_waiting_question_returns_by_itself_on_its_day(client: TestClient, isolated_env: Path) -> None:
    c = client
    t = _task(c, isolated_env)
    c.post(f"/api/review/{t['id']}/wait", json={"until": _soon(3), "note": "asked about VAT"})
    assert c.get("/api/review").json() == []
    _set_until(t["id"], _today())
    assert [x["id"] for x in c.get("/api/review").json()] == [t["id"]]
    assert c.get("/api/dashboard").json()["review"]["waiting"] == 0
    log = c.get("/api/audit", params={"entity_id": t["id"]}).json()
    assert [a["actor"] for a in log if a["action"] == "review.resume"] == ["clock"]


def test_approving_the_mapping_settles_a_waiting_question(client: TestClient, isolated_env: Path) -> None:
    c = client
    t = _task(c, isolated_env)
    c.post(f"/api/review/{t['id']}/wait", json={"until": _soon(), "note": "asked about VAT"})
    assert c.post(f"/api/mappings/{t['mapping_version_id']}/approve", json={}).status_code == 200
    assert c.get("/api/review", params={"status": "waiting"}).json() == []
    assert [x["id"] for x in c.get("/api/review", params={"status": "approved"}).json()] == [t["id"]]


def test_cancelling_a_round_settles_a_waiting_question(client: TestClient, isolated_env: Path) -> None:
    c = client
    t = _task(c, isolated_env)
    c.post(f"/api/review/{t['id']}/wait", json={"until": _soon(), "note": "asked about VAT"})
    assert c.post(f"/api/rounds/{t['round_id']}/cancel", json={"reason": "wrong upload"}).status_code == 200
    assert c.get("/api/review", params={"status": "waiting"}).json() == []


def test_a_customer_whose_answer_is_due_is_flagged(client: TestClient, isolated_env: Path) -> None:
    from forma.auth import Principal
    from forma.db.session import session_scope
    from forma.services import review_queue

    c = client
    t = _task(c, isolated_env)
    c.post(f"/api/review/{t['id']}/wait", json={"until": _soon(1), "note": "asked about VAT"})
    assert c.get("/api/foresight").json()["attention"] == []
    _set_until(t["id"], _today() - dt.timedelta(days=2))
    everyone = Principal(subject="t", roles=frozenset({"admin"}))
    with session_scope() as s:
        notes = review_queue.attention(s, everyone)
    assert notes[0]["kind"] == "customer_due" and notes[0]["days"] == 2
    assert "asked about VAT" in notes[0]["text"]["en"]


def test_an_idle_round_is_flagged_and_left_alone(client: TestClient, isolated_env: Path) -> None:
    from forma.db.models import ImportRound
    from forma.db.session import session_scope
    from forma.services import retention
    from forma.services.context import get_context

    c = client
    t = _task(c, isolated_env)
    assert c.get("/api/foresight").json()["idle_rounds"] == []
    with session_scope() as s:
        s.execute(
            update(ImportRound)
            .where(ImportRound.id == t["round_id"])
            .values(updated_at=dt.datetime.now(dt.UTC) - dt.timedelta(days=61))
        )
    idle = c.get("/api/foresight").json()["idle_rounds"]
    assert idle[0]["round_id"] == t["round_id"] and idle[0]["idle_days"] >= 60
    with session_scope() as s:
        retention.run(get_context(), s, apply_=True, actor="test")
    assert c.get(f"/api/rounds/{t['round_id']}").json()["status"] == "review"
