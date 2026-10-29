"""Persistent, account-scoped assistant conversations and idempotent turns.

Revision ID: 93c7aa12b641
Revises: 6d0cdb26a7b6
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "93c7aa12b641"
down_revision: str | None = "6d0cdb26a7b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assistant_conversations",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("owner_key", sa.String(64), nullable=False),
        sa.Column("access_key", sa.String(64), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("round_id", sa.String(40)),
        sa.Column("dataset_id", sa.String(40)),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("legacy_key", sa.String(64)),
        sa.Column("active_run", sa.String(40)),
        sa.Column("busy_until", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_key", "access_key", "legacy_key", name="uq_assistant_legacy"),
    )
    op.create_index(
        "ix_assistant_owner_access_updated",
        "assistant_conversations",
        ["owner_key", "access_key", "updated_at"],
    )
    op.create_table(
        "assistant_turns",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column(
            "conversation_id", sa.String(40), sa.ForeignKey("assistant_conversations.id"), nullable=False
        ),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("reply", sa.JSON().with_variant(postgresql.JSONB(), "postgresql")),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("conversation_id", "request_id", name="uq_assistant_request"),
        sa.UniqueConstraint("conversation_id", "sequence", name="uq_assistant_sequence"),
    )
    op.create_index("ix_assistant_turns_conversation_id", "assistant_turns", ["conversation_id"])


def downgrade() -> None:
    op.drop_table("assistant_turns")
    op.drop_table("assistant_conversations")
