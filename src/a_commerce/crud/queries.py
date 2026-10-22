"""短小的数据访问函数。只查询/加锁，不提交事务、不调用外部服务。"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a_commerce.errors import missing
from a_commerce.models.entities import SKU, Order, OrderItem, Product, Stock
from a_commerce.schemas.api import OrderLineOut, OrderOut, ProductOut, SKUOut


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
                **{name: getattr(order, name) for name in OrderOut.model_fields if name != "items"},
                "items": grouped.get(order.id, []),
            }
        )
        for order in orders
    ]


async def product_outputs(session: AsyncSession, products: list[Product]) -> list[ProductOut]:
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
        )
        for p in products
    ]
