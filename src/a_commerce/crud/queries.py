"""短小的数据访问函数。只查询/加锁，不提交事务、不调用外部服务。"""

from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a_commerce.conf.shop import ReturnAddress
from a_commerce.errors import missing
from a_commerce.models.entities import (
    SKU,
    Media,
    Order,
    OrderItem,
    Product,
    Refund,
    ReturnItem,
    ReturnRequest,
    Stock,
)
from a_commerce.schemas.api import (
    OrderLineOut,
    OrderOut,
    ProductOut,
    ReturnItemOut,
    ReturnOut,
    ReturnRefundOut,
    SKUOut,
)


async def order_locked(session: AsyncSession, order_id: UUID) -> Order:
    # 订单主键、报价与买家引用创建后不变；业务只修改状态、金额和时间。
    # NO KEY UPDATE 在订单写操作之间仍然互斥，但允许归档外键的 KEY SHARE 检查。
    # 否则归档持有事件序号锁、订单更新持有订单锁时，会形成相反顺序的循环等待。
    order = await session.scalar(
        select(Order).where(Order.id == order_id).with_for_update(key_share=True)
    )
    if order is None:
        raise missing()
    return order


async def order_output(session: AsyncSession, order: Order) -> OrderOut:
    return (await order_outputs(session, [order]))[0]


async def order_outputs(session: AsyncSession, orders: list[Order]) -> list[OrderOut]:
    """一次批量读取所有订单行，避免每页 N 个订单产生 N 次明细查询。"""
    items = (
        await session.scalars(
            select(OrderItem)
            .where(OrderItem.order_id.in_([o.id for o in orders]))
            .order_by(OrderItem.sku_id)
        )
    ).all()
    grouped: dict[UUID, list[OrderLineOut]] = {}
    for item in items:
        grouped.setdefault(item.order_id, []).append(OrderLineOut.model_validate(item))
    return [
        OrderOut.model_validate(
            {
                **{
                    name: getattr(order, name)
                    for name in OrderOut.model_fields
                    if name not in {"items", "return_deadline"}
                },
                "items": grouped.get(order.id, []),
                "return_deadline": (
                    order.delivered_at + timedelta(days=int(order.policy["return_days"]))
                    if order.delivered_at
                    else None
                ),
            }
        )
        for order in orders
    ]


async def product_outputs(session: AsyncSession, products: list[Product]) -> list[ProductOut]:
    covers = await product_covers(session, [p.id for p in products])
    rows = (
        await session.execute(
            select(SKU, Stock)
            .join(Stock, Stock.sku_id == SKU.id)
            .where(SKU.product_id.in_([p.id for p in products]))
            .order_by(SKU.id)
        )
    ).all()
    by_product: dict[UUID, list[SKUOut]] = {}
    for sku, stock in rows:
        by_product.setdefault(sku.product_id, []).append(
            SKUOut(
                id=sku.id,
                product_id=sku.product_id,
                code=sku.code,
                price_fen=sku.price_fen,
                attributes=sku.attributes,
                active=sku.active,
                version=sku.version,
                available=stock.on_hand - stock.reserved - stock.allocated,
            )
        )
    return [
        ProductOut(
            id=p.id,
            category_id=p.category_id,
            title=p.title,
            brand=p.brand,
            description=p.description,
            active=p.active,
            version=p.version,
            skus=by_product.get(p.id, []),
            cover_media_id=covers.get(p.id),
        )
        for p in products
    ]


async def product_covers(session: AsyncSession, ids: list[UUID]) -> dict[UUID, UUID]:
    """每页一次读取封面，不让列表对每件商品额外发起媒体请求。"""
    if not ids:
        return {}
    rows = await session.execute(
        select(Media.product_id, Media.id)
        .where(Media.product_id.in_(ids))
        .distinct(Media.product_id)
        .order_by(Media.product_id, Media.created_at, Media.id)
    )
    return {product_id: media_id for product_id, media_id in rows}


async def return_outputs(
    session: AsyncSession,
    records: list[ReturnRequest],
    address: ReturnAddress | None,
) -> list[ReturnOut]:
    if not records:
        return []
    rows = await session.execute(
        select(ReturnItem, OrderItem)
        .join(OrderItem, OrderItem.id == ReturnItem.order_item_id)
        .where(ReturnItem.return_id.in_([row.id for row in records]))
        .order_by(ReturnItem.order_item_id)
    )
    grouped: dict[UUID, list[ReturnItemOut]] = {}
    for item, original in rows:
        grouped.setdefault(item.return_id, []).append(
            ReturnItemOut(
                order_item_id=original.id,
                title=original.title,
                attributes=original.attributes,
                quantity=item.quantity,
                amount_fen=original.price_fen * item.quantity,
            )
        )
    refunds = {
        row.id: ReturnRefundOut.model_validate(row)
        for row in await session.scalars(
            select(Refund).where(Refund.id.in_([r.refund_id for r in records if r.refund_id]))
        )
    }
    return [
        ReturnOut.model_validate(
            {
                **{
                    name: getattr(row, name)
                    for name in ReturnOut.model_fields
                    if name not in {"items", "refund", "return_address"}
                },
                "items": grouped.get(row.id, []),
                "refund": refunds.get(row.refund_id) if row.refund_id else None,
                "return_address": address,
            }
        )
        for row in records
    ]
