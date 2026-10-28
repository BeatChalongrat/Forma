"""Real PostgreSQL run (ADR-011). Skipped unless ``FORMA_TEST_PG_URL`` is set, e.g.

    docker compose up -d postgres
    FORMA_TEST_PG_URL=postgresql+psycopg://forma:forma@localhost:5432/forma pytest tests/test_postgres.py

The whole API flow runs on Postgres with migrations applied by Alembic (no create_all), JSON columns
are JSONB, datetimes come back timezone-aware and the review/job queries use their indexes.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from tests.test_api import FILE_NAME, _run, _upload, make_workbook

PG_URL = os.environ.get("FORMA_TEST_PG_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="FORMA_TEST_PG_URL not set (needs a running Postgres)")


@pytest.fixture
def pg_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    assert PG_URL
    eng = create_engine(PG_URL)
    with eng.begin() as conn:  # fresh schema for every test
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    eng.dispose()
    monkeypatch.setenv("FORMA_DATABASE_URL", PG_URL)
    monkeypatch.setenv("FORMA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FORMA_LLM_PROVIDER", "mock")
    monkeypatch.setenv("FORMA_ENV", "prod")
    from tests.conftest import clear_forma_caches

    clear_forma_caches()
    yield tmp_path
    clear_forma_caches()


def test_full_flow_on_postgres(pg_env: Path) -> None:
    from forma.db import migrate
    from forma.db.session import get_engine

    migrate.upgrade(get_engine())
    insp = inspect(get_engine())
    cols = {c["name"]: c for c in insp.get_columns("jobs")}
    assert str(cols["payload"]["type"]).upper() == "JSONB"
    assert {i["name"] for i in insp.get_indexes("review_tasks")} >= {"ix_review_status_priority"}

    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        cust = c.post("/api/customers", json={"code": "PG1", "name": "pg"}).json()
        rid = c.post("/api/rounds", json={"customer_id": cust["id"]}).json()["id"]
        _upload(c, rid, make_workbook(pg_env / FILE_NAME))
        job = _run(c, rid)
        assert job["result"]["mapping_version_id"]
        r = c.get(f"/api/rounds/{rid}").json()
        assert r["status"] in ("review", "auto_approved", "transformed")
        assert r["created_at"].endswith("+00:00") or "Z" in r["created_at"]
        mv = r["mappings"][0]["id"]
        c.post(f"/api/mappings/{mv}/approve", json={"actor": "pg.tester"})
        job = c.post(f"/api/rounds/{rid}/transform?sync=true").json()["job_id"]
        j = c.get(f"/api/jobs/{job}").json()
        assert j["status"] == "done", j["error"]
        ds = j["result"]["dataset_version_id"]
        rows = c.get(f"/api/datasets/{ds}/rows?limit=5").json()
        assert rows["total"] == 12
        exp = c.post(f"/api/datasets/{ds}/export?fmt=csv").json()
        dl = c.get(f"/api/exports/{exp['id']}/download")
        assert dl.status_code == 200 and dl.content.startswith("﻿".encode())
        assert c.get("/api/review?limit=5&offset=0").status_code == 200
        assert c.get("/api/jobs/stats").json()["by_status"]["done"] >= 2
        dash = c.get("/api/dashboard").json()
        assert dash["published_rows"] == 12


def test_stale_recovery_on_postgres(pg_env: Path) -> None:
    import datetime as dt

    from forma.db import migrate
    from forma.db.models import Job
    from forma.db.session import get_engine, init_db, session_scope
    from forma.jobs import handlers as _h  # noqa: F401
    from forma.jobs import runner

    migrate.upgrade(get_engine())
    init_db()
    with session_scope() as s:
        j = Job(kind="profile_round", payload={"round_id": "rnd_missing"}, status="running", attempts=1)
        j.started_at = dt.datetime.now(dt.UTC) - dt.timedelta(hours=3)
        s.add(j)
        s.flush()
        jid = j.id
    assert runner.recover_stale() == [jid]
    import time

    for _ in range(100):
        st = runner.job_status(jid)
        if st["status"] in ("done", "failed"):
            break
        time.sleep(0.1)
    assert st["status"] == "failed" and st["attempts"] == 2  # round does not exist → readable failure
