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
    monkeypatch.setenv("FORMA_AUTH", "oidc")  # prod never runs on dev auth (ADR-013)
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


def test_duplicate_dataset_history_blocks_upgrade_without_rewriting(isolated_env: Path) -> None:
    from alembic import command
    from sqlalchemy.orm import Session

    from forma.db import migrate
    from forma.db.models import DatasetVersion
    from forma.db.session import get_engine

    engine = get_engine()
    config = migrate.alembic_config(engine.url.render_as_string(hide_password=False))
    command.upgrade(config, "b5d7f9012345")
    with Session(engine) as session:
        session.add_all(
            [
                DatasetVersion(
                    id=f"duplicate-{i}",
                    round_id="round",
                    customer_id="customer",
                    mapping_version_id="mapping",
                    version=2,
                    records_key=f"datasets/{i}.jsonl",
                )
                for i in range(2)
            ]
        )
        session.commit()
    with pytest.raises(RuntimeError, match="conflicts require review"):
        command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM dataset_versions WHERE version = 2")) == 2
    assert migrate.current_revision(engine) == "b5d7f9012345"


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


def test_storage_key_migration_converts_legacy_paths(isolated_env: Path) -> None:
    """Rows written before ADR-012 hold absolute paths under the data dir; 0002 turns them into keys."""
    from alembic import command

    from forma.core.settings import get_settings
    from forma.db import migrate
    from forma.db.session import get_engine

    eng = get_engine()
    cfg = migrate.alembic_config(eng.url.render_as_string(hide_password=False))
    command.upgrade(cfg, "06c905910cbb")
    root = get_settings().data_path
    legacy = root / "files" / "ab" / "abcd" / "x.xlsx"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"x")
    with eng.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO customers (id, code, name, active, created_at) VALUES ('cus_1', 'L', 'l', 1, '2026-01-01')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO import_rounds (id, customer_id, status, target_schema, summary, created_at, updated_at) "
                "VALUES ('rnd_1', 'cus_1', 'uploaded', 'product', '{}', '2026-01-01', '2026-01-01')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO source_files (id, round_id, customer_id, sha256, original_name, size, kind, storage_path, status, created_at) "
                "VALUES ('fil_1', 'rnd_1', 'cus_1', 'abcd', 'x.xlsx', 1, 'excel', :p, 'stored', '2026-01-01')"
            ),
            {"p": str(legacy)},
        )
        conn.execute(
            text(
                "INSERT INTO source_files (id, round_id, customer_id, sha256, original_name, size, kind, storage_path, status, created_at) "
                "VALUES ('fil_2', 'rnd_1', 'cus_1', 'ef01', 'y.xlsx', 1, 'excel', 'files/ef/ef01/y.xlsx', 'stored', '2026-01-01')"
            )
        )
    command.upgrade(cfg, "head")
    with eng.connect() as conn:
        rows = dict(conn.execute(text("SELECT id, storage_key FROM source_files")).fetchall())
    assert rows["fil_1"] == "files/ab/abcd/x.xlsx"
    assert rows["fil_2"] == "files/ef/ef01/y.xlsx"
    command.downgrade(cfg, "06c905910cbb")
    with eng.connect() as conn:
        assert "storage_path" in [c["name"] for c in inspect(eng).get_columns("source_files")]
