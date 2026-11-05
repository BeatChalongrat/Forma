"""Run inside an owned repro API container; restore only into a NEW database and bucket."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from forma.core.settings import get_settings
from forma.db.models import Base, DatasetVersion, ExportArtifact, ImportRound
from forma.db.session import _factory, get_engine, session_scope
from forma.services.backup import create_backup, restore_backup
from forma.services.context import get_context
from forma.services.datasets import records_of
from forma.storage import S3Storage
from sqlalchemy import text


def run(dataset_id: str, export_id: str, expected_hash: str) -> dict:
    settings = get_settings()
    ctx = get_context()
    engine = get_engine()
    # No generic restore target or external credentials are accepted by this check.
    assert settings.llm_mock_only and ctx.llm.is_mock
    assert settings.s3_bucket == "forma-repro"
    assert engine.url.host == "postgres" and engine.url.database == "forma"
    from forma.ai.llm import Usage
    from forma.ai.workflow_store import workflow_storage
    from langgraph.graph import END, START, StateGraph

    thread_id = "recovery-probe-" + uuid.uuid4().hex
    graph_config = {"configurable": {"thread_id": thread_id}}
    with workflow_storage(settings, thread_id) as (saver, ledger):
        graph = StateGraph(dict)
        graph.add_node("check", lambda state: {**state, "checked": True})
        graph.add_edge(START, "check")
        graph.add_edge("check", END)
        compiled = graph.compile(checkpointer=saver)
        compiled.invoke({"dataset_id": dataset_id}, graph_config)
        ledger.record(ledger.reserve(), Usage())
    runtime_tables = {
        "checkpoints",
        "checkpoint_blobs",
        "checkpoint_writes",
        "checkpoint_migrations",
        "forma_mapping_attempts",
        "forma_mapping_calls",
    }
    # The checkpoint store owns these tables and migrations separately from SQLAlchemy models.
    with engine.connect() as connection:
        diff = compare_metadata(
            MigrationContext.configure(
                connection,
                opts={
                    "include_object": lambda obj, name, type_, reflected, compare_to: (
                        not (type_ == "table" and name in runtime_tables)
                    )
                },
            ),
            Base.metadata,
        )
        assert not diff, diff
        runtime_counts = {
            name: connection.scalar(text(f'SELECT COUNT(*) FROM "{name}"'))
            for name in runtime_tables
        }
    backup = create_backup(
        ctx, Path("/data/recovery-check"), include_objects=True, archive=True
    )
    target = "restore_" + uuid.uuid4().hex[:12]
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text(f'CREATE DATABASE "{target}"'))
    target_url = engine.url.set(database=target).render_as_string(hide_password=False)
    engine.dispose()
    _factory.cache_clear()
    get_engine.cache_clear()
    get_context.cache_clear()
    os.environ["FORMA_DATABASE_URL"] = target_url
    os.environ["FORMA_S3_BUCKET"] = "forma-repro-" + target.replace("_", "-")
    os.environ["FORMA_DATA_DIR"] = "/data/" + target
    get_settings.cache_clear()
    restored_ctx = get_context()
    assert isinstance(restored_ctx.storage, S3Storage)
    restored_ctx.storage.ensure_bucket()
    result = restore_backup(restored_ctx, Path(backup["path"]), verify=True)
    with get_engine().connect() as connection:
        for name, count in runtime_counts.items():
            assert connection.scalar(text(f'SELECT COUNT(*) FROM "{name}"')) == count
    with workflow_storage(get_settings(), thread_id) as (saver, ledger):
        checkpoint = saver.get_tuple(graph_config)
        assert checkpoint and checkpoint.checkpoint["channel_values"]["__root__"] == {
            "dataset_id": dataset_id,
            "checked": True,
        }
        assert ledger.used() == 1 and ledger.usage().cost_usd == 0
    with session_scope() as session:
        dataset = session.get(DatasetVersion, dataset_id)
        export = session.get(ExportArtifact, export_id)
        assert dataset and export
        assert session.get(ImportRound, dataset.round_id).status == "published"
        rows = records_of(restored_ctx, dataset)
        assert len(rows) == 12
        assert (
            next(row for row in rows if row["sku"] == "A001")["product_name"]
            == "Edited in repro"
        )
        data = restored_ctx.storage.get(export.storage_key)
        assert hashlib.sha256(data).hexdigest() == expected_hash
    return {
        "restore": result,
        "restored_rows": len(rows),
        "download_sha256": expected_hash,
        "fresh_database": target,
        "schema_matches_models": True,
        "runtime_table_counts_restored": runtime_counts,
        "checkpoint_and_attempt_ledger_restored": True,
        "cost_usd": 0,
    }


if __name__ == "__main__":
    import sys

    print("RECOVERY_RESULT=" + json.dumps(run(*sys.argv[1:]), ensure_ascii=False))
