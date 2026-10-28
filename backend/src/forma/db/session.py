from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from forma.core.settings import get_settings


@lru_cache
def get_engine() -> Engine:
    url = get_settings().sqlalchemy_url
    kwargs: dict[str, object] = {"future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(url, **kwargs)  # type: ignore[arg-type]
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _record) -> None:  # type: ignore[no-untyped-def]
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()

    return engine


def init_db() -> None:
    """Bring the schema up (ADR-011).

    * dev + no ``alembic_version`` table → ``create_all`` (plus missing columns for legacy dev DBs) and
      stamp ``head`` so later changes go through migrations;
    * otherwise migrations are mandatory: a pending revision makes the API refuse to start.
    """
    from forma.db.migrate import ensure_schema

    ensure_schema(get_engine(), dev=not get_settings().is_prod)


@lru_cache
def _factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    s = _factory()()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    s = _factory()()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
