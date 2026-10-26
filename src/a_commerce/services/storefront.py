"""前端展示查询：批量读取事实，沿用业务身份与权限，不另建交易状态。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import select

from a_commerce.conf.resources import Resources
from a_commerce.crud.queries import product_outputs
from a_commerce.errors import missing
from a_commerce.models.entities import (
    SKU,
    Media,
    Order,
    Product,
    Quote,
    Refund,
    Session,
    ShippingRule,
    Staff,
    Stock,
)
from a_commerce.schemas.api import ProductOut, QuoteOut, Schema, StockOut
from a_commerce.services.identity import Principal, authorize
from a_commerce.services.management import RefundOut
from a_commerce.services.media import MediaOut
from a_commerce.services.pricing import ShippingRuleOut


class ProfileOut(Schema):
    id: UUID
    email: str
    active: bool
    permissions: list[str]
    session_id: UUID
    authenticated_at: datetime


class StoreOut(Schema):
    name: str
    environment: str
    payment_mode: Literal["simulator"]
    mail_enabled: bool
    return_days: int
    return_address_configured: bool
    support_email: str | None
    support_phone: str | None
    privacy_text: str | None
    terms_text: str | None


class InventoryOut(StockOut):
    product_id: UUID
    title: str
    code: str
    attributes: dict[str, str]
    available: int


class InventoryPage(Schema):
    items: list[InventoryOut]
    next_cursor: UUID | None


class ShippingRulePage(Schema):
    items: list[ShippingRuleOut]
    next_cursor: UUID | None


def store(r: Resources) -> StoreOut:
    shop = r.settings.shop
    return StoreOut(
        name=shop.name,
        environment=r.settings.environment,
        payment_mode="simulator",
        mail_enabled=r.settings.mail_enabled,
        return_days=r.settings.return_days,
        return_address_configured=shop.return_address is not None,
        support_email=str(shop.support_email) if shop.support_email else None,
        support_phone=shop.support_phone,
        privacy_text=shop.privacy_text,
        terms_text=shop.terms_text,
    )


async def profile(r: Resources, p: Principal) -> ProfileOut:
    async with r.sessions() as session:
        account = await authorize(session, p)
        token = await session.get(Session, p.session_id)
        if token is None:
            raise missing()
        return ProfileOut(
            id=account.id,
            email=account.email,
            active=account.active,
            permissions=account.permissions if isinstance(account, Staff) else [],
            session_id=p.session_id,
            authenticated_at=token.created_at,
        )


async def product(r: Resources, p: Principal, product_id: UUID) -> ProductOut:
    async with r.sessions() as session:
        await authorize(session, p, "catalog")
        record = await session.get(Product, product_id)
        if record is None:
            raise missing()
        return (await product_outputs(session, [record]))[0]


async def inventory(
    r: Resources,
    p: Principal,
    cursor: UUID | None,
    limit: int,
    q: str,
    low: bool,
    sku_id: UUID | None = None,
) -> InventoryPage:
    async with r.sessions() as session:
        await authorize(session, p, "catalog")
        query = (
            select(Stock, SKU, Product)
            .join(SKU, SKU.id == Stock.sku_id)
            .join(Product, Product.id == SKU.product_id)
        )
        if cursor:
            query = query.where(SKU.id > cursor)
        if sku_id:
            query = query.where(SKU.id == sku_id)
        if q:
            query = query.where(
                Product.title.icontains(q, autoescape=True) | SKU.code.icontains(q, autoescape=True)
            )
        if low:
            query = query.where(Stock.on_hand - Stock.reserved - Stock.allocated <= 5)
        rows = list((await session.execute(query.order_by(SKU.id).limit(limit + 1))).all())
        items = [
            InventoryOut(
                sku_id=sku.id,
                on_hand=stock.on_hand,
                reserved=stock.reserved,
                allocated=stock.allocated,
                available=stock.on_hand - stock.reserved - stock.allocated,
                product_id=product.id,
                title=product.title,
                code=sku.code,
                attributes=sku.attributes,
            )
            for stock, sku, product in rows[:limit]
        ]
        return InventoryPage(
            items=items, next_cursor=items[-1].sku_id if len(rows) > limit else None
        )


async def shipping_rules(
    r: Resources, p: Principal, cursor: UUID | None, limit: int
) -> ShippingRulePage:
    async with r.sessions() as session:
        await authorize(session, p, "catalog")
        query = select(ShippingRule)
        if cursor:
            query = query.where(ShippingRule.id > cursor)
        rows = list(await session.scalars(query.order_by(ShippingRule.id).limit(limit + 1)))
        return ShippingRulePage(
            items=[ShippingRuleOut.model_validate(row) for row in rows[:limit]],
            next_cursor=rows[limit - 1].id if len(rows) > limit else None,
        )


async def product_media(r: Resources, p: Principal, product_id: UUID) -> list[MediaOut]:
    async with r.sessions() as session:
        await authorize(session, p, "catalog")
        if await session.get(Product, product_id) is None:
            raise missing()
        rows = await session.scalars(
            select(Media)
            .where(Media.product_id == product_id)
            .order_by(Media.created_at, Media.id)
            .limit(20)
        )
        return [MediaOut.model_validate(row) for row in rows]


async def media(r: Resources, p: Principal, media_id: UUID) -> Media:
    async with r.sessions() as session:
        await authorize(session, p, "catalog")
        record = await session.get(Media, media_id)
        if record is None:
            raise missing()
        return record


async def quote(r: Resources, p: Principal, quote_id: UUID) -> QuoteOut:
    async with r.sessions() as session:
        await authorize(session, p)
        record = await session.get(Quote, quote_id)
        if record is None or record.user_id != p.id:
            raise missing()
        return QuoteOut.model_validate(record)


async def refunds(r: Resources, p: Principal, order_id: UUID) -> list[RefundOut]:
    async with r.sessions() as session:
        await authorize(session, p)
        order = await session.get(Order, order_id)
        if order is None or order.user_id != p.id:
            raise missing()
        rows = await session.scalars(
            select(Refund)
            .where(Refund.order_id == order_id)
            .order_by(Refund.created_at, Refund.id)
            .limit(100)
        )
        return [RefundOut.model_validate(row) for row in rows]
