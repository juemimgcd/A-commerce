"""Add flash sale quotas and order attribution.

Revision ID: 0008_flash_sales
Revises: 0007_harness
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_flash_sales"
down_revision: str | None = "0007_harness"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "flash_sales",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sku_id", sa.Uuid(), nullable=False),
        sa.Column("price_fen", sa.BigInteger(), nullable=False),
        sa.Column("stock", sa.Integer(), nullable=False),
        sa.Column("claimed", sa.Integer(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("price_fen > 0", name="positive_price"),
        sa.CheckConstraint(
            "stock >= 0 AND claimed >= 0 AND claimed <= stock", name="quota_balance"
        ),
        sa.CheckConstraint("ends_at > starts_at", name="time_range"),
        sa.ForeignKeyConstraint(["sku_id"], ["skus.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_flash_sales_sku_id", "flash_sales", ["sku_id"])
    op.create_index("ix_flash_sales_active_ends", "flash_sales", ["active", "ends_at"])
    op.add_column("order_items", sa.Column("flash_sale_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_order_items_flash_sale_id_flash_sales",
        "order_items",
        "flash_sales",
        ["flash_sale_id"],
        ["id"],
    )
    op.create_index("ix_order_items_flash_sale_id", "order_items", ["flash_sale_id"])


def downgrade() -> None:
    op.drop_index("ix_order_items_flash_sale_id", table_name="order_items")
    op.drop_constraint(
        "fk_order_items_flash_sale_id_flash_sales", "order_items", type_="foreignkey"
    )
    op.drop_column("order_items", "flash_sale_id")
    op.drop_table("flash_sales")
