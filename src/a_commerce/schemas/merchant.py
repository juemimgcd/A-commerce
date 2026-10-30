"""经营接口使用独立输出，不把成本混入公开商品模型。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import EmailStr, Field

from a_commerce.conf.shop import ReturnAddress
from a_commerce.schemas.api import ProductIn, ProductOut, Schema, SKUIn, VersionIn


class ShopIn(Schema):
    owner_email: EmailStr
    name: str = Field(min_length=1, max_length=100)
    return_address: ReturnAddress


class ShopOut(Schema):
    id: UUID
    owner_user_id: UUID
    name: str
    status: Literal["active", "suspended", "closed"]
    return_address: ReturnAddress | None
    version: int
    created_at: datetime
    updated_at: datetime


class ShopEdit(VersionIn):
    name: str = Field(min_length=1, max_length=100)
    return_address: ReturnAddress


class ShopStatus(VersionIn):
    status: Literal["active", "suspended", "closed"]
    reason: str = Field(min_length=1, max_length=500)


class MerchantProductIn(ProductIn):
    expected_version: int | None = Field(default=None, ge=1)


class MerchantSKUIn(SKUIn):
    expected_version: int | None = Field(default=None, ge=1)


class CostIn(VersionIn):
    cost_fen: int | None = Field(default=None, ge=0, le=100000000, strict=True)


class CostOut(Schema):
    sku_id: UUID
    cost_fen: int | None
    cost_version: int
    on_hand: int
    reserved: int
    allocated: int


class MerchantProductOut(Schema):
    product: ProductOut
    costs: list[CostOut]


class MerchantProductPage(Schema):
    items: list[MerchantProductOut]
    next_cursor: UUID | None
