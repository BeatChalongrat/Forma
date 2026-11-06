"""Persist personal review drafts and idempotent confirmation receipts."""

import sqlalchemy as sa
from alembic import op

revision = "d7f901234567"
down_revision = "c6e8fa123456"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "review_drafts",
        sa.Column("mapping_id", sa.String(40), sa.ForeignKey("mapping_versions.id"), primary_key=True),
        sa.Column("owner", sa.String(64), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "review_receipts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("mapping_id", sa.String(40), sa.ForeignKey("mapping_versions.id"), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_review_receipts_mapping_id", "review_receipts", ["mapping_id"])


def downgrade() -> None:
    op.drop_table("review_receipts")
    op.drop_table("review_drafts")
