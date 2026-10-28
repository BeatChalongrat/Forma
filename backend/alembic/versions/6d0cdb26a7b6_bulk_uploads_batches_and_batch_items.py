"""Bulk uploads (ADR-027): batches (one upload of many files) and batch_items (one per file, stored before
its customer is known, matched by format, then processed as a round of its own).

Revision ID: 6d0cdb26a7b6
Revises: f5f0fe5eb180
Create Date: 2026-10-28 16:57:00.982102+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "6d0cdb26a7b6"
down_revision: str | None = "f5f0fe5eb180"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json() -> sa.types.TypeEngine[object]:
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "batches",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=True),
        sa.Column("auto_publish", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "batch_items",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("batch_id", sa.String(length=40), nullable=False),
        sa.Column("file_name", sa.String(length=512), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("profile", _json(), nullable=True),
        sa.Column("fingerprint", _json(), nullable=True),
        sa.Column("match", _json(), nullable=True),
        sa.Column("customer_id", sa.String(length=40), nullable=True),
        sa.Column("round_id", sa.String(length=40), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["batches.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("batch_items", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_batch_items_batch_id"), ["batch_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_batch_items_customer_id"), ["customer_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_batch_items_round_id"), ["round_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_batch_items_status"), ["status"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("batch_items", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_batch_items_status"))
        batch_op.drop_index(batch_op.f("ix_batch_items_round_id"))
        batch_op.drop_index(batch_op.f("ix_batch_items_customer_id"))
        batch_op.drop_index(batch_op.f("ix_batch_items_batch_id"))
    op.drop_table("batch_items")
    op.drop_table("batches")
