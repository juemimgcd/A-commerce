"""店铺经营资料与受确认的业务操作。金额使用分，历史证据保存独立快照。"""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from a_commerce.models.base import Record


class BusinessReport(Record):
    __tablename__ = "merchant_reports"
    shop_id: Mapped[UUID] = mapped_column(ForeignKey("shops.id"), index=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    digest: Mapped[str] = mapped_column(String(64))


class ReportEvidence(Record):
    __tablename__ = "merchant_report_evidence"
    report_id: Mapped[UUID] = mapped_column(ForeignKey("merchant_reports.id"), index=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)


class OrderExpense(Record):
    __tablename__ = "merchant_order_expenses"
    __table_args__ = (
        CheckConstraint("amount_fen IS NULL OR amount_fen >= 0", name="expense_amount"),
        CheckConstraint("quality IN ('actual','estimated','unknown')", name="expense_quality"),
        CheckConstraint("category IN ('delivery','packaging','payment')", name="expense_category"),
        UniqueConstraint("order_id", "category", "revision"),
    )
    shop_id: Mapped[UUID] = mapped_column(ForeignKey("shops.id"), index=True)
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id"), index=True)
    category: Mapped[str] = mapped_column(String(16))
    quality: Mapped[str] = mapped_column(String(16))
    amount_fen: Mapped[int | None] = mapped_column(BigInteger)
    complete: Mapped[bool]
    revision: Mapped[int]
    note: Mapped[str] = mapped_column(String(300))


class RestockRule(Record):
    __tablename__ = "merchant_restock_rules"
    shop_id: Mapped[UUID] = mapped_column(ForeignKey("shops.id"), index=True)
    sku_id: Mapped[UUID] = mapped_column(ForeignKey("skus.id"), unique=True)
    version: Mapped[int] = mapped_column(default=1)
    window_days: Mapped[int]
    lead_days: Mapped[int]
    review_days: Mapped[int]
    safety_stock: Mapped[int]
    low_stock: Mapped[int]
    slow_sales: Mapped[int]


class RestockTask(Record):
    __tablename__ = "merchant_restock_tasks"
    __table_args__ = (
        CheckConstraint("quantity > 0 AND received >= 0 AND received <= quantity", name="quantity"),
        CheckConstraint(
            "status IN ('draft','confirmed','in_progress',"
            "'partially_received','completed','cancelled')",
            name="task_status",
        ),
    )
    shop_id: Mapped[UUID] = mapped_column(ForeignKey("shops.id"), index=True)
    sku_id: Mapped[UUID] = mapped_column(ForeignKey("skus.id"), index=True)
    quantity: Mapped[int]
    received: Mapped[int] = mapped_column(default=0)
    expected_on: Mapped[date | None]
    status: Mapped[str] = mapped_column(String(24), default="draft")
    version: Mapped[int] = mapped_column(default=1)
    note: Mapped[str] = mapped_column(String(300))
    evidence: Mapped[dict[str, object]] = mapped_column(JSONB)


class RestockReceipt(Record):
    __tablename__ = "merchant_restock_receipts"
    __table_args__ = (CheckConstraint("quantity > 0", name="receipt_quantity"),)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("merchant_restock_tasks.id"), index=True)
    quantity: Mapped[int]
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))


class CustomerSegment(Record):
    __tablename__ = "merchant_customer_segments"
    shop_id: Mapped[UUID] = mapped_column(ForeignKey("shops.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    rules: Mapped[dict[str, object]] = mapped_column(JSONB)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    member_count: Mapped[int]
    active: Mapped[bool] = mapped_column(default=True)


class SegmentMember(Record):
    __tablename__ = "merchant_segment_members"
    __table_args__ = (UniqueConstraint("segment_id", "user_id"),)
    segment_id: Mapped[UUID] = mapped_column(
        ForeignKey("merchant_customer_segments.id"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))


class MerchantPromotion(Record):
    __tablename__ = "merchant_promotions"
    __table_args__ = (
        CheckConstraint(
            "price_fen > 0 AND reference_price_fen > price_fen AND budget_fen > 0 "
            "AND reserved_fen >= 0 AND consumed_fen >= 0 "
            "AND reserved_fen + consumed_fen <= budget_fen",
            name="promotion_budget",
        ),
        CheckConstraint("quantity > 0 AND ends_at > starts_at", name="promotion_range"),
        CheckConstraint("status IN ('draft','published','stopped')", name="promotion_status"),
    )
    shop_id: Mapped[UUID] = mapped_column(ForeignKey("shops.id"), index=True)
    sku_id: Mapped[UUID] = mapped_column(ForeignKey("skus.id"), index=True)
    segment_id: Mapped[UUID | None] = mapped_column(ForeignKey("merchant_customer_segments.id"))
    version: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    price_fen: Mapped[int] = mapped_column(BigInteger)
    reference_price_fen: Mapped[int] = mapped_column(BigInteger)
    sku_version: Mapped[int]
    cost_fen: Mapped[int | None] = mapped_column(BigInteger)
    cost_version: Mapped[int]
    unit_expense_fen: Mapped[int | None] = mapped_column(BigInteger)
    minimum_margin_fen: Mapped[int] = mapped_column(BigInteger)
    budget_fen: Mapped[int] = mapped_column(BigInteger)
    reserved_fen: Mapped[int] = mapped_column(BigInteger, default=0)
    consumed_fen: Mapped[int] = mapped_column(BigInteger, default=0)
    quantity: Mapped[int]
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approval_digest: Mapped[str | None] = mapped_column(String(64))
    note: Mapped[str] = mapped_column(String(300))


class PromotionClaim(Record):
    __tablename__ = "merchant_promotion_claims"
    __table_args__ = (
        CheckConstraint("quantity > 0 AND discount_fen > 0", name="claim_amount"),
        CheckConstraint("status IN ('reserved','consumed','released')", name="claim_status"),
    )
    promotion_id: Mapped[UUID] = mapped_column(ForeignKey("merchant_promotions.id"), index=True)
    order_item_id: Mapped[UUID] = mapped_column(ForeignKey("order_items.id"), unique=True)
    quantity: Mapped[int]
    discount_fen: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16), default="reserved")
    promotion_version: Mapped[int]
