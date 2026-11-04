"""Index active category browsing in cursor order.

Revision ID: 0011_catalog_category_index
Revises: 0010_merchant_ops
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011_catalog_category_index"
down_revision: str | None = "0010_merchant_ops"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_products_active_category_id",
        "products",
        ["category_id", "id"],
        postgresql_where=sa.text("active IS TRUE"),
    )


def downgrade() -> None:
    op.drop_index("ix_products_active_category_id", table_name="products")
