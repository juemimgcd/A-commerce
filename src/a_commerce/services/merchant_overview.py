"""卖家首页：按上海经营日聚合，在同一快照中对比等长期间。"""

from datetime import date, datetime, timedelta
from uuid import UUID

from sqlalchemy import Date, case, cast, func, select, text

from a_commerce.conf.resources import Resources
from a_commerce.errors import CommerceError
from a_commerce.models.base import now
from a_commerce.models.entities import (
    SKU,
    Order,
    OrderItem,
    Product,
    Refund,
    RefundItem,
    ReturnRequest,
    Stock,
)
from a_commerce.models.merchant_ops import RestockRule, RestockTask
from a_commerce.schemas.api import Schema
from a_commerce.services.identity import Principal
from a_commerce.services.merchant_analysis import bounds
from a_commerce.services.shop_access import scope


class SalesTotals(Schema):
    net_goods_fen: int | None = 0
    paid_orders: int = 0
    new_buyers: int = 0
    refunds_fen: int = 0


class SalesDay(SalesTotals):
    day: date


class MerchantTodos(Schema):
    unshipped: int
    returns: int
    low_stock: int
    unconfigured_stock: int
    restock_drafts: int


class MerchantOverview(Schema):
    as_of: datetime
    start: date
    end: date
    previous_start: date
    timezone: str = "Asia/Shanghai"
    current: SalesTotals
    previous: SalesTotals
    days: list[SalesDay]
    todos: MerchantTodos


async def overview(
    r: Resources, p: Principal, shop_id: UUID, start: date, end: date
) -> MerchantOverview:
    _, finish = bounds(start, end)
    if start - date.min < end - start:
        raise CommerceError("REPORT_RANGE", "开始日期过早，无法计算上一等长周期。", 422)
    previous_start = start - (end - start)
    previous_begin, _ = bounds(previous_start, start)
    days = {
        previous_start + timedelta(days=i): SalesDay(day=previous_start + timedelta(days=i))
        for i in range((end - previous_start).days)
    }
    async with r.sessions.begin() as session:
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        await scope(session, p, shop_id, "audit")
        as_of = now()
        cohort = [Order.shop_id == shop_id, Order.paid_at >= previous_begin, Order.paid_at < finish]
        paid_day = cast(func.timezone("Asia/Shanghai", Order.paid_at), Date)
        for day, count in await session.execute(
            select(paid_day, func.count(Order.id)).where(*cohort).group_by(paid_day)
        ):
            days[day].paid_orders = count
        first = (
            select(func.min(Order.paid_at).label("paid_at"))
            .where(Order.shop_id == shop_id, Order.paid_at.is_not(None))
            .group_by(Order.user_id)
            .subquery()
        )
        first_day = cast(func.timezone("Asia/Shanghai", first.c.paid_at), Date)
        for day, count in await session.execute(
            select(first_day, func.count())
            .where(first.c.paid_at >= previous_begin, first.c.paid_at < finish)
            .group_by(first_day)
        ):
            days[day].new_buyers = count
        allocation = (
            select(RefundItem.order_item_id, func.sum(RefundItem.amount_fen).label("amount"))
            .join(Refund)
            .join(Order, Order.id == Refund.order_id)
            .where(*cohort, Refund.status == "succeeded")
            .group_by(RefundItem.order_item_id)
            .subquery()
        )
        incomplete = select(Refund.order_id).where(Refund.shipping_fen.is_(None))
        for day, net, unknown in await session.execute(
            select(
                paid_day,
                func.sum(
                    OrderItem.price_fen * OrderItem.quantity - func.coalesce(allocation.c.amount, 0)
                ),
                func.sum(case((Order.id.in_(incomplete), 1), else_=0)),
            )
            .select_from(OrderItem)
            .join(Order, Order.id == OrderItem.order_id)
            .outerjoin(allocation, allocation.c.order_item_id == OrderItem.id)
            .where(*cohort)
            .group_by(paid_day)
        ):
            days[day].net_goods_fen = None if unknown else int(net)
        refund_day = cast(func.timezone("Asia/Shanghai", Refund.confirmed_at), Date)
        for day, amount in await session.execute(
            select(refund_day, func.sum(Refund.amount_fen))
            .join(Order)
            .where(
                Order.shop_id == shop_id,
                Refund.status == "succeeded",
                Refund.confirmed_at >= previous_begin,
                Refund.confirmed_at < finish,
            )
            .group_by(refund_day)
        ):
            days[day].refunds_fen = int(amount)
        unshipped = await session.scalar(
            select(func.count(Order.id)).where(
                Order.shop_id == shop_id,
                Order.status == "paid",
                Order.fulfillment_status.in_(["unfulfilled", "fulfilling"]),
            )
        )
        returns = await session.scalar(
            select(func.count(ReturnRequest.id))
            .join(Order)
            .where(
                Order.shop_id == shop_id,
                ReturnRequest.status.in_(["requested", "shipped", "received"]),
            )
        )
        available = Stock.on_hand - Stock.reserved - Stock.allocated
        low_stock, unconfigured = (
            await session.execute(
                select(
                    func.count(
                        case((available <= func.coalesce(RestockRule.low_stock, 0), SKU.id))
                    ),
                    func.count(case((RestockRule.id.is_(None), SKU.id))),
                )
                .select_from(SKU)
                .join(Product)
                .join(Stock, Stock.sku_id == SKU.id)
                .outerjoin(RestockRule, RestockRule.sku_id == SKU.id)
                .where(Product.shop_id == shop_id, Product.active.is_(True), SKU.active.is_(True))
            )
        ).one()
        drafts = await session.scalar(
            select(func.count(RestockTask.id)).where(
                RestockTask.shop_id == shop_id, RestockTask.status == "draft"
            )
        )
        current, previous = SalesTotals(), SalesTotals()
        for day, row in days.items():
            total = current if day >= start else previous
            total.paid_orders += row.paid_orders
            total.new_buyers += row.new_buyers
            total.refunds_fen += row.refunds_fen
            total.net_goods_fen = (
                total.net_goods_fen + row.net_goods_fen
                if total.net_goods_fen is not None and row.net_goods_fen is not None
                else None
            )
        return MerchantOverview(
            as_of=as_of,
            start=start,
            end=end,
            previous_start=previous_start,
            current=current,
            previous=previous,
            days=[row for day, row in days.items() if day >= start],
            todos=MerchantTodos(
                unshipped=unshipped or 0,
                returns=returns or 0,
                low_stock=low_stock,
                unconfigured_stock=unconfigured,
                restock_drafts=drafts or 0,
            ),
        )
