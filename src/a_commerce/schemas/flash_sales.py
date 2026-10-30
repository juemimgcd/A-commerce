"""秒杀按 SKU 配置，金额为分，时间必须携带时区。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from a_commerce.schemas.api import Money, Schema


class FlashSaleIn(Schema):
    sku_id: UUID
    price_fen: Money
    stock: int = Field(strict=True, ge=0, le=1000000)
    starts_at: datetime
    ends_at: datetime
    active: bool = False

    @model_validator(mode="after")
    def valid_range(self) -> "FlashSaleIn":
        if self.starts_at.tzinfo is None or self.ends_at.tzinfo is None:
            raise ValueError("活动时间必须包含时区")
        if self.ends_at <= self.starts_at:
            raise ValueError("结束时间必须晚于开始时间")
        return self


class FlashSaleUpdate(FlashSaleIn):
    expected_version: int = Field(strict=True, ge=1)


class FlashSaleOut(FlashSaleIn):
    id: UUID
    version: int
    claimed: int
    available: int
    product_id: UUID
    title: str
    attributes: dict[str, str]
    regular_price_fen: int
    cover_media_id: UUID | None
    status: Literal["disabled", "scheduled", "active", "ended", "sold_out", "unavailable"]


class FlashSalePage(Schema):
    items: list[FlashSaleOut]
    next_cursor: UUID | None = None
