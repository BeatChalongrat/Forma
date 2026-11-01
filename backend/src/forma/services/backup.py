"""Backup / restore (ADR-015, docs/RUNBOOK.md).

A backup is a directory (or tar.gz) with:
  * ``db.sql`` / ``db.sqlite`` — a logical dump (``pg_dump`` on Postgres, the SQLite backup API locally),
  * ``manifest.json`` — every storage key with size + sha256, the config (secrets masked) and the schema
    revision,
  * ``objects/`` — the storage objects themselves unless ``--no-objects``, which only a bucket replicated
    somewhere else can afford (ADR-054).

Only a finished backup carries the name ``forma-backup-<stamp>``. Until then it is a hidden partial, so a
backup killed half way is never mistaken for one, and nothing old is pruned because of it.

Restore replays the dump into an *empty* database and uploads the objects the manifest lists.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from forma.core.logging import get_logger
from forma.core.settings import get_settings
from forma.services.context import ServiceContext
from forma.storage import ObjectInfo

log = get_logger(__name__)
PREFIXES = ("files/", "datasets/", "exports/", "assistant/", "sandbox/")
PARTIAL_MAX_AGE_S = 24 * 3600  # a partial this old belongs to a run that was killed
DUMP_HEADER_LINES = 40
NEWER_THAN_THE_SERVER = b"SET transaction_timeout = "


def _fs(p: Path) -> Path:
    """Filesystem view of a path: on Windows the ``\\\\?\\`` prefix lifts the 260-char MAX_PATH limit
    (backup trees nest keys under long temp/backup directories)."""
    if os.name == "nt":
        s = str(p.resolve())
        if not s.startswith("\\\\?\\"):
            return Path("\\\\?\\" + s)
    return p


def _sha256_stream(fh: Any) -> str:
    h = hashlib.sha256()
    for chunk in iter(lambda: fh.read(1024 * 1024), b""):
        h.update(chunk)
    return h.hexdigest()


def _objects(ctx: ServiceContext) -> Iterator[ObjectInfo]:
    """A bucket holds nothing but objects, so it is taken whole and a new kind of object cannot be missed.
    The local data directory also holds the database and the backups, so there the kinds are named."""
    if ctx.storage.name != "local":
        yield from ctx.storage.list("")
        return
    for prefix in PREFIXES:
        yield from ctx.storage.list(prefix)


def _copy_hashing(fh: Any, target: Path) -> str:
    """One read of the object, so the sha256 in the manifest is of the bytes written to the backup."""
    h = hashlib.sha256()
    with target.open("wb") as out:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
            out.write(chunk)
    return h.hexdigest()


def _sweep_partials(dest: Path) -> None:
    cutoff = time.time() - PARTIAL_MAX_AGE_S
    for old in dest.glob(".forma-backup-*"):
        if old.stat().st_mtime >= cutoff:
            continue
        if old.is_dir():
            shutil.rmtree(_fs(old), ignore_errors=True)
        else:
            old.unlink(missing_ok=True)


def dump_database(dest_dir: Path) -> Path:
    """Logical dump. SQLite uses the online backup API (consistent with WAL); Postgres uses pg_dump."""
    from forma.db.session import get_engine

    url = get_engine().url
    if url.get_backend_name() == "sqlite":
        out = dest_dir / "db.sqlite"
        src = sqlite3.connect(str(url.database))
        try:
            dst = sqlite3.connect(str(out))
            with dst:
                src.backup(dst)
            dst.close()
        finally:
            src.close()
        return out
    out = dest_dir / "db.sql"
    pg_url = url.render_as_string(hide_password=False).replace("postgresql+psycopg://", "postgresql://")
    with out.open("wb") as fh:
        subprocess.run(["pg_dump", "--no-owner", "--no-privileges", pg_url], check=True, stdout=fh)
    return out


def _drop_newer_settings(dump: Path, out: Path) -> None:
    """pg_dump 17 writes a session setting that a Postgres 16 server refuses, which stops a restore on its
    first lines. The image's client is newer than the server, and the setting says nothing about the data.
    Only the header is touched, so a row that happens to hold the same text stays."""
    with dump.open("rb") as src, out.open("wb") as dst:
        for number, line in enumerate(src):
            if number >= DUMP_HEADER_LINES or not line.startswith(NEWER_THAN_THE_SERVER):
                dst.write(line)


def restore_database(dump: Path) -> None:
    from sqlalchemy import inspect

    from forma.db.session import get_engine

    engine = get_engine()
    if set(inspect(engine).get_table_names()) - {"alembic_version"}:
        raise RuntimeError("refusing to restore into a non-empty database (see RUNBOOK: restore)")
    url = engine.url
    if url.get_backend_name() == "sqlite":
        engine.dispose()
        src = sqlite3.connect(str(dump))
        dst = sqlite3.connect(str(url.database))
        try:
            with dst:
                src.backup(dst)
        finally:
            src.close()
            dst.close()
        return
    pg_url = url.render_as_string(hide_password=False).replace("postgresql+psycopg://", "postgresql://")
    with tempfile.TemporaryDirectory(prefix="forma-restore-") as tmp:
        script = Path(tmp) / "db.sql"
        _drop_newer_settings(dump, script)
        subprocess.run(["psql", "--quiet", "--set", "ON_ERROR_STOP=1", pg_url, "-f", str(script)], check=True)


def create_backup(
    ctx: ServiceContext, dest: Path, *, include_objects: bool = True, archive: bool = False
) -> dict[str, Any]:
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    name = f"forma-backup-{stamp}"
    dest.mkdir(parents=True, exist_ok=True)
    _sweep_partials(dest)
    work = dest / f".{name}"
    partial_archive = dest / f".{name}.tar.gz"
    work.mkdir(exist_ok=False)
    try:
        return _write_backup(ctx, dest, name, work, partial_archive, include_objects, archive, stamp)
    except BaseException:
        shutil.rmtree(_fs(work), ignore_errors=True)
        partial_archive.unlink(missing_ok=True)
        raise


def _write_backup(
    ctx: ServiceContext,
    dest: Path,
    name: str,
    work: Path,
    partial_archive: Path,
    include_objects: bool,
    archive: bool,
    stamp: str,
) -> dict[str, Any]:
    from forma.db import migrate
    from forma.db.session import get_engine

    # The dump comes first: an object is written before its row, so every key the dump names is in the bucket.
    db_file = dump_database(work)
    objects: list[dict[str, Any]] = []
    total = 0
    for obj in _objects(ctx):
        with ctx.storage.open(obj.key) as fh:
            if include_objects:
                target = _fs(work / "objects" / obj.key)
                target.parent.mkdir(parents=True, exist_ok=True)
                digest = _copy_hashing(fh, target)
            else:
                digest = _sha256_stream(fh)
        objects.append({"key": obj.key, "size": obj.size, "sha256": digest})
        total += obj.size
    manifest = {
        "created_at": stamp,
        "forma_version": __import__("forma").__version__,
        "schema_revision": migrate.current_revision(get_engine()),
        "storage": ctx.storage.name,
        "database": db_file.name,
        "objects_included": include_objects,
        "object_count": len(objects),
        "object_bytes": total,
        "config": get_settings().effective(mask_secrets=True),
        "objects": objects,
    }
    (work / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    if archive:
        out_path = dest / f"{name}.tar.gz"
        with tarfile.open(partial_archive, "w:gz") as tar:
            tar.add(_fs(work), arcname=name)
        partial_archive.replace(out_path)
        shutil.rmtree(_fs(work))
        size = out_path.stat().st_size
    else:
        out_path = dest / name
        work.replace(out_path)
        size = sum(p.stat().st_size for p in _fs(out_path).rglob("*") if p.is_file())
    log.info("backup created", path=str(out_path), objects=len(objects), bytes=size)
    return {
        "path": str(out_path),
        "objects": len(objects),
        "object_bytes": total,
        "objects_included": include_objects,
        "bytes": size,
        "database": db_file.name,
    }


def restore_backup(ctx: ServiceContext, src: Path, *, verify: bool = True) -> dict[str, Any]:
    if src.is_file() and src.name.endswith(".tar.gz"):
        # a short temp dir keeps nested keys under Windows' MAX_PATH during extraction
        extract = Path(tempfile.mkdtemp(prefix="forma-restore-"))
        with tarfile.open(src, "r:gz") as tar:
            tar.extractall(extract, filter="data")
        inner = [p for p in extract.iterdir() if p.is_dir()]
        src = inner[0] if len(inner) == 1 else extract
    manifest = json.loads((src / "manifest.json").read_text(encoding="utf-8"))
    restore_database(src / manifest["database"])
    restored = 0
    missing: list[str] = []
    for obj in manifest["objects"]:
        local = _fs(src / "objects" / obj["key"])
        if local.is_file():
            ctx.storage.put(obj["key"], local)
            restored += 1
        elif verify and not ctx.storage.exists(obj["key"]):
            missing.append(obj["key"])
    log.info("backup restored", objects=restored, missing=len(missing))
    return {"database": manifest["database"], "objects_restored": restored, "missing_objects": missing}
