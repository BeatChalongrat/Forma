"""Durable assistant work, drafts, branches and feedback."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "daa012345680"
down_revision = "d9a012345679"
branch_labels = None
depends_on = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(JSONB(), "postgresql")
    op.create_table(
        "assistant_runs",
        sa.Column("turn_id", sa.String(40), primary_key=True),
        sa.Column("conversation_id", sa.String(40), nullable=False),
        sa.Column("job_id", sa.String(40), nullable=False, unique=True),
        sa.Column("state", json_type, nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("calls", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_assistant_runs_conversation_id", "assistant_runs", ["conversation_id"])
    op.create_table(
        "assistant_chat_state",
        sa.Column("conversation_id", sa.String(40), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("draft", json_type, nullable=False),
        sa.Column("branch", json_type, nullable=False),
        sa.Column("seen_sequence", sa.Integer(), nullable=False),
    )
    op.create_table(
        "assistant_feedback",
        sa.Column("turn_id", sa.String(40), primary_key=True),
        sa.Column("rating", sa.String(12), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    for table in ("assistant_feedback", "assistant_chat_state", "assistant_runs"):
        op.drop_table(table)
