"""运营能力、媒体、通知及增量资金汇总

Revision ID: 0002_operations
Revises: 0001_initial
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_operations"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 先创建新表，再回填首阶段已确认交易；空库升级时回填自然为空。
    op.create_table(
        "daily_reports",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("sales_fen", sa.BigInteger(), nullable=False),
        sa.Column("refunds_fen", sa.BigInteger(), nullable=False),
        sa.Column("paid_orders", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_daily_reports")),
        sa.UniqueConstraint("day", name=op.f("uq_daily_reports_day")),
    )
    op.create_table(
        "shipping_rules",
        sa.Column("region", sa.String(length=120), nullable=False),
        sa.Column("fee_fen", sa.BigInteger(), nullable=False),
        sa.Column("free_above_fen", sa.BigInteger(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shipping_rules")),
        sa.UniqueConstraint("region", name=op.f("uq_shipping_rules_region")),
    )
    op.create_table(
        "export_jobs",
        sa.Column("staff_id", sa.Uuid(), nullable=False),
        sa.Column("start", sa.Date(), nullable=False),
        sa.Column("end", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["staff_id"], ["staff.id"], name=op.f("fk_export_jobs_staff_id_staff")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_export_jobs")),
    )
    op.create_index(op.f("ix_export_jobs_staff_id"), "export_jobs", ["staff_id"], unique=False)
    op.create_table(
        "notification_preferences",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("in_app", sa.Boolean(), nullable=False),
        sa.Column("email", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_notification_preferences_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_preferences")),
        sa.UniqueConstraint("user_id", name=op.f("uq_notification_preferences_user_id")),
    )
    op.create_table(
        "password_resets",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_password_resets_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_password_resets")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_password_resets_token_hash")),
    )
    op.create_index(
        op.f("ix_password_resets_user_id"), "password_resets", ["user_id"], unique=False
    )
    op.create_table(
        "product_media",
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("staff_id", sa.Uuid(), nullable=False),
        sa.Column("storage_key", sa.String(length=160), nullable=False),
        sa.Column("content_type", sa.String(length=32), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("backend", sa.String(length=16), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["product_id"], ["products.id"], name=op.f("fk_product_media_product_id_products")
        ),
        sa.ForeignKeyConstraint(
            ["staff_id"], ["staff.id"], name=op.f("fk_product_media_staff_id_staff")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_product_media")),
        sa.UniqueConstraint("storage_key", name=op.f("uq_product_media_storage_key")),
    )
    op.create_index(
        op.f("ix_product_media_product_id"), "product_media", ["product_id"], unique=False
    )
    op.create_table(
        "product_daily_reports",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("sku_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("sales_fen", sa.BigInteger(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["sku_id"], ["skus.id"], name=op.f("fk_product_daily_reports_sku_id_skus")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_product_daily_reports")),
        sa.UniqueConstraint("day", "sku_id", name=op.f("uq_product_daily_reports_day")),
    )
    op.create_table(
        "financial_entries",
        sa.Column("reference", sa.String(length=120), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("amount_fen", sa.BigInteger(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name=op.f("fk_financial_entries_order_id_orders")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_financial_entries")),
        sa.UniqueConstraint("reference", name=op.f("uq_financial_entries_reference")),
    )
    op.create_index(
        op.f("ix_financial_entries_order_id"), "financial_entries", ["order_id"], unique=False
    )
    op.create_table(
        "mail_jobs",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=True),
        sa.Column("reset_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["event_id"], ["order_events.id"], name=op.f("fk_mail_jobs_event_id_order_events")
        ),
        sa.ForeignKeyConstraint(
            ["reset_id"], ["password_resets.id"], name=op.f("fk_mail_jobs_reset_id_password_resets")
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_mail_jobs_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mail_jobs")),
        sa.UniqueConstraint("event_id", name=op.f("uq_mail_jobs_event_id")),
        sa.UniqueConstraint("reset_id", name=op.f("uq_mail_jobs_reset_id")),
    )
    op.create_table(
        "notifications",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=60), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["event_id"], ["order_events.id"], name=op.f("fk_notifications_event_id_order_events")
        ),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name=op.f("fk_notifications_order_id_orders")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_notifications_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
        sa.UniqueConstraint("event_id", name=op.f("uq_notifications_event_id")),
    )
    op.create_index(
        "ix_notifications_user_created",
        "notifications",
        ["user_id", "created_at", "id"],
        unique=False,
    )
    op.create_table(
        "shipment_events",
        sa.Column("event_key", sa.String(length=100), nullable=False),
        sa.Column("shipment_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["shipment_id"], ["shipments.id"], name=op.f("fk_shipment_events_shipment_id_shipments")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shipment_events")),
        sa.UniqueConstraint("event_key", name=op.f("uq_shipment_events_event_key")),
    )
    op.create_index(
        op.f("ix_shipment_events_shipment_id"), "shipment_events", ["shipment_id"], unique=False
    )
    op.add_column("refunds", sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True))
    # 旧版本未保存退款确认时间，不能伪造提供方完成时间：以迁移核对时间记账。
    # 新版本在确认成功的同一事务写 confirmed_at；支付沿用已有 paid_at。
    op.execute("UPDATE refunds SET confirmed_at = CURRENT_TIMESTAMP WHERE status = 'succeeded'")
    op.execute("""
        INSERT INTO financial_entries (id, created_at, reference, order_id, kind, amount_fen)
        SELECT gen_random_uuid(), o.paid_at, 'payment:' || p.id, o.id, 'payment', o.total_fen
        FROM orders o JOIN payment_intents p ON p.order_id = o.id WHERE o.paid_at IS NOT NULL
    """)
    op.execute("""
        INSERT INTO financial_entries (id, created_at, reference, order_id, kind, amount_fen)
        SELECT gen_random_uuid(), confirmed_at, 'refund:' || id, order_id, 'refund', amount_fen
        FROM refunds WHERE status = 'succeeded'
    """)
    op.execute("""
        INSERT INTO daily_reports (id, created_at, day, sales_fen, refunds_fen, paid_orders)
        SELECT gen_random_uuid(), CURRENT_TIMESTAMP, (created_at AT TIME ZONE 'UTC')::date,
               SUM(CASE WHEN kind = 'payment' THEN amount_fen ELSE 0 END),
               SUM(CASE WHEN kind = 'refund' THEN amount_fen ELSE 0 END),
               COUNT(*) FILTER (WHERE kind = 'payment')
        FROM financial_entries GROUP BY (created_at AT TIME ZONE 'UTC')::date
    """)
    op.execute("""
        INSERT INTO product_daily_reports (id, created_at, day, sku_id, quantity, sales_fen)
        SELECT gen_random_uuid(), CURRENT_TIMESTAMP, (o.paid_at AT TIME ZONE 'UTC')::date,
               i.sku_id, SUM(i.quantity), SUM(i.quantity * i.price_fen)
        FROM orders o JOIN order_items i ON i.order_id = o.id WHERE o.paid_at IS NOT NULL
        GROUP BY (o.paid_at AT TIME ZONE 'UTC')::date, i.sku_id
    """)


def downgrade() -> None:
    # 回退只适合空库开发；已产生通知/报表数据时应优先使用前向修复。
    op.drop_column("refunds", "confirmed_at")
    op.drop_index(op.f("ix_shipment_events_shipment_id"), table_name="shipment_events")
    op.drop_table("shipment_events")
    op.drop_index("ix_notifications_user_created", table_name="notifications")
    op.drop_table("notifications")
    op.drop_table("mail_jobs")
    op.drop_index(op.f("ix_financial_entries_order_id"), table_name="financial_entries")
    op.drop_table("financial_entries")
    op.drop_table("product_daily_reports")
    op.drop_index(op.f("ix_product_media_product_id"), table_name="product_media")
    op.drop_table("product_media")
    op.drop_index(op.f("ix_password_resets_user_id"), table_name="password_resets")
    op.drop_table("password_resets")
    op.drop_table("notification_preferences")
    op.drop_index(op.f("ix_export_jobs_staff_id"), table_name="export_jobs")
    op.drop_table("export_jobs")
    op.drop_table("shipping_rules")
    op.drop_table("daily_reports")
