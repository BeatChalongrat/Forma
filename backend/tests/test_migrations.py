"""Alembic lifecycle (ADR-011): dev create_all + stamp, mandatory migrations in prod, legacy dev DBs,
`forma db` commands, UTC datetimes and the model/migration diff being empty."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from typer.testing import CliRunner

runner = CliRunner()


def test_dev_init_creates_schema_and_stamps_head(isolated_env: Path) -> None:
    from forma.db import migrate
    from forma.db.session import get_engine, init_db

    init_db()
    st = migrate.status(get_engine())
    assert st["current"] == st["head"] and st["pending"] is False and st["head"]
    assert "jobs" in inspect(get_engine()).get_table_names()
    # idempotent
    init_db()
    assert migrate.status(get_engine())["pending"] is False


def test_prod_refuses_without_migrations(isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORMA_ENV", "prod")
    from tests.conftest import clear_forma_caches

    clear_forma_caches()
    from forma.db import migrate
    from forma.db.session import get_engine, init_db

    with pytest.raises(migrate.MigrationPending, match="forma db upgrade"):
        init_db()
    # the API lifespan surfaces the same error
    from fastapi.testclient import TestClient

    from forma.api.app import create_app

    with pytest.raises(migrate.MigrationPending), TestClient(create_app()):
        pass
    migrate.upgrade(get_engine())
    init_db()  # now fine
    assert migrate.status(get_engine())["pending"] is False


def test_migrations_upgrade_and_downgrade_from_scratch(isolated_env: Path) -> None:
    from forma.db import migrate
    from forma.db.session import get_engine

    eng = get_engine()
    assert migrate.current_revision(eng) is None
    migrate.upgrade(eng)
    tables = set(inspect(eng).get_table_names())
    assert {"customers", "import_rounds", "jobs", "alembic_version", "llm_calls"} <= tables
    # the indexes the hot queries need (ADR-011)
    idx = {i["name"] for i in inspect(eng).get_indexes("review_tasks")}
    assert "ix_review_status_priority" in idx
    idx = {i["name"] for i in inspect(eng).get_indexes("mapping_versions")}
    assert "ix_mapping_customer_status" in idx
    from alembic import command

    command.downgrade(migrate.alembic_config(eng.url.render_as_string(hide_password=False)), "base")
    assert set(inspect(eng).get_table_names()) <= {"alembic_version"}


def test_models_and_migrations_agree(isolated_env: Path) -> None:
    """Autogenerate against a migrated DB must find nothing — the guard against forgotten migrations."""
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext

    from forma.db import migrate
    from forma.db.models import Base
    from forma.db.session import get_engine

    eng = get_engine()
    migrate.upgrade(eng)
    with eng.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True})
        diff = compare_metadata(ctx, Base.metadata)
    assert diff == [], diff


def test_legacy_dev_db_gets_missing_columns(isolated_env: Path) -> None:
    """A dev DB created by plain create_all before Phase 7 lacks the new job columns; init_db adds them."""
    from forma.core.settings import get_settings
    from forma.db import migrate
    from forma.db.session import get_engine, init_db

    url = get_settings().sqlalchemy_url
    eng = create_engine(url)
    with eng.begin() as conn:
        conn.execute(
            text("CREATE TABLE customers (id VARCHAR(40) PRIMARY KEY, code VARCHAR(64), name VARCHAR(255))")
        )
        conn.execute(
            text(
                "CREATE TABLE jobs (id VARCHAR(40) PRIMARY KEY, kind VARCHAR(64), status VARCHAR(32), payload JSON)"
            )
        )
    eng.dispose()
    init_db()
    cols = {c["name"] for c in inspect(get_engine()).get_columns("jobs")}
    assert {"attempts", "batch_id", "worker", "request_id"} <= cols
    assert migrate.status(get_engine())["pending"] is False


def test_utc_datetimes_round_trip_aware(isolated_env: Path) -> None:
    from forma.db.models import Customer
    from forma.db.session import init_db, session_scope

    init_db()
    with session_scope() as s:
        c = Customer(code="TZ", name="tz")
        s.add(c)
        s.flush()
        cid = c.id
    with session_scope() as s:
        got = s.get(Customer, cid)
        assert got is not None and got.created_at.tzinfo is not None
        assert abs((dt.datetime.now(dt.UTC) - got.created_at).total_seconds()) < 60


def test_cli_db_commands(isolated_env: Path) -> None:
    from forma.cli import app

    res = runner.invoke(app, ["db", "current"])
    assert res.exit_code == 0, res.output
    assert '"pending": true' in res.output
    res = runner.invoke(app, ["db", "upgrade"])
    assert res.exit_code == 0, res.output
    assert '"pending": false' in res.output
    res = runner.invoke(app, ["db", "downgrade", "base"])
    assert res.exit_code == 0, res.output
    assert '"current": null' in res.output
    res = runner.invoke(app, ["db", "stamp", "head"])
    assert res.exit_code == 0, res.output
    assert '"pending": false' in res.output
