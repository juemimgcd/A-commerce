"""Shop ownership, immutable trading snapshots and merchant sessions.

Revision ID: 0009_merchants
Revises: 0008_flash_sales
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0009_merchants"
down_revision: str | None = "0008_flash_sales"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SHOP = "cb59df63-1c60-4bf8-90ac-ae5809c57c01"
OWNER = "cb59df63-1c60-4bf8-90ac-ae5809c57c02"


def upgrade() -> None:
    op.create_table(
        "shops",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("return_address", postgresql.JSONB(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('active', 'suspended', 'closed')", name="shop_status"),
    )
    op.create_index("ix_shops_owner_user_id", "shops", ["owner_user_id"])
    # 固定保留账号没有有效密码且禁用，不能成为登录后门。开店须由平台显式配置。
    op.execute(
        sa.text(
            "INSERT INTO users (id, created_at, email, password_hash, active, event_sequence) "
            f"VALUES ('{OWNER}', now(), 'legacy-store@internal.invalid', '!disabled', false, 0)"
        )
    )
    op.execute(
        sa.text(
            "INSERT INTO shops (id, created_at, owner_user_id, name, status, version, updated_at) "
            f"VALUES ('{SHOP}', now(), '{OWNER}', '历史自营店铺', 'suspended', 1, now())"
        )
    )
    for table in ("products", "quotes", "orders"):
        op.add_column(table, sa.Column("shop_id", sa.Uuid(), nullable=True))
        op.execute(sa.text(f"UPDATE {table} SET shop_id = '{SHOP}' WHERE shop_id IS NULL"))
        op.alter_column(table, "shop_id", nullable=False)
        op.create_foreign_key(f"fk_{table}_shop_id_shops", table, "shops", ["shop_id"], ["id"])
    op.create_index("ix_products_shop_id", "products", ["shop_id"])
    op.create_index("ix_orders_shop_created", "orders", ["shop_id", "created_at", "id"])
    op.create_index("ix_orders_shop_paid", "orders", ["shop_id", "paid_at"])
    op.add_column(
        "quotes", sa.Column("shop_version", sa.Integer(), server_default="1", nullable=False)
    )
    op.alter_column("quotes", "shop_version", server_default=None)
    op.execute("UPDATE quotes SET expires_at = LEAST(expires_at, now())")
    op.add_column("orders", sa.Column("return_address", postgresql.JSONB(), nullable=True))
    op.add_column("skus", sa.Column("cost_fen", sa.BigInteger(), nullable=True))
    op.add_column(
        "skus", sa.Column("cost_version", sa.Integer(), server_default="1", nullable=False)
    )
    op.alter_column("skus", "cost_version", server_default=None)
    for table in ("skus", "order_items"):
        if table == "order_items":
            op.add_column(table, sa.Column("cost_fen", sa.BigInteger(), nullable=True))
        op.create_check_constraint("nonnegative_cost", table, "cost_fen IS NULL OR cost_fen >= 0")
    for column, target in (("product_id", "products"), ("category_id", "categories")):
        op.add_column("order_items", sa.Column(column, sa.Uuid(), nullable=True))
        op.create_foreign_key(
            f"fk_order_items_{column}_{target}", "order_items", target, [column], ["id"]
        )
    # 商品引用可以从不可变 SKU 外键回填，历史品类、成本和原价不能从当前值猜测。
    op.execute(
        "UPDATE order_items i SET product_id = s.product_id FROM skus s WHERE i.sku_id = s.id"
    )
    op.add_column("order_items", sa.Column("category_name", sa.String(100), nullable=True))
    op.add_column("order_items", sa.Column("regular_price_fen", sa.BigInteger(), nullable=True))
    op.add_column("refunds", sa.Column("shipping_fen", sa.BigInteger(), nullable=True))
    op.add_column(
        "return_requests", sa.Column("inspected_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("return_requests", sa.Column("resellable", sa.Boolean(), nullable=True))
    op.create_table(
        "refund_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("refund_id", sa.Uuid(), sa.ForeignKey("refunds.id"), nullable=False),
        sa.Column("order_item_id", sa.Uuid(), sa.ForeignKey("order_items.id"), nullable=False),
        sa.Column("amount_fen", sa.BigInteger(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.UniqueConstraint("refund_id", "order_item_id"),
        sa.CheckConstraint("amount_fen > 0 AND quantity >= 0", name="refund_item_balance"),
    )
    op.create_index("ix_refund_items_refund_id", "refund_items", ["refund_id"])
    op.create_index("ix_refund_items_order_item_id", "refund_items", ["order_item_id"])
    for table in ("audit_events", "support_conversations", "support_runs"):
        op.add_column(table, sa.Column("shop_id", sa.Uuid(), nullable=True))
        op.create_foreign_key(f"fk_{table}_shop_id_shops", table, "shops", ["shop_id"], ["id"])
        op.create_index(f"ix_{table}_shop_id", table, ["shop_id"])
    op.add_column("audit_events", sa.Column("actor_kind", sa.String(16), nullable=True))
    op.alter_column("product_media", "staff_id", nullable=True)
    op.add_column("product_media", sa.Column("user_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_product_media_user_id_users", "product_media", "users", ["user_id"], ["id"]
    )
    # 数据迁移窗口内清空旧的公开缓存需由发布步骤执行；不在迁移里访问 Redis 或外部支付。


def downgrade() -> None:
    # 有商家交易后回退会丢失成交归属和成本，必须使用前向修复。
    raise RuntimeError("Merchant migration is irreversible; use a forward repair migration")
