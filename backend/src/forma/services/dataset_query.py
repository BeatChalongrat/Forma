"""Disk-backed browsing of immutable JSONL versions; never loads the full dataset into RAM."""

from __future__ import annotations

import contextlib
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from forma.core.hashing import stable_hash
from forma.db.models import DatasetVersion
from forma.engine.transform import _get_pointer
from forma.schema_registry import PROVENANCE_KEY
from forma.services.context import ServiceContext
from forma.services.datasets import iter_records


def _signature(source: Path) -> str:
    stat = source.stat()
    return f"{stat.st_size}:{stat.st_mtime_ns}"


def _valid(index: Path, signature: str) -> bool:
    if not index.is_file():
        return False
    try:
        with contextlib.closing(sqlite3.connect(f"{index.as_uri()}?mode=ro", uri=True)) as db:
            return bool(db.execute("SELECT value FROM metadata").fetchone() == (signature,))
    except sqlite3.Error:
        return False


def _index(ctx: ServiceContext, dv: DatasetVersion) -> Path:
    source = ctx.records_path(dv)
    signature = _signature(source)
    folder = ctx.settings.data_path / "cache" / "dataset_queries"
    folder.mkdir(parents=True, exist_ok=True)
    index = (folder / f"{stable_hash([dv.id, dv.records_key, signature])}.sqlite").resolve()
    if _valid(index, signature):
        return index
    pending = index.with_suffix(f".{uuid.uuid4().hex}.tmp")
    try:
        with contextlib.closing(sqlite3.connect(pending)) as db:
            db.execute("PRAGMA journal_mode=OFF")  # disposable, published only after a complete build
            db.execute("PRAGMA cache_size=-4096")
            db.execute("CREATE TABLE records (seq INTEGER PRIMARY KEY, body TEXT, search TEXT)")
            db.execute("CREATE TABLE metadata (value TEXT NOT NULL)")

            def rows() -> Any:
                for i, record in enumerate(iter_records(source)):
                    body = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
                    searchable = str({k: v for k, v in record.items() if k != PROVENANCE_KEY}).casefold()
                    yield i, body, searchable

            db.executemany("INSERT INTO records VALUES (?, ?, ?)", rows())
            db.execute("INSERT INTO metadata VALUES (?)", (signature,))
            db.commit()
        try:
            pending.replace(index)
        except OSError:
            # Another request may have published and opened the same immutable index on Windows.
            if not _valid(index, signature):
                raise
    finally:
        pending.unlink(missing_ok=True)
    return index


def _sort_value(body: str, pointer: str) -> Any:
    value = _get_pointer(json.loads(body), pointer)
    if value is None or isinstance(value, int | float):
        return value
    if isinstance(value, str):
        return value.casefold()
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def page(
    ctx: ServiceContext,
    dv: DatasetVersion,
    *,
    offset: int,
    limit: int,
    query: str | None = None,
    sort: str | None = None,
    descending: bool = False,
) -> tuple[int, list[dict[str, Any]]]:
    index = _index(ctx, dv)
    with contextlib.closing(sqlite3.connect(f"{index.as_uri()}?mode=ro", uri=True)) as db:
        db.execute("PRAGMA cache_size=-4096")
        db.execute("PRAGMA temp_store=FILE")
        db.create_function("sort_value", 2, _sort_value, deterministic=True)
        where = "WHERE instr(search, ?) > 0" if query else ""
        args: list[Any] = [query.casefold()] if query else []
        total = db.execute(f"SELECT count(*) FROM records {where}", args).fetchone()[0]
        order = "seq ASC"
        if sort:
            order = f"sort_value(body, ?) IS NULL ASC, sort_value(body, ?) {'DESC' if descending else 'ASC'}, seq ASC"
            args += [sort, sort]
        result = db.execute(
            f"SELECT body FROM records {where} ORDER BY {order} LIMIT ? OFFSET ?", [*args, limit, offset]
        )
        return total, [json.loads(row[0]) for row in result]
