"""业务表与数据库底线约束。金额均为整数分，JSON 仅保存有 schema 的快照。"""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from a_commerce.models.base import Record
from a_commerce.models.shops import LEGACY_SHOP_ID
from a_commerce.models.snapshots import QuoteItem


class User(Record):
    __tablename__ = "users"
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    active: Mapped[bool] = mapped_column(default=True)
    event_sequence: Mapped[int] = mapped_column(BigInteger, default=0)


class Staff(Record):
    __tablename__ = "staff"
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    active: Mapped[bool] = mapped_column(default=True)
    permissions: Mapped[list[str]] = mapped_column(JSONB)


class Session(Record):
    __tablename__ = "sessions"
    __table_args__ = (
        CheckConstraint("(user_id IS NULL) <> (staff_id IS NULL)", name="one_principal"),
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    staff_id: Mapped[UUID | None] = mapped_column(ForeignKey("staff.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(default=False)


class Address(Record):
    __tablename__ = "addresses"
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    version: Mapped[int] = mapped_column(default=1)
    recipient: Mapped[str] = mapped_column(String(80))
    phone: Mapped[str] = mapped_column(String(32))
    region: Mapped[str] = mapped_column(String(120))
    detail: Mapped[str] = mapped_column(String(300))


class Category(Record):
    __tablename__ = "categories"
    name: Mapped[str] = mapped_column(String(100), unique=True)


class Shop(Record):
    __tablename__ = "shops"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'suspended', 'closed')", name="shop_status"),
    )
    owner_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(16), default="suspended")
    return_address: Mapped[dict[str, str] | None] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Product(Record):
    __tablename__ = "products"
    shop_id: Mapped[UUID] = mapped_column(
        ForeignKey("shops.id"), index=True, default=LEGACY_SHOP_ID
    )
    category_id: Mapped[UUID] = mapped_column(ForeignKey("categories.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    brand: Mapped[str] = mapped_column(String(100), default="")
    description: Mapped[str] = mapped_column(String(4000), default="")
    active: Mapped[bool] = mapped_column(default=False)
    version: Mapped[int] = mapped_column(default=1)


class SKU(Record):
    __tablename__ = "skus"
    __table_args__ = (
        CheckConstraint("price_fen > 0", name="positive_price"),
        CheckConstraint("cost_fen IS NULL OR cost_fen >= 0", name="nonnegative_cost"),
    )
    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"), index=True)
    code: Mapped[str] = mapped_column(String(80), unique=True)
    attributes: Mapped[dict[str, str]] = mapped_column(JSONB)
    price_fen: Mapped[int] = mapped_column(BigInteger)
    cost_fen: Mapped[int | None] = mapped_column(BigInteger)
    cost_version: Mapped[int] = mapped_column(default=1)
    version: Mapped[int] = mapped_column(default=1)
    active: Mapped[bool] = mapped_column(default=True)


class FlashSale(Record):
    __tablename__ = "flash_sales"
    __table_args__ = (
        CheckConstraint("price_fen > 0", name="positive_price"),
        CheckConstraint("stock >= 0 AND claimed >= 0 AND claimed <= stock", name="quota_balance"),
        CheckConstraint("ends_at > starts_at", name="time_range"),
        Index("ix_flash_sales_active_ends", "active", "ends_at"),
    )
    sku_id: Mapped[UUID] = mapped_column(ForeignKey("skus.id"), index=True)
    merchant_promotion_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("merchant_promotions.id"), unique=True
    )
    price_fen: Mapped[int] = mapped_column(BigInteger)
    stock: Mapped[int]
    # 未取消订单占用的额度（含待付款和已成交），取消时与实物库存一起释放。
    claimed: Mapped[int] = mapped_column(default=0)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(default=False)
    version: Mapped[int] = mapped_column(default=1)


class Stock(Record):
    __tablename__ = "stock_items"
    __table_args__ = (
        CheckConstraint(
            "on_hand >= 0 AND reserved >= 0 AND allocated >= 0 AND reserved + allocated <= on_hand",
            name="stock_balance",
        ),
    )
    sku_id: Mapped[UUID] = mapped_column(ForeignKey("skus.id"), unique=True)
    on_hand: Mapped[int] = mapped_column(default=0)
    reserved: Mapped[int] = mapped_column(default=0)
    allocated: Mapped[int] = mapped_column(default=0)


class StockMovement(Record):
    __tablename__ = "stock_movements"
    reference: Mapped[str] = mapped_column(String(160), unique=True)
    sku_id: Mapped[UUID] = mapped_column(ForeignKey("skus.id"), index=True)
    on_hand_delta: Mapped[int]
    reserved_delta: Mapped[int]
    allocated_delta: Mapped[int]
    reason: Mapped[str] = mapped_column(String(300))


class CartItem(Record):
    __tablename__ = "cart_items"
    __table_args__ = (
        UniqueConstraint("user_id", "sku_id"),
        CheckConstraint("quantity > 0 AND quantity <= 99", name="quantity"),
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    sku_id: Mapped[UUID] = mapped_column(ForeignKey("skus.id"))
    quantity: Mapped[int]


class Favorite(Record):
    __tablename__ = "favorites"
    __table_args__ = (UniqueConstraint("user_id", "product_id"),)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"))


class Quote(Record):
    __tablename__ = "quotes"
    shop_id: Mapped[UUID] = mapped_column(ForeignKey("shops.id"), default=LEGACY_SHOP_ID)
    shop_version: Mapped[int] = mapped_column(default=1)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    address_id: Mapped[UUID] = mapped_column(ForeignKey("addresses.id"))
    address_version: Mapped[int]
    address_snapshot: Mapped[dict[str, str]] = mapped_column(JSONB)
    items: Mapped[list[QuoteItem]] = mapped_column(JSONB)
    total_fen: Mapped[int] = mapped_column(BigInteger)
    shipping_fen: Mapped[int] = mapped_column(BigInteger)
    policy: Mapped[dict[str, str | int]] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Order(Record):
    __tablename__ = "orders"
    __table_args__ = (
        Index("ix_orders_user_created", "user_id", "created_at", "id"),
        Index("ix_orders_expiry", "status", "expires_at"),
        Index("ix_orders_shop_created", "shop_id", "created_at", "id"),
        Index("ix_orders_shop_paid", "shop_id", "paid_at"),
        CheckConstraint(
            "total_fen > 0 AND refund_reserved_fen >= 0 AND refunded_fen >= 0 "
            "AND refund_reserved_fen + refunded_fen <= total_fen",
            name="refund_balance",
        ),
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    shop_id: Mapped[UUID] = mapped_column(ForeignKey("shops.id"), default=LEGACY_SHOP_ID)
    return_address: Mapped[dict[str, str] | None] = mapped_column(JSONB)
    quote_id: Mapped[UUID] = mapped_column(ForeignKey("quotes.id"), unique=True)
    status: Mapped[str] = mapped_column(String(32), default="pending_payment")
    payment_status: Mapped[str] = mapped_column(String(32), default="preparing")
    fulfillment_status: Mapped[str] = mapped_column(String(32), default="unfulfilled")
    version: Mapped[int] = mapped_column(default=1)
    total_fen: Mapped[int] = mapped_column(BigInteger)
    shipping_fen: Mapped[int] = mapped_column(BigInteger)
    refund_reserved_fen: Mapped[int] = mapped_column(BigInteger, default=0)
    refunded_fen: Mapped[int] = mapped_column(BigInteger, default=0)
    address_snapshot: Mapped[dict[str, str]] = mapped_column(JSONB)
    policy: Mapped[dict[str, str | int]] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OrderItem(Record):
    __tablename__ = "order_items"
    __table_args__ = (
        UniqueConstraint("order_id", "sku_id"),
        CheckConstraint("cost_fen IS NULL OR cost_fen >= 0", name="nonnegative_cost"),
        CheckConstraint(
            "quantity > 0 AND returned_quantity >= 0 AND returned_quantity <= quantity",
            name="return_quantity",
        ),
    )
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id"), index=True)
    sku_id: Mapped[UUID] = mapped_column(ForeignKey("skus.id"))
    flash_sale_id: Mapped[UUID | None] = mapped_column(ForeignKey("flash_sales.id"), index=True)
    merchant_promotion_version: Mapped[int | None]
    title: Mapped[str] = mapped_column(String(200))
    attributes: Mapped[dict[str, str]] = mapped_column(JSONB)
    quantity: Mapped[int]
    price_fen: Mapped[int] = mapped_column(BigInteger)
    returned_quantity: Mapped[int] = mapped_column(default=0)
    product_id: Mapped[UUID | None] = mapped_column(ForeignKey("products.id"))
    category_id: Mapped[UUID | None] = mapped_column(ForeignKey("categories.id"))
    category_name: Mapped[str | None] = mapped_column(String(100))
    regular_price_fen: Mapped[int | None] = mapped_column(BigInteger)
    cost_fen: Mapped[int | None] = mapped_column(BigInteger)


class PaymentIntent(Record):
    __tablename__ = "payment_intents"
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id"), unique=True)
    # 只记录提供方引用；支付链接在受认证的入口查询，不能信任回调携带的 URL。
    provider_reference: Mapped[str | None] = mapped_column(String(100))


class IdempotencyRecord(Record):
    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("actor_id", "action", "key"),)
    actor_id: Mapped[UUID]
    action: Mapped[str] = mapped_column(String(60))
    key: Mapped[str] = mapped_column(String(128))
    fingerprint: Mapped[str] = mapped_column(String(64))
    result_id: Mapped[UUID]


class Refund(Record):
    __tablename__ = "refunds"
    __table_args__ = (CheckConstraint("amount_fen > 0", name="positive_amount"),)
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id"), index=True)
    reference: Mapped[str] = mapped_column(String(120), unique=True)
    amount_fen: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    shipping_fen: Mapped[int | None] = mapped_column(BigInteger)


class RefundItem(Record):
    __tablename__ = "refund_items"
    __table_args__ = (
        UniqueConstraint("refund_id", "order_item_id"),
        CheckConstraint("amount_fen > 0 AND quantity >= 0", name="refund_item_balance"),
    )
    refund_id: Mapped[UUID] = mapped_column(ForeignKey("refunds.id"), index=True)
    order_item_id: Mapped[UUID] = mapped_column(ForeignKey("order_items.id"), index=True)
    amount_fen: Mapped[int] = mapped_column(BigInteger)
    quantity: Mapped[int]


class ReturnRequest(Record):
    __tablename__ = "return_requests"
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id"), index=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="requested")
    reason: Mapped[str] = mapped_column(String(500))
    inspection_note: Mapped[str | None] = mapped_column(String(500))
    refund_id: Mapped[UUID | None] = mapped_column(ForeignKey("refunds.id"))
    carrier: Mapped[str | None] = mapped_column(String(80))
    tracking_number: Mapped[str | None] = mapped_column(String(100))
    inspected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resellable: Mapped[bool | None]


class ReturnItem(Record):
    __tablename__ = "return_items"
    __table_args__ = (UniqueConstraint("return_id", "order_item_id"),)
    return_id: Mapped[UUID] = mapped_column(ForeignKey("return_requests.id"), index=True)
    order_item_id: Mapped[UUID] = mapped_column(ForeignKey("order_items.id"))
    quantity: Mapped[int]


class Shipment(Record):
    __tablename__ = "shipments"
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id"), unique=True)
    carrier: Mapped[str] = mapped_column(String(80))
    tracking_number: Mapped[str] = mapped_column(String(100))


class Event(Record):
    __tablename__ = "order_events"
    __table_args__ = (UniqueConstraint("user_id", "sequence"),)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id"))
    sequence: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(60))
    order_version: Mapped[int]


class EventSequence(Record):
    __tablename__ = "event_sequences"
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), unique=True)
    value: Mapped[int] = mapped_column(BigInteger, default=0)
    # 已归档的连续前缀；小于此值的消费游标必须先恢复订单快照。
    retained_after: Mapped[int] = mapped_column(BigInteger, default=0)
    archive_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Audit(Record):
    __tablename__ = "audit_events"
    actor_id: Mapped[UUID | None]
    action: Mapped[str] = mapped_column(String(80), index=True)
    resource_id: Mapped[UUID]
    detail: Mapped[str] = mapped_column(String(500))
    actor_kind: Mapped[str | None] = mapped_column(String(16))
    shop_id: Mapped[UUID | None] = mapped_column(ForeignKey("shops.id"), index=True)


class Operation(Record):
    __tablename__ = "async_operations"
    __table_args__ = (
        UniqueConstraint("kind", "resource_id"),
        Index("ix_operations_due", "status", "next_attempt_at"),
    )
    kind: Mapped[str] = mapped_column(String(32))
    resource_id: Mapped[UUID]
    status: Mapped[str] = mapped_column(String(32), default="pending")
    dispatch_version: Mapped[int] = mapped_column(default=1)
    fence: Mapped[int] = mapped_column(default=0)
    attempts: Mapped[int] = mapped_column(default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(120))


class Outbox(Record):
    __tablename__ = "outbox_events"
    __table_args__ = (
        UniqueConstraint("operation_id", "dispatch_version"),
        Index("ix_outbox_due", "sent_at", "available_at"),
    )
    operation_id: Mapped[UUID] = mapped_column(ForeignKey("async_operations.id"))
    dispatch_version: Mapped[int]
    kind: Mapped[str] = mapped_column(String(32))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[UUID | None]
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(default=0)


class ProviderEvent(Record):
    __tablename__ = "provider_events"
    event_key: Mapped[str] = mapped_column(String(100), unique=True)
    intent_id: Mapped[UUID] = mapped_column(ForeignKey("payment_intents.id"))
    state: Mapped[str] = mapped_column(String(32))


class MessageFailure(Record):
    __tablename__ = "message_failures"
    digest: Mapped[str] = mapped_column(String(64), unique=True)
    message_id: Mapped[str | None] = mapped_column(String(100))
    reason: Mapped[str] = mapped_column(String(200))


class NotificationPreference(Record):
    __tablename__ = "notification_preferences"
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), unique=True)
    in_app: Mapped[bool] = mapped_column(default=True)
    email: Mapped[bool] = mapped_column(default=False)


class Notification(Record):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_user_created", "user_id", "created_at", "id"),)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    event_id: Mapped[UUID] = mapped_column(ForeignKey("order_events.id"), unique=True)
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id"))
    kind: Mapped[str] = mapped_column(String(60))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FinancialEntry(Record):
    __tablename__ = "financial_entries"
    reference: Mapped[str] = mapped_column(String(120), unique=True)
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    amount_fen: Mapped[int] = mapped_column(BigInteger)


class DailyReport(Record):
    __tablename__ = "daily_reports"
    day: Mapped[date] = mapped_column(unique=True)
    sales_fen: Mapped[int] = mapped_column(BigInteger, default=0)
    refunds_fen: Mapped[int] = mapped_column(BigInteger, default=0)
    paid_orders: Mapped[int] = mapped_column(default=0)


class ProductDailyReport(Record):
    __tablename__ = "product_daily_reports"
    __table_args__ = (UniqueConstraint("day", "sku_id"),)
    day: Mapped[date]
    sku_id: Mapped[UUID] = mapped_column(ForeignKey("skus.id"))
    quantity: Mapped[int] = mapped_column(default=0)
    sales_fen: Mapped[int] = mapped_column(BigInteger, default=0)


class ShippingEvent(Record):
    __tablename__ = "shipment_events"
    event_key: Mapped[str] = mapped_column(String(100), unique=True)
    shipment_id: Mapped[UUID] = mapped_column(ForeignKey("shipments.id"), index=True)
    state: Mapped[str] = mapped_column(String(32))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ShippingRule(Record):
    __tablename__ = "shipping_rules"
    region: Mapped[str] = mapped_column(String(120), unique=True)
    fee_fen: Mapped[int] = mapped_column(BigInteger)
    free_above_fen: Mapped[int | None] = mapped_column(BigInteger)
    version: Mapped[int] = mapped_column(default=1)


class PasswordReset(Record):
    __tablename__ = "password_resets"
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used: Mapped[bool] = mapped_column(default=False)


class MailJob(Record):
    __tablename__ = "mail_jobs"
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    event_id: Mapped[UUID | None] = mapped_column(ForeignKey("order_events.id"), unique=True)
    reset_id: Mapped[UUID | None] = mapped_column(ForeignKey("password_resets.id"), unique=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")


class ExportJob(Record):
    __tablename__ = "export_jobs"
    staff_id: Mapped[UUID] = mapped_column(ForeignKey("staff.id"), index=True)
    start: Mapped[date]
    end: Mapped[date]
    status: Mapped[str] = mapped_column(String(32), default="pending")
    file_key: Mapped[str | None] = mapped_column(String(100))


class Media(Record):
    __tablename__ = "product_media"
    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"), index=True)
    staff_id: Mapped[UUID | None] = mapped_column(ForeignKey("staff.id"))
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    storage_key: Mapped[str] = mapped_column(String(160), unique=True)
    content_type: Mapped[str] = mapped_column(String(32))
    size_bytes: Mapped[int]
    backend: Mapped[str] = mapped_column(String(16))


class CatalogChange(Record):
    __tablename__ = "catalog_changes"
    __table_args__ = (UniqueConstraint("product_id", "version"),)
    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"))
    version: Mapped[int]
    snapshot: Mapped[dict[str, object]] = mapped_column(JSONB)


class ScheduledTask(Record):
    __tablename__ = "scheduled_tasks"
    name: Mapped[str] = mapped_column(String(32), unique=True)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    operation_id: Mapped[UUID | None] = mapped_column(ForeignKey("async_operations.id"))


class ReconciliationRun(Record):
    __tablename__ = "reconciliation_runs"
    status: Mapped[str] = mapped_column(String(32), default="provider")
    # 提供方资金流水序号形成固定快照，每个消息只处理一页，提交后可从游标恢复。
    through_sequence: Mapped[int | None] = mapped_column(BigInteger)
    after_sequence: Mapped[int] = mapped_column(BigInteger, default=0)
    local_cursor: Mapped[UUID | None]
    entries_checked: Mapped[int] = mapped_column(default=0)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReconciliationEntry(Record):
    __tablename__ = "reconciliation_entries"
    __table_args__ = (UniqueConstraint("run_id", "kind", "resource_id"),)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("reconciliation_runs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    resource_id: Mapped[UUID]
    evidence: Mapped[dict[str, object]] = mapped_column(JSONB)


class ReconciliationCase(Record):
    __tablename__ = "reconciliation_cases"
    __table_args__ = (UniqueConstraint("kind", "resource_id"),)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("reconciliation_runs.id"))
    kind: Mapped[str] = mapped_column(String(16))
    resource_id: Mapped[UUID]
    order_id: Mapped[UUID | None] = mapped_column(ForeignKey("orders.id"))
    status: Mapped[str] = mapped_column(String(32), default="open", index=True)
    reason: Mapped[str] = mapped_column(String(80))
    evidence: Mapped[dict[str, object]] = mapped_column(JSONB)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # revision 用于并发比较并交换；reviewed_at 阻止旧轮次的固定账单覆盖人工复核。
    revision: Mapped[int] = mapped_column(default=1)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_note: Mapped[str | None] = mapped_column(String(500))


class EventSnapshot(Record):
    __tablename__ = "event_snapshots"
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    through_sequence: Mapped[int] = mapped_column(BigInteger)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class EventSnapshotItem(Record):
    __tablename__ = "event_snapshot_items"
    __table_args__ = (UniqueConstraint("snapshot_id", "order_id"),)
    snapshot_id: Mapped[UUID] = mapped_column(ForeignKey("event_snapshots.id"), index=True)
    order_id: Mapped[UUID]
    snapshot: Mapped[dict[str, object]] = mapped_column(JSONB)


class EventArchive(Record):
    __tablename__ = "event_archive"
    __table_args__ = (UniqueConstraint("user_id", "sequence"),)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id"), index=True)
    sequence: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(60))
    order_version: Mapped[int]
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class StoredObject(Record):
    __tablename__ = "stored_objects"
    storage_key: Mapped[str] = mapped_column(String(160), unique=True)
    backend: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProcessHeartbeat(Record):
    __tablename__ = "process_heartbeats"
    role: Mapped[str] = mapped_column(String(32), index=True)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    stopped: Mapped[bool] = mapped_column(default=False)
