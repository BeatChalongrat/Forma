"""Per-customer policy overrides, applied from calibration proposals (ADR-037)."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f2a3b4c5d6e7"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    kind = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")
    op.add_column("customers", sa.Column("policy_overrides", kind, nullable=True))


def downgrade() -> None:
    op.drop_column("customers", "policy_overrides")
