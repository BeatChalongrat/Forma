"""Persist per-conversation pins.

Revision ID: b8e012a95d70
Revises: 93c7aa12b641
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b8e012a95d70"
down_revision = "93c7aa12b641"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assistant_conversations",
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("assistant_conversations", "pinned")
