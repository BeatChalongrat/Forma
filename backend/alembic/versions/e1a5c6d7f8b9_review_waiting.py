"""A review task can wait for the customer, with a note and a date (ADR-053)."""

import sqlalchemy as sa
from alembic import op

revision = "e1a5c6d7f8b9"
down_revision = "d0f4b5c6e7a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("review_tasks", sa.Column("waiting_until", sa.Date(), nullable=True))
    op.add_column("review_tasks", sa.Column("waiting_note", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("review_tasks", "waiting_note")
    op.drop_column("review_tasks", "waiting_until")
