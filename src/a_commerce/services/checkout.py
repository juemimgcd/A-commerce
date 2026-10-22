"""报价与下单：在一个事务中完成最终价格检查、库存预留和 outbox 写入。"""

import hashlib
from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a_commerce.conf.resources import Resources
from a_commerce.crud.queries import order_output
from a_commerce.errors import CommerceError, missing
from a_commerce.models.base import now
from a_commerce.models.entities import (
    SKU,
    Address,
    Audit,
    IdempotencyRecord,
    Operation,
    Order,
    OrderItem,
    PaymentIntent,
    Product,
    Quote,
    Stock,
    StockMovement,
)
from a_commerce.schemas.api import OrderIn, OrderOut, QuoteIn, QuoteOut
from a_commerce.services.events import record_event
from a_commerce.services.identity import Principal, authorize
from a_commerce.services.operations import enqueue
from a_commerce.services.pricing import shipping_price


async def lock_catalog(session: AsyncSession, ids: list[UUID]) -> dict[UUID, tuple[SKU, Product]]:
    # 商品 → SKU → 库存，且每层都按 UUID 排序。商品编辑和下单使用相同协议，
    # 因而下单检查完价格之后，管理员不能在订单提交前穿插修改该价格。
    product_ids = select(SKU.product_id).where(SKU.id.in_(ids))
    products = list(
        (
            await session.scalars(
                select(Product)
                .where(Product.id.in_(product_ids))
                .order_by(Product.id)
                .with_for_update()
            )
        ).all()
    )
    skus = list(
        (
            await session.scalars(
                select(SKU).where(SKU.id.in_(ids)).order_by(SKU.id).with_for_update()
            )
        ).all()
    )
    by_product = {p.id: p for p in products}
    result = {sku.id: (sku, by_product[sku.product_id]) for sku in skus}
    if len(result) != len(ids) or any(not s.active or not p.active for s, p in result.values()):
        raise CommerceError("PRODUCT_UNAVAILABLE", "商品不存在或已下架。")
    return result


async def create_quote(r: Resources, p: Principal, data: QuoteIn) -> QuoteOut:
    async with r.sessions.begin() as session:
        await authorize(session, p, lock=True)
        address = await session.scalar(
            select(Address)
            .where(Address.id == data.address_id, Address.user_id == p.id)
            .with_for_update()
        )
        if address is None:
            raise missing()
        catalog = await lock_catalog(session, [item.sku_id for item in data.items])
        items: list[dict[str, str | int]] = []
        for line in sorted(data.items, key=lambda item: item.sku_id):
            sku, product = catalog[line.sku_id]
            items.append(
                {
                    "sku_id": str(sku.id),
                    "sku_version": sku.version,
                    "product_version": product.version,
                    "quantity": line.quantity,
                    "price_fen": sku.price_fen,
                    "title": product.title,
                }
            )
        subtotal = sum(int(i["price_fen"]) * int(i["quantity"]) for i in items)
        shipping, rule_version = await shipping_price(
            session, address.region, subtotal, r.settings.shipping_fen
        )
        quote = Quote(
            user_id=p.id,
            address_id=address.id,
            address_version=address.version,
            address_snapshot={
                field: getattr(address, field)
                for field in ("recipient", "phone", "region", "detail")
            },
            items=items,
            shipping_fen=shipping,
            total_fen=subtotal + shipping,
            policy={
                "version": "fixed-v1",
                "shipping_rule": rule_version,
                "return_days": r.settings.return_days,
                "return_shipping": "non_refundable",
            },
            expires_at=now() + timedelta(seconds=r.settings.quote_seconds),
        )
        session.add(quote)
        await session.flush()
        return QuoteOut.model_validate(quote)


async def place_order(r: Resources, p: Principal, data: OrderIn, key: str) -> OrderOut:
    fingerprint = hashlib.sha256(data.model_dump_json().encode()).hexdigest()
    async with r.sessions.begin() as session:
        await authorize(session, p, lock=True)
        quote = await session.scalar(
            select(Quote).where(Quote.id == data.quote_id, Quote.user_id == p.id).with_for_update()
        )
        if quote is None:
            raise missing()
        prior = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.actor_id == p.id,
                IdempotencyRecord.action == "order.create",
                IdempotencyRecord.key == key,
            )
        )
        if prior and prior.fingerprint != fingerprint:
            raise CommerceError("IDEMPOTENCY_CONFLICT", "同一幂等键不能用于不同报价。")
        existing = await session.scalar(select(Order).where(Order.quote_id == quote.id))
        if existing:
            if prior is None:
                session.add(
                    IdempotencyRecord(
                        actor_id=p.id,
                        action="order.create",
                        key=key,
                        fingerprint=fingerprint,
                        result_id=existing.id,
                    )
                )
            return await order_output(session, existing)
        if quote.expires_at <= now():
            raise CommerceError("QUOTE_EXPIRED", "报价已过期，请重新报价并确认。")
        # 对未完成关键任务作有界检查；队列积压时停止继续占用新的库存。
        pending = list(
            (
                await session.scalars(
                    select(Operation.id)
                    .where(
                        Operation.status.not_in(["done", "failed"]),
                        Operation.kind.in_(["payment", "close", "refund"]),
                    )
                    .limit(r.settings.backlog_limit)
                )
            ).all()
        )
        if len(pending) >= r.settings.backlog_limit:
            raise CommerceError("CHECKOUT_PAUSED", "支付任务积压，暂时停止新结算。", 503, True)
        address = await session.scalar(
            select(Address).where(Address.id == quote.address_id).with_for_update()
        )
        if address is None or address.version != quote.address_version:
            raise CommerceError("QUOTE_STALE", "收货地址已变化，请重新报价。")
        catalog = await lock_catalog(session, [UUID(str(i["sku_id"])) for i in quote.items])
        shipping, rule_version = await shipping_price(
            session, address.region, quote.total_fen - quote.shipping_fen, r.settings.shipping_fen
        )
        if shipping != quote.shipping_fen or rule_version != quote.policy.get("shipping_rule"):
            raise CommerceError("QUOTE_STALE", "配送规则已变化，请重新报价。")
        stocks = {
            s.sku_id: s
            for s in await session.scalars(
                select(Stock)
                .where(Stock.sku_id.in_(catalog))
                .order_by(Stock.sku_id)
                .with_for_update()
            )
        }
        order = Order(
            user_id=p.id,
            quote_id=quote.id,
            total_fen=quote.total_fen,
            shipping_fen=quote.shipping_fen,
            address_snapshot=quote.address_snapshot,
            policy=quote.policy,
            expires_at=now() + timedelta(seconds=r.settings.payment_seconds),
        )
        session.add(order)
        await session.flush()
        for line in quote.items:
            sku_id = UUID(str(line["sku_id"]))
            sku, product = catalog[sku_id]
            quantity = int(line["quantity"])
            if (
                sku.version != line["sku_version"]
                or product.version != line["product_version"]
                or sku.price_fen != line["price_fen"]
            ):
                raise CommerceError("QUOTE_STALE", "商品或价格已变化，请重新报价并确认。")
            stock = stocks[sku_id]
            if stock.on_hand - stock.reserved - stock.allocated < quantity:
                raise CommerceError("OUT_OF_STOCK", "可售库存不足，整张订单未创建。")
            stock.reserved += quantity
            session.add_all(
                [
                    OrderItem(
                        order_id=order.id,
                        sku_id=sku_id,
                        title=product.title,
                        attributes=sku.attributes,
                        quantity=quantity,
                        price_fen=sku.price_fen,
                    ),
                    StockMovement(
                        reference=f"reserve:{order.id}:{sku_id}",
                        sku_id=sku_id,
                        on_hand_delta=0,
                        reserved_delta=quantity,
                        allocated_delta=0,
                        reason="创建订单预留库存",
                    ),
                ]
            )
        intent = PaymentIntent(order_id=order.id)
        session.add(intent)
        await session.flush()
        await enqueue(session, "payment", intent.id)
        session.add_all(
            [
                IdempotencyRecord(
                    actor_id=p.id,
                    action="order.create",
                    key=key,
                    fingerprint=fingerprint,
                    result_id=order.id,
                ),
                Audit(
                    actor_id=p.id,
                    action="order.create",
                    resource_id=order.id,
                    detail="报价确认下单",
                ),
            ]
        )
        await record_event(session, order, "order.created")
        return await order_output(session, order)
