"""Corrections learned from human approvals (ADR-038)."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e1f2a3b4c5d6"
down_revision = "d84e012f931b"
branch_labels = None
depends_on = None


def _json() -> sa.types.TypeEngine[object]:
    return sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "corrections",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("customer_id", sa.String(length=40), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("target", sa.String(length=255), nullable=True),
        sa.Column("header", sa.String(length=255), nullable=True),
        sa.Column("before", _json(), nullable=True),
        sa.Column("after", _json(), nullable=True),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("mapping_version_id", sa.String(length=40), nullable=True),
        sa.Column("round_id", sa.String(length=40), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="confirmed"),
        sa.Column("uses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_corrections_customer_id", "corrections", ["customer_id"])
    op.create_index("ix_corrections_kind_status", "corrections", ["kind", "status"])
    op.create_index("ix_corrections_header_target", "corrections", ["header", "target"])


def downgrade() -> None:
    op.drop_index("ix_corrections_header_target", table_name="corrections")
    op.drop_index("ix_corrections_kind_status", table_name="corrections")
    op.drop_index("ix_corrections_customer_id", table_name="corrections")
    op.drop_table("corrections")
