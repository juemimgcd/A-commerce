"""HTTP 输入与输出契约；禁止多余字段，避免客户端越权指定内部状态。"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, model_validator

from a_commerce.conf.shop import ReturnAddress
from a_commerce.models.snapshots import QuoteItem

Quantity = Annotated[int, Field(strict=True, ge=1, le=99)]
Money = Annotated[int, Field(strict=True, ge=1, le=100000000)]


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class Credentials(Schema):
    email: EmailStr
    password: SecretStr = Field(min_length=12, max_length=128)


class TokenOut(Schema):
    token: str
    expires_at: datetime
    token_type: Literal["bearer"] = "bearer"


class AccountOut(Schema):
    id: UUID
    email: str
    active: bool


class SessionOut(Schema):
    id: UUID
    created_at: datetime
    expires_at: datetime
    revoked: bool
    current: bool = False


class PasswordChange(Schema):
    current_password: SecretStr = Field(max_length=128)
    new_password: SecretStr = Field(min_length=12, max_length=128)


class AddressIn(Schema):
    recipient: str = Field(min_length=1, max_length=80)
    phone: str = Field(pattern=r"^[+0-9 -]{5,32}$")
    region: str = Field(min_length=1, max_length=120)
    detail: str = Field(min_length=1, max_length=300)


class AddressOut(AddressIn):
    id: UUID
    version: int


class CategoryIn(Schema):
    name: str = Field(min_length=1, max_length=100)


class CategoryOut(CategoryIn):
    id: UUID


class ProductIn(Schema):
    category_id: UUID
    title: str = Field(min_length=1, max_length=200)
    brand: str = Field(default="", max_length=100)
    description: str = Field(default="", max_length=4000)
    active: bool = False


class SKUIn(Schema):
    code: str = Field(min_length=1, max_length=80)
    price_fen: Money
    attributes: dict[str, str] = Field(default_factory=dict, max_length=20)
    active: bool = True

    @model_validator(mode="after")
    def bounded_attributes(self) -> "SKUIn":
        if any(len(k) > 80 or len(v) > 200 for k, v in self.attributes.items()):
            raise ValueError("规格名称最长 80 字符，规格值最长 200 字符")
        return self


class SKUOut(SKUIn):
    id: UUID
    product_id: UUID
    version: int
    available: int


class ProductOut(ProductIn):
    id: UUID
    version: int
    skus: list[SKUOut]
    cover_media_id: UUID | None = None


class ProductPage(Schema):
    items: list[ProductOut]
    next_cursor: UUID | None = None


class StockAdjustment(Schema):
    delta: int = Field(strict=True, ge=-1000000, le=1000000)
    reason: str = Field(min_length=1, max_length=300)


class StockOut(Schema):
    sku_id: UUID
    on_hand: int
    reserved: int
    allocated: int


class CartIn(Schema):
    quantity: int = Field(strict=True, ge=0, le=99)


class CartOut(Schema):
    sku_id: UUID
    quantity: int
    title: str
    price_fen: int
    available: int
    active: bool
    product_id: UUID
    attributes: dict[str, str]
    cover_media_id: UUID | None = None


class QuoteLine(Schema):
    sku_id: UUID
    quantity: Quantity
    flash_sale_id: UUID | None = None


class QuoteIn(Schema):
    address_id: UUID
    items: list[QuoteLine] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def unique_skus(self) -> "QuoteIn":
        if len({i.sku_id for i in self.items}) != len(self.items):
            raise ValueError("同一 SKU 只能出现一次")
        return self


class QuoteOut(Schema):
    id: UUID
    items: list[QuoteItem]
    address_snapshot: dict[str, str]
    shipping_fen: int
    total_fen: int
    expires_at: datetime
    policy: dict[str, str | int]


class OrderIn(Schema):
    quote_id: UUID


class OrderLineOut(Schema):
    id: UUID
    sku_id: UUID
    title: str
    attributes: dict[str, str]
    price_fen: int
    quantity: int
    returned_quantity: int
    flash_sale_id: UUID | None = None


class OrderOut(Schema):
    id: UUID
    status: str
    payment_status: str
    fulfillment_status: str
    version: int
    total_fen: int
    shipping_fen: int
    refunded_fen: int
    refund_reserved_fen: int
    address_snapshot: dict[str, str]
    created_at: datetime
    expires_at: datetime
    items: list[OrderLineOut]
    policy: dict[str, str | int]
    delivered_at: datetime | None
    return_deadline: datetime | None


class OrderPage(Schema):
    items: list[OrderOut]
    next_cursor: str | None = None


class OrderFilter(Schema):
    status: Literal["pending_payment", "closing", "paid", "cancelled"] | None = None
    payment_status: (
        Literal["preparing", "pending", "unknown", "paid", "closed", "refunded"] | None
    ) = None
    fulfillment_status: Literal["unfulfilled", "fulfilling", "shipped", "delivered"] | None = None
    created_from: datetime | None = None
    created_to: datetime | None = None

    @model_validator(mode="after")
    def valid_range(self) -> "OrderFilter":
        for value in (self.created_from, self.created_to):
            if value is not None and value.tzinfo is None:
                raise ValueError("时间筛选必须包含时区")
        if self.created_from and self.created_to and self.created_from > self.created_to:
            raise ValueError("开始时间不能晚于结束时间")
        return self


class VersionIn(Schema):
    expected_version: int = Field(ge=1)


class CancelIn(VersionIn):
    reason: str = Field(min_length=1, max_length=300)


class ShipmentIn(VersionIn):
    carrier: str = Field(min_length=1, max_length=80)
    tracking_number: str = Field(min_length=1, max_length=100)


class ReturnLine(Schema):
    order_item_id: UUID
    quantity: Quantity


class ReturnIn(VersionIn):
    items: list[ReturnLine] = Field(min_length=1, max_length=50)
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique_lines(self) -> "ReturnIn":
        if len({i.order_item_id for i in self.items}) != len(self.items):
            raise ValueError("同一订单行只能出现一次")
        return self


class InspectionIn(Schema):
    resellable: bool
    note: str = Field(min_length=1, max_length=500)


class ReturnItemOut(Schema):
    order_item_id: UUID
    title: str
    attributes: dict[str, str]
    quantity: int
    amount_fen: int


class ReturnRefundOut(Schema):
    id: UUID
    amount_fen: int
    status: str
    confirmed_at: datetime | None


class ReturnOut(Schema):
    id: UUID
    order_id: UUID
    status: str
    reason: str
    inspection_note: str | None
    refund_id: UUID | None
    carrier: str | None
    tracking_number: str | None
    created_at: datetime
    items: list[ReturnItemOut]
    refund: ReturnRefundOut | None
    return_address: ReturnAddress | None


class EventOut(Schema):
    id: UUID
    sequence: int
    order_id: UUID
    kind: str
    order_version: int
    created_at: datetime


class PaymentOut(Schema):
    intent_id: UUID
    status: str
    # 本站入口使用 Bearer 认证；不会把客户 token 放入 URL。
    payment_path: str


class PaymentCallback(Schema):
    event_id: UUID
    intent_id: UUID
    merchant_id: str
    amount_fen: int = Field(gt=0)
    currency: Literal["CNY"]
    state: Literal["pending", "paid", "closed"]


class OperationOut(Schema):
    id: UUID
    kind: str
    resource_id: UUID
    status: str
    attempts: int
    last_error: str | None
    next_attempt_at: datetime
