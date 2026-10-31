"""经营分析、内部草稿和店主确认的有界输入。"""

from datetime import UTC, date, datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, computed_field, model_validator

from a_commerce.schemas.api import Schema, VersionIn


class PeriodIn(Schema):
    start: date
    end: date
    minimum_samples: int = Field(default=5, ge=1, le=10000)
    minimum_change_fen: int = Field(default=1000, ge=0, le=100000000)
    change_percent: int = Field(default=10, ge=1, le=1000)
    ranking: Literal["gross_profit_fen", "paid_quantity", "net_goods_fen", "contribution_fen"] = (
        "gross_profit_fen"
    )


class ExpenseIn(Schema):
    category: Literal["delivery", "packaging", "payment"]
    quality: Literal["actual", "estimated", "unknown"]
    amount_fen: int | None = Field(default=None, ge=0, le=100000000, strict=True)
    complete: bool = False
    expected_revision: int = Field(ge=0)
    note: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def completeness(self) -> "ExpenseIn":
        if (self.quality == "unknown") != (self.amount_fen is None):
            raise ValueError("未知费用留空，其他费用必须填写金额")
        if self.quality == "unknown" and self.complete:
            raise ValueError("未知费用不能声明完整")
        return self


class ExpenseOut(ExpenseIn):
    id: UUID
    order_id: UUID
    revision: int


class RuleIn(Schema):
    window_days: int = Field(default=30, ge=7, le=366)
    lead_days: int = Field(ge=0, le=365)
    review_days: int = Field(ge=1, le=90)
    safety_stock: int = Field(ge=0, le=1000000)
    low_stock: int = Field(ge=0, le=1000000)
    slow_sales: int = Field(default=2, ge=0, le=1000000)
    expected_version: int = Field(default=0, ge=0)


class RiskOut(Schema):
    sku_id: UUID
    title: str
    code: str
    on_hand: int
    reserved: int
    allocated: int
    available: int
    cost_fen: int | None
    rule: RuleIn | None
    adjusted_sales: int
    excluded_cancelled: int
    inspected_returns: int
    coverage_days: float | None
    incoming: int
    suggested_quantity: int | None
    estimated_cost_fen: int | None
    risk: Literal["unconfigured", "low", "slow", "normal", "insufficient"]
    limitations: list[str]


class MerchantInventoryOut(Schema):
    shop_id: UUID
    as_of: datetime
    timezone: str = "Asia/Shanghai"
    basis_version: str = "restock-v1"
    total: int
    items: list[RiskOut]
    next_offset: int | None


class RestockIn(Schema):
    sku_id: UUID
    quantity: int = Field(ge=1, le=1000000)
    expected_on: date | None = None
    note: str = Field(default="补货建议", min_length=1, max_length=300)


class RestockEdit(VersionIn):
    quantity: int = Field(ge=1, le=1000000)
    expected_on: date
    note: str = Field(min_length=1, max_length=300)


class ReceiptIn(VersionIn):
    quantity: int = Field(ge=1, le=1000000)


class RestockOut(Schema):
    id: UUID
    sku_id: UUID
    quantity: int
    received: int
    expected_on: date | None
    status: str
    version: int
    note: str
    evidence: dict[str, object]
    created_at: datetime


class SegmentIn(Schema):
    name: str = Field(default="客户分群", min_length=1, max_length=100)
    window_days: int = Field(default=90, ge=1, le=366)
    min_recency_days: int = Field(default=0, ge=0, le=3650)
    max_recency_days: int | None = Field(default=None, ge=0, le=3650)
    min_orders: int = Field(default=1, ge=1, le=100000)
    min_spending_fen: int | None = Field(default=None, ge=0, le=1000000000)
    max_spending_fen: int | None = Field(default=None, ge=0, le=1000000000)
    category_id: UUID | None = None

    @model_validator(mode="after")
    def bounds(self) -> "SegmentIn":
        if self.max_recency_days is not None and self.max_recency_days < self.min_recency_days:
            raise ValueError("最近购买间隔范围错误")
        if self.min_spending_fen is not None and self.max_spending_fen is not None:
            if self.max_spending_fen < self.min_spending_fen:
                raise ValueError("消费范围错误")
        return self


class SegmentPreview(Schema):
    as_of: datetime
    members: int
    excluded_unknown_spending: int
    median_recency_days: float | None
    paid_orders: int
    known_net_goods_fen: int
    basis_version: str = "paid-rfm-v1"
    rules: SegmentIn


class SegmentOut(Schema):
    id: UUID
    name: str
    rules: dict[str, object]
    as_of: datetime
    member_count: int
    active: bool


class RepeatOut(Schema):
    as_of: datetime
    cohort_buyers: int
    mature_buyers: int
    immature_buyers: int
    repeat_buyers: int
    repeat_rate: float | None
    observation_days: int
    basis_version: str = "paid-repeat-v1"
    note: str = "按支付事实统计，退款不抹除支付次数；未成熟样本不计入分母。"


class PromotionIn(Schema):
    sku_id: UUID
    segment_id: UUID | None = None
    price_fen: int = Field(gt=0, le=100000000, strict=True)
    budget_fen: int = Field(gt=0, le=1000000000, strict=True)
    quantity: int = Field(ge=1, le=1000000)
    starts_at: datetime
    ends_at: datetime
    unit_expense_fen: int | None = Field(default=None, ge=0, le=100000000, strict=True)
    minimum_margin_fen: int = Field(default=0, ge=0, le=100000000, strict=True)
    note: str = Field(default="商家活动", min_length=1, max_length=300)

    @model_validator(mode="after")
    def times(self) -> "PromotionIn":
        if self.starts_at.tzinfo is None or self.ends_at.tzinfo is None:
            raise ValueError("活动时间必须包含时区")
        if self.ends_at <= self.starts_at:
            raise ValueError("结束时间必须晚于开始时间")
        return self


class PromotionOut(Schema):
    id: UUID
    sku_id: UUID
    segment_id: UUID | None
    version: int
    status: str
    price_fen: int
    reference_price_fen: int
    cost_fen: int | None
    unit_expense_fen: int | None
    minimum_margin_fen: int
    budget_fen: int
    reserved_fen: int
    consumed_fen: int
    quantity: int
    starts_at: datetime
    ends_at: datetime
    approved_at: datetime | None
    approved_by: UUID | None
    approval_digest: str | None
    note: str
    validation_issues: list[str] = Field(default_factory=list)

    @computed_field
    def phase(self) -> str:
        if self.status != "published":
            return self.status
        if self.validation_issues:
            return "unavailable"
        current = datetime.now(UTC)
        return (
            "ended"
            if current >= self.ends_at
            else "scheduled"
            if current < self.starts_at
            else "active"
        )


class PromotionReportOut(Schema):
    promotion: PromotionOut
    flash_sale_id: UUID | None
    as_of: datetime
    reserved_quantity: int
    consumed_quantity: int
    released_quantity: int
    refunded_goods_fen: int
    net_discount_fen: int
    performance: list["SkuPerformance"]
    limitations: list[str]
    executions: list["OperationLogOut"]


class OperationLogOut(Schema):
    id: UUID
    created_at: datetime
    action: str
    detail: str
    actor_id: UUID | None
    actor_kind: str | None


class EmergencyStopIn(VersionIn):
    reason: str = Field(min_length=1, max_length=300)


class SimulationOut(Schema):
    reference_price_fen: int
    discount_fen: int
    unit_margin_fen: int | None
    margin_basis: str
    max_units: int
    scenarios: list[dict[str, int | None]]
    blockers: list[str]


class SkuPerformance(Schema):
    sku_id: UUID
    product_id: UUID | None
    title: str
    paid_quantity: int
    net_goods_fen: int | None
    known_net_goods_fen: int
    known_cost_fen: int
    gross_profit_fen: int | None
    contribution_fen: int | None
    estimated_contribution_fen: int | None
    line_count: int
    profit_line_count: int
    contribution_line_count: int


class DiagnosticsOut(Schema):
    report_id: UUID
    shop_id: UUID
    start: date
    end: date
    as_of: datetime
    currency: str = "CNY"
    timezone: str = "Asia/Shanghai"
    basis_version: str = "diagnostics-v1"
    current: list[SkuPerformance]
    previous: list[SkuPerformance]
    total_skus: int
    next_offset: int | None
    summary: dict[str, int | float | None]
    anomalies: list[str]
    limitations: list[str]
    breakdown: dict[str, object]
    decomposition: list["MerchantChangeOut"] = Field(default_factory=list)
    decomposition_total: int = 0


class MerchantChangeOut(Schema):
    dimension: str
    name: str
    metric: str
    current_fen: int | None
    previous_fen: int | None
    delta_fen: int | None


class EvidenceOut(Schema):
    report_id: UUID
    items: list[dict[str, object]]
    next_cursor: UUID | None


class ReportIndex(Schema):
    report_id: UUID
    start: date
    end: date
    as_of: datetime
