"""Keep the exact reviewed mapping as durable assistant context."""

import sqlalchemy as sa
from alembic import op

revision = "f3b4c5d6e7f8"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("assistant_conversations", sa.Column("mapping_id", sa.String(40), nullable=True))


def downgrade() -> None:
    op.drop_column("assistant_conversations", "mapping_id")
