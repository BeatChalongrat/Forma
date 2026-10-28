"""Backup / restore (ADR-015, docs/RUNBOOK.md).

A backup is a directory (or tar.gz) with:
  * ``db.sql`` / ``db.sqlite`` — a logical dump (``pg_dump`` on Postgres, the SQLite backup API locally),
  * ``manifest.json`` — every storage key with size + sha256, the config (secrets masked) and the schema
    revision,
  * ``objects/`` — the storage objects themselves unless ``--no-objects`` (S3 users typically rely on
    bucket versioning/replication and keep only the manifest).

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
from pathlib import Path
from typing import Any

from forma.core.logging import get_logger
from forma.core.settings import get_settings
from forma.services.context import ServiceContext

log = get_logger(__name__)
PREFIXES = ("files/", "datasets/", "exports/")


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
    subprocess.run(["psql", "--quiet", "--set", "ON_ERROR_STOP=1", pg_url, "-f", str(dump)], check=True)


def create_backup(
    ctx: ServiceContext, dest: Path, *, include_objects: bool = True, archive: bool = False
) -> dict[str, Any]:
    from forma.db import migrate
    from forma.db.session import get_engine

    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    work = dest / f"forma-backup-{stamp}"
    work.mkdir(parents=True, exist_ok=False)
    db_file = dump_database(work)
    objects: list[dict[str, Any]] = []
    total = 0
    for prefix in PREFIXES:
        for obj in ctx.storage.list(prefix):
            with ctx.storage.open(obj.key) as fh:
                digest = _sha256_stream(fh)
            objects.append({"key": obj.key, "size": obj.size, "sha256": digest})
            total += obj.size
            if include_objects:
                target = _fs(work / "objects" / obj.key)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(_fs(ctx.storage.local_path(obj.key)), target)
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
    out_path = work
    if archive:
        out_path = dest / f"{work.name}.tar.gz"
        with tarfile.open(out_path, "w:gz") as tar:
            tar.add(_fs(work), arcname=work.name)
        shutil.rmtree(_fs(work))
    log.info("backup created", path=str(out_path), objects=len(objects), bytes=total)
    return {"path": str(out_path), "objects": len(objects), "object_bytes": total, "database": db_file.name}


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
