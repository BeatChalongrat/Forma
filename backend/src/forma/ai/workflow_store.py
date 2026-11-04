"""Independent durable graph checkpoints and attempt reservations, outside business transactions.

No credentials enter graph state. PostgreSQL shares state across ARQ workers; development uses a
separate SQLite file to avoid locking the application's transaction. Attempts are reserved BEFORE
HTTP, including failed/unknown responses, and never refunded after a crash.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from sqlalchemy.engine import make_url

from forma.ai.llm import LLMError, Usage
from forma.core.settings import Settings

_setup_lock = threading.Lock()


class AttemptLedger:
    def __init__(self, conn: Any, thread_id: str, maximum: int, postgres: bool, seconds: float = 600) -> None:
        self.conn, self.thread_id, self.maximum, self.postgres = conn, thread_id, maximum, postgres
        self.seconds = seconds

    def reserve(self) -> int:
        mark = "%s" if self.postgres else "?"
        sql = (
            "INSERT INTO forma_mapping_attempts (thread_id, attempts, expires_at) "
            f"VALUES ({mark}, 1, {mark}) "
            "ON CONFLICT (thread_id) DO UPDATE SET attempts = forma_mapping_attempts.attempts + 1 "
            f"WHERE forma_mapping_attempts.attempts < {mark} "
            f"AND forma_mapping_attempts.expires_at > {mark} RETURNING attempts"
        )
        now = time.time()
        row = self.conn.execute(sql, (self.thread_id, now + self.seconds, self.maximum, now)).fetchone()
        if not self.postgres:
            self.conn.commit()
        if row is None:
            raise LLMError("Mapping workflow attempt/time budget exhausted; review the saved evidence")
        return int(row["attempts"] if isinstance(row, dict) else row[0])

    def record(self, attempt: int, usage: Usage) -> None:
        mark = "%s" if self.postgres else "?"
        self.conn.execute(
            "INSERT INTO forma_mapping_calls (thread_id, attempt, usage_json) "
            f"VALUES ({mark}, {mark}, {mark}) "
            "ON CONFLICT (thread_id, attempt) DO NOTHING",
            (self.thread_id, attempt, json.dumps(usage.to_dict())),
        )
        if not self.postgres:
            self.conn.commit()

    def usage(self) -> Usage:
        mark = "%s" if self.postgres else "?"
        total = Usage()
        for row in self.conn.execute(
            f"SELECT usage_json FROM forma_mapping_calls WHERE thread_id = {mark}", (self.thread_id,)
        ).fetchall():
            value = json.loads(row["usage_json"] if isinstance(row, dict) else row[0])
            total.add(Usage(**{k: value[k] for k in Usage.__dataclass_fields__}))
        return total

    def used(self) -> int:
        mark = "%s" if self.postgres else "?"
        row = self.conn.execute(
            f"SELECT attempts FROM forma_mapping_attempts WHERE thread_id = {mark}", (self.thread_id,)
        ).fetchone()
        return int(row["attempts"] if isinstance(row, dict) else row[0]) if row else 0


@contextmanager
def workflow_storage(
    settings: Settings, thread_id: str
) -> Iterator[tuple[BaseCheckpointSaver[Any], AttemptLedger]]:
    url = make_url(settings.database_url)
    ddl = (
        "CREATE TABLE IF NOT EXISTS forma_mapping_attempts "
        "(thread_id TEXT PRIMARY KEY, attempts INTEGER NOT NULL, expires_at DOUBLE PRECISION NOT NULL)"
    )
    calls_ddl = (
        "CREATE TABLE IF NOT EXISTS forma_mapping_calls (thread_id TEXT NOT NULL, attempt INTEGER NOT NULL, "
        "usage_json TEXT NOT NULL, PRIMARY KEY(thread_id, attempt))"
    )
    if url.get_backend_name() == "postgresql":
        import psycopg
        from langgraph.checkpoint.postgres import PostgresSaver
        from psycopg.rows import dict_row

        dsn = url.set(drivername="postgresql").render_as_string(hide_password=False)
        # Separate control connection: checkpoints survive rollback of a MappingVersion transaction.
        with (
            psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as conn,
            psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as ledger_conn,
        ):
            saver = PostgresSaver(conn)
            conn.execute("SELECT pg_advisory_lock(736211934)")
            try:
                saver.setup()
                conn.execute(ddl)
                conn.execute(calls_ddl)
            finally:
                conn.execute("SELECT pg_advisory_unlock(736211934)")
            lock_id = int.from_bytes(hashlib.sha256(thread_id.encode()).digest()[:8], signed=True)
            # The saver writes checkpoints on a background thread in pipeline mode. Sharing that
            # connection with synchronous attempt/usage SQL races the libpq pipeline.
            locked = ledger_conn.execute("SELECT pg_try_advisory_lock(%s) AS locked", (lock_id,)).fetchone()
            if not locked or not locked["locked"]:
                raise LLMError("This mapping workflow is already running")
            try:
                yield (
                    saver,
                    AttemptLedger(
                        ledger_conn,
                        thread_id,
                        settings.mapping_workflow_calls,
                        True,
                        settings.llm_task_budget_s,
                    ),
                )
            finally:
                ledger_conn.execute("SELECT pg_advisory_unlock(%s)", (lock_id,))
    else:
        from langgraph.checkpoint.sqlite import SqliteSaver

        directory = settings.resolve(settings.data_dir)
        directory.mkdir(parents=True, exist_ok=True)
        with (
            _setup_lock,
            sqlite3.connect(directory / "mapping-workflows.sqlite", check_same_thread=False) as conn,
            sqlite3.connect(directory / "mapping-attempts.sqlite", check_same_thread=False) as ledger_conn,
        ):
            ledger_conn.execute(ddl)
            ledger_conn.execute(calls_ddl)
            ledger_conn.commit()
            saver_sqlite = SqliteSaver(conn)
            saver_sqlite.setup()
            yield (
                saver_sqlite,
                AttemptLedger(
                    ledger_conn, thread_id, settings.mapping_workflow_calls, False, settings.llm_task_budget_s
                ),
            )
