"""Persist bounded support execution metadata.

Revision ID: 0007_harness
Revises: 0006_support
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007_harness"
down_revision: str | None = "0006_support"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "support_runs",
        sa.Column("runtime", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.alter_column("support_runs", "runtime", server_default=None)


def downgrade() -> None:
    op.drop_column("support_runs", "runtime")
