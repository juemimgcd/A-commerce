"""初始化身份目录交易和异步恢复表

Revision ID: 0001_initial
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 初始版本固定表结构，不引用未来可能变化的 ORM metadata。
    op.create_table(
        "async_operations",
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("dispatch_version", sa.Integer(), nullable=False),
        sa.Column("fence", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=120), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_async_operations")),
        sa.UniqueConstraint("kind", "resource_id", name=op.f("uq_async_operations_kind")),
    )
    op.create_index(
        "ix_operations_due", "async_operations", ["status", "next_attempt_at"], unique=False
    )
    op.create_table(
        "audit_events",
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("detail", sa.String(length=500), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
    )
    op.create_index(op.f("ix_audit_events_action"), "audit_events", ["action"], unique=False)
    op.create_table(
        "categories",
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_categories")),
        sa.UniqueConstraint("name", name=op.f("uq_categories_name")),
    )
    op.create_table(
        "idempotency_records",
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=60), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("result_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_idempotency_records")),
        sa.UniqueConstraint(
            "actor_id", "action", "key", name=op.f("uq_idempotency_records_actor_id")
        ),
    )
    op.create_table(
        "message_failures",
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("message_id", sa.String(length=100), nullable=True),
        sa.Column("reason", sa.String(length=200), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_message_failures")),
        sa.UniqueConstraint("digest", name=op.f("uq_message_failures_digest")),
    )
    op.create_table(
        "staff",
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("password_hash", sa.String(length=256), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("permissions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_staff")),
        sa.UniqueConstraint("email", name=op.f("uq_staff_email")),
    )
    op.create_table(
        "users",
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("password_hash", sa.String(length=256), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("event_sequence", sa.BigInteger(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_table(
        "addresses",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("recipient", sa.String(length=80), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=False),
        sa.Column("region", sa.String(length=120), nullable=False),
        sa.Column("detail", sa.String(length=300), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_addresses_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_addresses")),
    )
    op.create_index(op.f("ix_addresses_user_id"), "addresses", ["user_id"], unique=False)
    op.create_table(
        "event_sequences",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("value", sa.BigInteger(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_event_sequences_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_event_sequences")),
        sa.UniqueConstraint("user_id", name=op.f("uq_event_sequences_user_id")),
    )
    op.create_table(
        "outbox_events",
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("dispatch_version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["async_operations.id"],
            name=op.f("fk_outbox_events_operation_id_async_operations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox_events")),
        sa.UniqueConstraint(
            "operation_id", "dispatch_version", name=op.f("uq_outbox_events_operation_id")
        ),
    )
    op.create_index("ix_outbox_due", "outbox_events", ["sent_at", "available_at"], unique=False)
    op.create_table(
        "products",
        sa.Column("category_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("brand", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=4000), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["category_id"], ["categories.id"], name=op.f("fk_products_category_id_categories")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_products")),
    )
    op.create_index(op.f("ix_products_category_id"), "products", ["category_id"], unique=False)
    op.create_table(
        "sessions",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("staff_id", sa.Uuid(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(user_id IS NULL) <> (staff_id IS NULL)", name=op.f("ck_sessions_one_principal")
        ),
        sa.ForeignKeyConstraint(
            ["staff_id"], ["staff.id"], name=op.f("fk_sessions_staff_id_staff")
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_sessions_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_sessions_token_hash")),
    )
    op.create_index(op.f("ix_sessions_staff_id"), "sessions", ["staff_id"], unique=False)
    op.create_index(op.f("ix_sessions_user_id"), "sessions", ["user_id"], unique=False)
    op.create_table(
        "favorites",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["product_id"], ["products.id"], name=op.f("fk_favorites_product_id_products")
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_favorites_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_favorites")),
        sa.UniqueConstraint("user_id", "product_id", name=op.f("uq_favorites_user_id")),
    )
    op.create_table(
        "quotes",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("address_id", sa.Uuid(), nullable=False),
        sa.Column("address_version", sa.Integer(), nullable=False),
        sa.Column("address_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("items", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("total_fen", sa.BigInteger(), nullable=False),
        sa.Column("shipping_fen", sa.BigInteger(), nullable=False),
        sa.Column("policy", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["address_id"], ["addresses.id"], name=op.f("fk_quotes_address_id_addresses")
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_quotes_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quotes")),
    )
    op.create_index(op.f("ix_quotes_user_id"), "quotes", ["user_id"], unique=False)
    op.create_table(
        "skus",
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("attributes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("price_fen", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("price_fen > 0", name=op.f("ck_skus_positive_price")),
        sa.ForeignKeyConstraint(
            ["product_id"], ["products.id"], name=op.f("fk_skus_product_id_products")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_skus")),
        sa.UniqueConstraint("code", name=op.f("uq_skus_code")),
    )
    op.create_index(op.f("ix_skus_product_id"), "skus", ["product_id"], unique=False)
    op.create_table(
        "cart_items",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("sku_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("quantity > 0 AND quantity <= 99", name=op.f("ck_cart_items_quantity")),
        sa.ForeignKeyConstraint(["sku_id"], ["skus.id"], name=op.f("fk_cart_items_sku_id_skus")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_cart_items_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cart_items")),
        sa.UniqueConstraint("user_id", "sku_id", name=op.f("uq_cart_items_user_id")),
    )
    op.create_table(
        "orders",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("quote_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payment_status", sa.String(length=32), nullable=False),
        sa.Column("fulfillment_status", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("total_fen", sa.BigInteger(), nullable=False),
        sa.Column("shipping_fen", sa.BigInteger(), nullable=False),
        sa.Column("refund_reserved_fen", sa.BigInteger(), nullable=False),
        sa.Column("refunded_fen", sa.BigInteger(), nullable=False),
        sa.Column("address_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("policy", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "total_fen > 0 AND refund_reserved_fen >= 0 AND refunded_fen >= 0 "
            "AND refund_reserved_fen + refunded_fen <= total_fen",
            name=op.f("ck_orders_refund_balance"),
        ),
        sa.ForeignKeyConstraint(
            ["quote_id"], ["quotes.id"], name=op.f("fk_orders_quote_id_quotes")
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_orders_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_orders")),
        sa.UniqueConstraint("quote_id", name=op.f("uq_orders_quote_id")),
    )
    op.create_index("ix_orders_expiry", "orders", ["status", "expires_at"], unique=False)
    op.create_index(
        "ix_orders_user_created", "orders", ["user_id", "created_at", "id"], unique=False
    )
    op.create_table(
        "stock_items",
        sa.Column("sku_id", sa.Uuid(), nullable=False),
        sa.Column("on_hand", sa.Integer(), nullable=False),
        sa.Column("reserved", sa.Integer(), nullable=False),
        sa.Column("allocated", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "on_hand >= 0 AND reserved >= 0 AND allocated >= 0 AND reserved + allocated <= on_hand",
            name=op.f("ck_stock_items_stock_balance"),
        ),
        sa.ForeignKeyConstraint(["sku_id"], ["skus.id"], name=op.f("fk_stock_items_sku_id_skus")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_items")),
        sa.UniqueConstraint("sku_id", name=op.f("uq_stock_items_sku_id")),
    )
    op.create_table(
        "stock_movements",
        sa.Column("reference", sa.String(length=160), nullable=False),
        sa.Column("sku_id", sa.Uuid(), nullable=False),
        sa.Column("on_hand_delta", sa.Integer(), nullable=False),
        sa.Column("reserved_delta", sa.Integer(), nullable=False),
        sa.Column("allocated_delta", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=300), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["sku_id"], ["skus.id"], name=op.f("fk_stock_movements_sku_id_skus")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_movements")),
        sa.UniqueConstraint("reference", name=op.f("uq_stock_movements_reference")),
    )
    op.create_index(op.f("ix_stock_movements_sku_id"), "stock_movements", ["sku_id"], unique=False)
    op.create_table(
        "order_events",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=60), nullable=False),
        sa.Column("order_version", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name=op.f("fk_order_events_order_id_orders")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_order_events_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_events")),
        sa.UniqueConstraint("user_id", "sequence", name=op.f("uq_order_events_user_id")),
    )
    op.create_table(
        "order_items",
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("sku_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("attributes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("price_fen", sa.BigInteger(), nullable=False),
        sa.Column("returned_quantity", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "quantity > 0 AND returned_quantity >= 0 AND returned_quantity <= quantity",
            name=op.f("ck_order_items_return_quantity"),
        ),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name=op.f("fk_order_items_order_id_orders")
        ),
        sa.ForeignKeyConstraint(["sku_id"], ["skus.id"], name=op.f("fk_order_items_sku_id_skus")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_items")),
        sa.UniqueConstraint("order_id", "sku_id", name=op.f("uq_order_items_order_id")),
    )
    op.create_index(op.f("ix_order_items_order_id"), "order_items", ["order_id"], unique=False)
    op.create_table(
        "payment_intents",
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("provider_reference", sa.String(length=100), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name=op.f("fk_payment_intents_order_id_orders")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payment_intents")),
        sa.UniqueConstraint("order_id", name=op.f("uq_payment_intents_order_id")),
    )
    op.create_table(
        "refunds",
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("reference", sa.String(length=120), nullable=False),
        sa.Column("amount_fen", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount_fen > 0", name=op.f("ck_refunds_positive_amount")),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name=op.f("fk_refunds_order_id_orders")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refunds")),
        sa.UniqueConstraint("reference", name=op.f("uq_refunds_reference")),
    )
    op.create_index(op.f("ix_refunds_order_id"), "refunds", ["order_id"], unique=False)
    op.create_table(
        "shipments",
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("carrier", sa.String(length=80), nullable=False),
        sa.Column("tracking_number", sa.String(length=100), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name=op.f("fk_shipments_order_id_orders")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shipments")),
        sa.UniqueConstraint("order_id", name=op.f("uq_shipments_order_id")),
    )
    op.create_table(
        "provider_events",
        sa.Column("event_key", sa.String(length=100), nullable=False),
        sa.Column("intent_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["intent_id"],
            ["payment_intents.id"],
            name=op.f("fk_provider_events_intent_id_payment_intents"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_provider_events")),
        sa.UniqueConstraint("event_key", name=op.f("uq_provider_events_event_key")),
    )
    op.create_table(
        "return_requests",
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("inspection_note", sa.String(length=500), nullable=True),
        sa.Column("refund_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name=op.f("fk_return_requests_order_id_orders")
        ),
        sa.ForeignKeyConstraint(
            ["refund_id"], ["refunds.id"], name=op.f("fk_return_requests_refund_id_refunds")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_return_requests_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_return_requests")),
    )
    op.create_index(
        op.f("ix_return_requests_order_id"), "return_requests", ["order_id"], unique=False
    )
    op.create_index(
        op.f("ix_return_requests_user_id"), "return_requests", ["user_id"], unique=False
    )
    op.create_table(
        "return_items",
        sa.Column("return_id", sa.Uuid(), nullable=False),
        sa.Column("order_item_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["order_item_id"],
            ["order_items.id"],
            name=op.f("fk_return_items_order_item_id_order_items"),
        ),
        sa.ForeignKeyConstraint(
            ["return_id"],
            ["return_requests.id"],
            name=op.f("fk_return_items_return_id_return_requests"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_return_items")),
        sa.UniqueConstraint("return_id", "order_item_id", name=op.f("uq_return_items_return_id")),
    )
    op.create_index(op.f("ix_return_items_return_id"), "return_items", ["return_id"], unique=False)


def downgrade() -> None:
    # 回退会删除业务表，只供空库开发回退；有业务数据时先备份并使用前向修复。
    op.drop_index(op.f("ix_return_items_return_id"), table_name="return_items")
    op.drop_table("return_items")
    op.drop_index(op.f("ix_return_requests_user_id"), table_name="return_requests")
    op.drop_index(op.f("ix_return_requests_order_id"), table_name="return_requests")
    op.drop_table("return_requests")
    op.drop_table("provider_events")
    op.drop_table("shipments")
    op.drop_index(op.f("ix_refunds_order_id"), table_name="refunds")
    op.drop_table("refunds")
    op.drop_table("payment_intents")
    op.drop_index(op.f("ix_order_items_order_id"), table_name="order_items")
    op.drop_table("order_items")
    op.drop_table("order_events")
    op.drop_index(op.f("ix_stock_movements_sku_id"), table_name="stock_movements")
    op.drop_table("stock_movements")
    op.drop_table("stock_items")
    op.drop_index("ix_orders_user_created", table_name="orders")
    op.drop_index("ix_orders_expiry", table_name="orders")
    op.drop_table("orders")
    op.drop_table("cart_items")
    op.drop_index(op.f("ix_skus_product_id"), table_name="skus")
    op.drop_table("skus")
    op.drop_index(op.f("ix_quotes_user_id"), table_name="quotes")
    op.drop_table("quotes")
    op.drop_table("favorites")
    op.drop_index(op.f("ix_sessions_user_id"), table_name="sessions")
    op.drop_index(op.f("ix_sessions_staff_id"), table_name="sessions")
    op.drop_table("sessions")
    op.drop_index(op.f("ix_products_category_id"), table_name="products")
    op.drop_table("products")
    op.drop_index("ix_outbox_due", table_name="outbox_events")
    op.drop_table("outbox_events")
    op.drop_table("event_sequences")
    op.drop_index(op.f("ix_addresses_user_id"), table_name="addresses")
    op.drop_table("addresses")
    op.drop_table("users")
    op.drop_table("staff")
    op.drop_table("message_failures")
    op.drop_table("idempotency_records")
    op.drop_table("categories")
    op.drop_index(op.f("ix_audit_events_action"), table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_operations_due", table_name="async_operations")
    op.drop_table("async_operations")
