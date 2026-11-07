"""Durable job activity journal.

Revision ID: d8a012345678
Revises: d7f901234567
"""

import sqlalchemy as sa
from alembic import op

revision = "d8a012345678"
down_revision = "d7f901234567"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_activity",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.String(40), nullable=False),
        sa.Column("attempt", sa.Integer, nullable=False),
        sa.Column("span_id", sa.String(40), nullable=False),
        sa.Column("parent_id", sa.String(40)),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("at", sa.String(40), nullable=False),
        sa.Column("duration_ms", sa.Integer),
        sa.Column("detail", sa.Text, nullable=False),
    )
    op.create_index("ix_activity_job_attempt_id", "job_activity", ["job_id", "attempt", "id"])


def downgrade() -> None:
    op.drop_table("job_activity")
