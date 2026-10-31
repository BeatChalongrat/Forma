"""Files in assistant chats: uploads and what the code sandbox produces (ADR-048, ADR-049)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a7c1e2d3f4b5"
down_revision = "f3b4c5d6e7f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assistant_turns",
        sa.Column("attachments", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=True),
    )
    op.create_table(
        "assistant_attachments",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column(
            "conversation_id", sa.String(40), sa.ForeignKey("assistant_conversations.id"), nullable=False
        ),
        sa.Column("turn_id", sa.String(40), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("media_type", sa.String(120), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_assistant_attachments_conversation_id", "assistant_attachments", ["conversation_id"])


def downgrade() -> None:
    op.drop_index("ix_assistant_attachments_conversation_id", table_name="assistant_attachments")
    op.drop_table("assistant_attachments")
    op.drop_column("assistant_turns", "attachments")
