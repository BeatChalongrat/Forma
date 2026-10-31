"""What the assistant remembers across chats, and chats that stay out of it (ADR-051)."""

import sqlalchemy as sa
from alembic import op

revision = "c9e3a4f5b6d7"
down_revision = "b8d2f3e4a5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in ("memory_off", "incognito"):
        op.add_column(
            "assistant_conversations",
            sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    op.create_table(
        "assistant_memories",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("owner_key", sa.String(64), nullable=False),
        sa.Column("topic", sa.String(120), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("sensitive", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source_chat_id", sa.String(40), nullable=True),
        sa.Column("source_turn_id", sa.String(40), nullable=True),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_assistant_memories_owner_key", "assistant_memories", ["owner_key"])


def downgrade() -> None:
    op.drop_table("assistant_memories")
    for name in ("incognito", "memory_off"):
        op.drop_column("assistant_conversations", name)
