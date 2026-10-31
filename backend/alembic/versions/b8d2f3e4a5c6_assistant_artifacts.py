"""Works the assistant makes in a chat, with their versions (ADR-050)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b8d2f3e4a5c6"
down_revision = "a7c1e2d3f4b5"
branch_labels = None
depends_on = None


def _json() -> sa.types.TypeEngine[object]:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "assistant_artifacts",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column(
            "conversation_id", sa.String(40), sa.ForeignKey("assistant_conversations.id"), nullable=False
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("latest", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_assistant_artifacts_conversation_id", "assistant_artifacts", ["conversation_id"])
    op.create_table(
        "assistant_artifact_versions",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("artifact_id", sa.String(40), sa.ForeignKey("assistant_artifacts.id"), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.String(40), nullable=True),
        sa.Column("restored_from", sa.Integer(), nullable=True),
        sa.Column("turn_id", sa.String(40), nullable=True),
        sa.Column("note", sa.String(160), nullable=False),
        sa.Column("attachment_id", sa.String(40), nullable=True),
        sa.Column("content", _json(), nullable=True),
        sa.Column("code", sa.Text(), nullable=True),
        sa.Column("preview", _json(), nullable=True),
        sa.Column("checked", sa.Integer(), nullable=False),
        sa.Column("source", _json(), nullable=True),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("artifact_id", "number", name="uq_assistant_artifact_version"),
    )
    op.create_index(
        "ix_assistant_artifact_versions_artifact_id", "assistant_artifact_versions", ["artifact_id"]
    )
    op.create_index("ix_assistant_artifact_versions_turn_id", "assistant_artifact_versions", ["turn_id"])


def downgrade() -> None:
    op.drop_table("assistant_artifact_versions")
    op.drop_table("assistant_artifacts")
