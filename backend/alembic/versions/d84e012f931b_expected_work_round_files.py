"""Optional file receipt targets for work-round dashboards."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d84e012f931b"
down_revision = "c73d901e820a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("batches", sa.Column("expected_files", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("batches", "expected_files")
