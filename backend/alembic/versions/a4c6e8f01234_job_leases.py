"""Durable worker leases and queue dispatch receipts."""

import sqlalchemy as sa
from alembic import op

revision = "a4c6e8f01234"
down_revision = "e1a5c6d7f8b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("lease_token", sa.String(40), nullable=True))
    for name in ("heartbeat_at", "lease_expires_at", "dispatched_at"):
        op.add_column("jobs", sa.Column(name, sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    for name in ("dispatched_at", "lease_expires_at", "heartbeat_at", "lease_token"):
        op.drop_column("jobs", name)
