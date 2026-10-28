"""Storage keys instead of absolute paths (ADR-012).

source_files.storage_path → storage_key, dataset_versions.records_path → records_key,
exports.path → storage_key. Existing absolute paths under the data directory are converted to keys;
anything outside it is kept verbatim (and reported) so nothing is silently lost.

Revision ID: a1b2c3d4e5f6
Revises: 06c905910cbb
Create Date: 2026-10-28 05:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "06c905910cbb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RENAMES = [
    ("source_files", "storage_path", "storage_key"),
    ("dataset_versions", "records_path", "records_key"),
    ("exports", "path", "storage_key"),
]


def _data_root() -> Path | None:
    try:
        from forma.core.settings import get_settings

        return get_settings().data_path
    except Exception:  # pragma: no cover - settings unavailable in exotic migration contexts
        return None


def _to_key(value: str, root: Path | None) -> str:
    if not value or "://" in value or not (":" in value[:3] or value.startswith(("/", "\\"))):
        return value.replace("\\", "/")  # already a key
    if root is not None:
        try:
            return Path(value).resolve().relative_to(root.resolve()).as_posix()
        except (ValueError, OSError):
            pass
    return value


def upgrade() -> None:
    for table, old, new in RENAMES:
        with op.batch_alter_table(table) as batch:
            batch.alter_column(old, new_column_name=new, existing_type=sa.Text(), existing_nullable=False)
    root = _data_root()
    conn = op.get_bind()
    for table, _old, new in RENAMES:
        rows = conn.execute(sa.text(f"SELECT id, {new} AS v FROM {table}")).fetchall()
        for rid, v in rows:
            key = _to_key(v or "", root)
            if key != v:
                conn.execute(sa.text(f"UPDATE {table} SET {new} = :k WHERE id = :i"), {"k": key, "i": rid})


def downgrade() -> None:
    for table, old, new in RENAMES:
        with op.batch_alter_table(table) as batch:
            batch.alter_column(new, new_column_name=old, existing_type=sa.Text(), existing_nullable=False)
