"""Schema lifecycle on top of Alembic (ADR-011): used by ``init_db`` and ``forma db``."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, inspect, text

from forma.core.logging import get_logger
from forma.db.models import Base

log = get_logger(__name__)


class MigrationPending(RuntimeError):
    pass


def alembic_dir() -> Path:
    """Where the migration scripts live: FORMA_ALEMBIC_DIR, the source checkout (backend/alembic), or
    ./alembic next to the process (the container image copies it to the working directory)."""
    from forma.core.settings import get_settings

    s = get_settings()
    candidates = [
        s.resolve(s.alembic_dir) if s.alembic_dir else None,
        Path(__file__).resolve().parents[3] / "alembic",
        Path.cwd() / "alembic",
    ]
    for c in candidates:
        if c is not None and (c / "env.py").exists():
            return c
    raise RuntimeError("alembic directory not found; set FORMA_ALEMBIC_DIR")


def alembic_config(url: str | None = None) -> Config:
    scripts = alembic_dir()
    ini = scripts.parent / "alembic.ini"
    cfg = Config(str(ini)) if ini.exists() else Config()
    cfg.set_main_option("script_location", str(scripts))
    cfg.set_main_option("prepend_sys_path", str(scripts.parent / "src"))
    if url:
        cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))  # configparser escapes
    return cfg


def head_revision(cfg: Config | None = None) -> str | None:
    return ScriptDirectory.from_config(cfg or alembic_config()).get_current_head()


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as conn:
        if "alembic_version" not in inspect(conn).get_table_names():
            return None
        return MigrationContext.configure(conn).get_current_revision()


def status(engine: Engine) -> dict[str, str | bool | None]:
    cur, head = current_revision(engine), head_revision()
    return {"current": cur, "head": head, "pending": cur != head}


def upgrade(engine: Engine, revision: str = "head") -> None:
    command.upgrade(alembic_config(str(engine.url.render_as_string(hide_password=False))), revision)


def stamp(engine: Engine, revision: str = "head") -> None:
    command.stamp(alembic_config(str(engine.url.render_as_string(hide_password=False))), revision)


def revision(message: str, *, autogenerate: bool = True) -> None:
    from forma.db.session import get_engine

    url = str(get_engine().url.render_as_string(hide_password=False))
    command.revision(alembic_config(url), message=message, autogenerate=autogenerate)


def _add_missing_columns(engine: Engine) -> list[str]:
    """Legacy dev databases created with ``create_all`` before Alembic: add columns the models grew."""
    added: list[str] = []
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                ddl = f"ALTER TABLE {table.name} ADD COLUMN {col.name} {col.type.compile(engine.dialect)}"
                conn.execute(text(ddl))
                added.append(f"{table.name}.{col.name}")
    return added


def ensure_schema(engine: Engine, *, dev: bool) -> dict[str, object]:
    cur = current_revision(engine)
    head = head_revision()
    if cur is None and dev:
        insp = inspect(engine)
        legacy = bool(set(insp.get_table_names()) - {"alembic_version"})
        Base.metadata.create_all(engine)
        added = _add_missing_columns(engine) if legacy else []
        if head:
            stamp(engine, head)
        log.info("schema created (dev)", legacy=legacy, added_columns=added, stamped=head)
        return {"mode": "create_all", "legacy": legacy, "added_columns": added, "revision": head}
    if cur is None:
        raise MigrationPending(
            "database has no schema; run `forma db upgrade` (FORMA_ENV=prod never runs create_all)"
        )
    if cur != head:
        raise MigrationPending(f"database is at revision {cur}, code expects {head}; run `forma db upgrade`")
    return {"mode": "migrated", "revision": cur}
