"""Enforce unique dataset version numbers without rewriting immutable history."""

import sqlalchemy as sa
from alembic import op

revision = "c6e8fa123456"
down_revision = "b5d7f9012345"
branch_labels = None
depends_on = None


def upgrade() -> None:
    duplicates = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT round_id, version, COUNT(*) AS n FROM dataset_versions "
                "GROUP BY round_id, version HAVING COUNT(*) > 1 LIMIT 20"
            )
        )
        .fetchall()
    )
    if duplicates:
        raise RuntimeError(
            "Dataset version conflicts require review before upgrade; no history was changed: "
            + repr([tuple(row) for row in duplicates])
        )
    op.drop_index("ix_dataset_round_version", table_name="dataset_versions")
    op.create_index("ix_dataset_round_version", "dataset_versions", ["round_id", "version"], unique=True)
    op.add_column("overrides", sa.Column("schema_ref", sa.String(255)))
    op.add_column("overrides", sa.Column("schema_hash", sa.String(64)))


def downgrade() -> None:
    op.drop_column("overrides", "schema_hash")
    op.drop_column("overrides", "schema_ref")
    op.drop_index("ix_dataset_round_version", table_name="dataset_versions")
    op.create_index("ix_dataset_round_version", "dataset_versions", ["round_id", "version"])
