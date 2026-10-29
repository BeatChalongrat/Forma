"""User-selected dates for reusable upload rounds."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c73d901e820a"
down_revision = "b8e012a95d70"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("batches", sa.Column("work_date", sa.Date(), nullable=True))
    op.create_index("ix_batches_work_date", "batches", ["work_date"])


def downgrade() -> None:
    op.drop_index("ix_batches_work_date", table_name="batches")
    op.drop_column("batches", "work_date")
