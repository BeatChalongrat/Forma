"""Align review JSON storage with models; found by the full-stack recovery check."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "d9a012345679"
down_revision = "d8a012345678"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table, column in (("review_drafts", "payload"), ("review_receipts", "result")):
            op.alter_column(
                table, column, type_=JSONB(), existing_type=sa.JSON(), postgresql_using=f'"{column}"::jsonb'
            )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table, column in (("review_drafts", "payload"), ("review_receipts", "result")):
            op.alter_column(
                table, column, type_=sa.JSON(), existing_type=JSONB(), postgresql_using=f'"{column}"::json'
            )
