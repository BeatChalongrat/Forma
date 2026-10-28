"""Shared fixtures.

The settings object is ``lru_cache``d, so the environment must be isolated *before* ``forma`` is
imported anywhere. conftest is collected before any test module, so we point the database and the
data directory at a throw-away location here; API tests build a fresh isolated context on top.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import polars as pl
import pytest

# setdefault: this module may be imported twice (as ``conftest`` by pytest and as ``tests.conftest`` by
# tests that reuse ``clear_forma_caches``); a second import must never clobber a fixture's monkeypatch.
_SESSION_TMP = Path(tempfile.mkdtemp(prefix="forma-tests-"))
os.environ.setdefault("FORMA_DATABASE_URL", f"sqlite:///{_SESSION_TMP}/session.db")
os.environ.setdefault("FORMA_DATA_DIR", str(_SESSION_TMP / "data"))
os.environ.setdefault("FORMA_LLM_PROVIDER", "mock")

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = REPO_ROOT / "target_schema"
POLICY_DIR = REPO_ROOT / "policies"


def clear_forma_caches() -> None:
    """Drop every lru_cache that pins settings / engine / context between tests."""
    from forma.core.settings import get_settings
    from forma.db.session import _factory, get_engine
    from forma.jobs import runner
    from forma.services.context import get_context

    runner.reset_backend()
    get_context.cache_clear()
    _factory.cache_clear()
    with contextlib.suppress(Exception):  # the engine may never have been created
        get_engine().dispose()
    get_engine.cache_clear()
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def schema():  # type: ignore[no-untyped-def]
    from forma.schema_registry import SchemaRegistry

    return SchemaRegistry(SCHEMA_DIR).get("product")


@pytest.fixture
def grid():  # type: ignore[no-untyped-def]
    """Build a raw string grid (columns c0..cN) the way the readers produce it."""

    def _grid(rows: list[list[object]]) -> pl.DataFrame:
        width = max(len(r) for r in rows)
        padded = [list(r) + [None] * (width - len(r)) for r in rows]
        return pl.DataFrame(
            {f"c{j}": [None if r[j] is None else str(r[j]) for r in padded] for j in range(width)},
            schema={f"c{j}": pl.String for j in range(width)},
        )

    return _grid


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point FORMA at a private SQLite DB + data dir and reset all caches around the test."""
    monkeypatch.setenv("FORMA_DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("FORMA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FORMA_LLM_PROVIDER", "mock")
    clear_forma_caches()
    yield tmp_path
    clear_forma_caches()
