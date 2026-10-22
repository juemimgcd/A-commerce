"""订单库存迁移；调用方持有订单锁，因此同一业务迁移不会被重复应用。"""

from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a_commerce.models.entities import Order, OrderItem, Stock, StockMovement


async def move_order_stock(
    session: AsyncSession,
    order: Order,
    action: Literal["allocate", "release_reserved", "release_allocated", "ship"],
) -> None:
    items = (
        await session.scalars(
            select(OrderItem).where(OrderItem.order_id == order.id).order_by(OrderItem.sku_id)
        )
    ).all()
    stocks = {
        s.sku_id: s
        for s in await session.scalars(
            select(Stock)
            .where(Stock.sku_id.in_([i.sku_id for i in items]))
            .order_by(Stock.sku_id)
            .with_for_update()
        )
    }
    for item in items:
        q = item.quantity
        hand, reserved, allocated = {
            "allocate": (0, -q, q),
            "release_reserved": (0, -q, 0),
            "release_allocated": (0, 0, -q),
            "ship": (-q, 0, -q),
        }[action]
        stock = stocks[item.sku_id]
        stock.on_hand += hand
        stock.reserved += reserved
        stock.allocated += allocated
        session.add(
            StockMovement(
                reference=f"{action}:{order.id}:{item.sku_id}",
                sku_id=item.sku_id,
                on_hand_delta=hand,
                reserved_delta=reserved,
                allocated_delta=allocated,
                reason=action,
            )
        )
