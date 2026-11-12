"""Upgrade an existing, populated previous schema; empty-database migration is not enough."""

from sqlalchemy import MetaData, Table, inspect, select


def test_upgrade_retains_work_and_is_idempotent(isolated_env):
    import datetime as dt

    from alembic import command

    from forma.db import migrate
    from forma.db.session import get_engine

    engine = get_engine()
    config = migrate.alembic_config(engine.url.render_as_string(hide_password=False))
    command.upgrade(config, "d9a012345679")
    now = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    original = isolated_env / "original.csv"
    original.write_bytes(b"sku,price\n001,107.00\n")
    source_bytes = original.read_bytes()
    table = Table("customers", MetaData(), autoload_with=engine)
    rounds = Table("import_rounds", MetaData(), autoload_with=engine)
    sources = Table("source_files", MetaData(), autoload_with=engine)
    mappings = Table("mapping_versions", MetaData(), autoload_with=engine)
    with engine.begin() as conn:
        conn.execute(
            table.insert().values(
                id="upgrade-customer",
                code="UPGRADE",
                name="ทดสอบการอัปเกรด",
                active=True,
                created_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
                notes="Keep original data",
            )
        )
        conn.execute(
            rounds.insert().values(
                id="upgrade-round",
                customer_id="upgrade-customer",
                label="รอบเดิม",
                status="review",
                target_schema="product",
                summary={"rows": 1},
                created_at=now,
                updated_at=now,
            )
        )
        conn.execute(
            sources.insert().values(
                id="upgrade-source",
                round_id="upgrade-round",
                customer_id="upgrade-customer",
                sha256="a" * 64,
                original_name="ข้อมูล.csv",
                size=len(source_bytes),
                kind="csv",
                storage_key="original.csv",
                status="profiled",
                created_at=now,
            )
        )
        conn.execute(
            mappings.insert().values(
                id="upgrade-mapping",
                round_id="upgrade-round",
                customer_id="upgrade-customer",
                version=1,
                status="proposed",
                target_schema="product",
                document={"vat": False},
                created_by="human",
                created_at=now,
                open_questions=["ราคานี้รวมภาษีหรือยัง?"],
                agent_log=[{"stage": "review", "rows": 1}],
            )
        )
        snapshots = [
            (t, dict(conn.execute(select(t)).mappings().one())) for t in (table, rounds, sources, mappings)
        ]
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    with engine.connect() as conn:
        for t, before in snapshots:
            assert dict(conn.execute(select(t)).mappings().one()) == before
    assert original.read_bytes() == source_bytes
    assert {"assistant_runs", "assistant_chat_state", "assistant_feedback"} <= set(
        inspect(engine).get_table_names()
    )
    assert not migrate.status(engine)["pending"]
