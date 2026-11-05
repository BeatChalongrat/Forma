"""Add provenance/release ledger without modifying or deleting existing knowledge."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "b5d7f9012345"
down_revision = "a4c6e8f01234"
branch_labels = None
depends_on = None


def upgrade() -> None:
    data = sa.JSON().with_variant(JSONB(), "postgresql")
    op.create_table(
        "knowledge_labels",
        sa.Column("subject", sa.String(90), primary_key=True),
        sa.Column("origin", sa.String(24), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("digest", sa.String(64)),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("release_id", sa.String(64)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_knowledge_labels_state", "knowledge_labels", ["state"])
    op.create_index("ix_knowledge_labels_release_id", "knowledge_labels", ["release_id"])
    op.create_table(
        "knowledge_releases",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("source_instance", sa.String(64), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("manifest", data, nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_by", sa.String(128)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_table("knowledge_releases")
    op.drop_table("knowledge_labels")
