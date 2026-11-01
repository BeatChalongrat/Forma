"""Closing a round without deleting it: cancel, move, replace (ADR-052)."""

import sqlalchemy as sa
from alembic import op

revision = "d0f4b5c6e7a8"
down_revision = "c9e3a4f5b6d7"
branch_labels = None
depends_on = None

COLUMNS = (
    ("closed_from", sa.String(32)),
    ("closed_by", sa.String(128)),
    ("closed_at", sa.DateTime(timezone=True)),
    ("closed_reason", sa.Text()),
    ("successor_id", sa.String(40)),
    ("predecessor_id", sa.String(40)),
)


def upgrade() -> None:
    for name, kind in COLUMNS:
        op.add_column("import_rounds", sa.Column(name, kind, nullable=True))


def downgrade() -> None:
    for name, _ in reversed(COLUMNS):
        op.drop_column("import_rounds", name)
