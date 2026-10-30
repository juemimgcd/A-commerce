"""按同一主库快照计算收退款流量与支付订单群表现，金额仅用整数。"""

from datetime import date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select, text

from a_commerce.conf.resources import Resources
from a_commerce.errors import CommerceError, missing
from a_commerce.models.base import now
from a_commerce.models.entities import (
    Order,
    OrderItem,
    Product,
    Refund,
    RefundItem,
    ReturnItem,
    ReturnRequest,
)
from a_commerce.schemas.api import Schema
from a_commerce.services.identity import Principal
from a_commerce.services.shop_access import scope


class CountOut(Schema):
    name: str
    count: int


class CashOut(Schema):
    receipts_fen: int
    goods_receipts_fen: int
    shipping_receipts_fen: int
    refunds_fen: int
    goods_refunds_fen: int
    shipping_refunds_fen: int
    unallocated_refunds_fen: int
    net_receipts_fen: int


class PerformanceOut(Schema):
    product_id: UUID | None
    title: str
    paid_quantity: int
    returned_quantity: int
    goods_receipts_fen: int
    net_goods_fen: int | None
    known_net_goods_fen: int
    known_cost_fen: int
    gross_profit_fen: int | None
    cost_coverage: float | None
    profit_coverage: float | None
    line_count: int
    profit_line_count: int


class CustomersOut(Schema):
    buyers: int
    new_buyers: int
    returning_buyers: int
    paid_orders: int
    regions: list[CountOut]
    categories: list[CountOut]
    frequency: list[CountOut]
    spending: list[CountOut]
    spending_excluded_buyers: int


class MerchantReport(Schema):
    shop_id: UUID
    start: datetime
    end: datetime
    as_of: datetime
    timezone: str = "Asia/Shanghai"
    currency: str = "CNY"
    basis_version: str = "merchant-v1"
    cash: CashOut
    performance: PerformanceOut
    products: list[PerformanceOut]
    customers: CustomersOut
    refund_pending_fen: int
    pending_returns: int
    limitations: list[str]


async def report(
    r: Resources,
    p: Principal,
    shop_id: UUID,
    start: date,
    end: date,
    product_id: UUID | None = None,
) -> MerchantReport:
    if end <= start or end - start > timedelta(days=366):
        raise CommerceError(
            "REPORT_RANGE", "请选择不超过 366 天的有效区间，结束日期不包含在内。", 422
        )
    begin = datetime.combine(start, time.min, ZoneInfo("Asia/Shanghai"))
    finish = datetime.combine(end, time.min, ZoneInfo("Asia/Shanghai"))
    async with r.sessions.begin() as session:
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        await scope(session, p, shop_id, "audit")
        as_of = now()
        if product_id and not await session.scalar(
            select(Product.id).where(Product.id == product_id, Product.shop_id == shop_id)
        ):
            raise missing()
        cohort = [Order.shop_id == shop_id, Order.paid_at >= begin, Order.paid_at < finish]
        paid = (
            await session.execute(
                select(
                    func.coalesce(func.sum(Order.total_fen), 0),
                    func.coalesce(func.sum(Order.shipping_fen), 0),
                ).where(*cohort)
            )
        ).one()
        refunds = (
            await session.execute(
                select(
                    func.coalesce(func.sum(Refund.amount_fen), 0),
                    func.coalesce(func.sum(Refund.shipping_fen), 0),
                    func.coalesce(
                        func.sum(case((Refund.shipping_fen.is_(None), Refund.amount_fen), else_=0)),
                        0,
                    ),
                )
                .join(Order)
                .where(
                    Order.shop_id == shop_id,
                    Refund.status == "succeeded",
                    Refund.confirmed_at >= begin,
                    Refund.confirmed_at < finish,
                )
            )
        ).one()
        allocation = (
            select(RefundItem.order_item_id, func.sum(RefundItem.amount_fen).label("amount"))
            .join(Refund)
            .where(Refund.status == "succeeded")
            .group_by(RefundItem.order_item_id)
            .subquery()
        )
        # 旧退款缺失分配时，整单明细均不能声称可计算毛利。
        incomplete = select(Refund.order_id).where(Refund.shipping_fen.is_(None)).distinct()
        returned = (
            select(
                ReturnItem.order_item_id,
                func.sum(ReturnItem.quantity).label("quantity"),
                func.sum(
                    case((ReturnRequest.resellable.is_(True), ReturnItem.quantity), else_=0)
                ).label("resellable"),
            )
            .join(ReturnRequest)
            .where(ReturnRequest.status == "inspected")
            .group_by(ReturnItem.order_item_id)
            .subquery()
        )
        amount = OrderItem.price_fen * OrderItem.quantity
        known = OrderItem.cost_fen.is_not(None)
        complete = known & Order.id.not_in(incomplete)
        reverse = case(
            (
                (Order.status == "cancelled")
                & (Order.fulfillment_status == "unfulfilled")
                & (Order.refunded_fen == Order.total_fen),
                OrderItem.quantity,
            ),
            else_=func.coalesce(returned.c.resellable, 0),
        )
        net = amount - func.coalesce(allocation.c.amount, 0)
        cost = OrderItem.cost_fen * (OrderItem.quantity - reverse)
        statement = (
            select(
                OrderItem.product_id,
                func.min(OrderItem.title),
                func.sum(OrderItem.quantity),
                func.sum(func.coalesce(returned.c.quantity, 0)),
                func.sum(amount),
                func.sum(net),
                func.sum(case((complete, net), else_=0)),
                func.sum(case((complete, cost), else_=0)),
                func.sum(case((known, amount), else_=0)),
                func.count(OrderItem.id),
                func.sum(case((complete, 1), else_=0)),
                func.sum(case((Order.id.in_(incomplete), 1), else_=0)),
            )
            .join(Order, Order.id == OrderItem.order_id)
            .outerjoin(allocation, allocation.c.order_item_id == OrderItem.id)
            .outerjoin(returned, returned.c.order_item_id == OrderItem.id)
            .where(*cohort)
        )
        if product_id:
            statement = statement.where(OrderItem.product_id == product_id)
        products: list[PerformanceOut] = []
        known_amount_total = 0
        for (
            ident,
            title,
            quantity,
            returned_q,
            gross,
            net_g,
            known_net,
            cost_g,
            known_amount,
            lines,
            complete_lines,
            unknown_refund_lines,
        ) in await session.execute(
            statement.group_by(OrderItem.product_id).order_by(OrderItem.product_id)
        ):
            known_amount_total += int(known_amount)
            products.append(
                PerformanceOut(
                    product_id=ident,
                    title=title,
                    paid_quantity=quantity,
                    returned_quantity=returned_q,
                    goods_receipts_fen=gross,
                    net_goods_fen=None if unknown_refund_lines else net_g,
                    known_net_goods_fen=known_net,
                    known_cost_fen=cost_g,
                    gross_profit_fen=known_net - cost_g if complete_lines else None,
                    cost_coverage=known_amount / gross if gross else None,
                    profit_coverage=complete_lines / lines if lines else None,
                    line_count=lines,
                    profit_line_count=complete_lines,
                )
            )
        totals = {
            field: sum(getattr(row, field) or 0 for row in products)
            for field in (
                "paid_quantity",
                "returned_quantity",
                "goods_receipts_fen",
                "net_goods_fen",
                "known_net_goods_fen",
                "known_cost_fen",
                "line_count",
                "profit_line_count",
            )
        }
        if any(row.net_goods_fen is None for row in products):
            totals.pop("net_goods_fen")
        performance = PerformanceOut(
            product_id=product_id,
            title="所选成交订单",
            **totals,
            **({"net_goods_fen": None} if "net_goods_fen" not in totals else {}),
            gross_profit_fen=totals["known_net_goods_fen"] - totals["known_cost_fen"]
            if totals["profit_line_count"]
            else None,
            cost_coverage=known_amount_total / totals["goods_receipts_fen"]
            if totals["goods_receipts_fen"]
            else None,
            profit_coverage=totals["profit_line_count"] / totals["line_count"]
            if totals["line_count"]
            else None,
        )
        first = (
            select(Order.user_id, func.min(Order.paid_at).label("first_paid"))
            .where(Order.shop_id == shop_id, Order.paid_at.is_not(None))
            .group_by(Order.user_id)
            .subquery()
        )
        buyers = (
            await session.execute(
                select(
                    func.count(func.distinct(Order.user_id)),
                    func.count(func.distinct(case((first.c.first_paid >= begin, Order.user_id)))),
                    func.count(Order.id),
                )
                .join(first, first.c.user_id == Order.user_id)
                .where(*cohort)
            )
        ).one()
        region = Order.address_snapshot["region"].astext
        regions = [
            CountOut(name=name or "未知", count=count)
            for name, count in await session.execute(
                select(region, func.count(Order.id)).where(*cohort).group_by(region)
            )
        ]
        categories = [
            CountOut(name=name or "未知", count=count)
            for name, count in await session.execute(
                select(OrderItem.category_name, func.sum(OrderItem.quantity))
                .join(Order)
                .where(*cohort)
                .group_by(OrderItem.category_name)
            )
        ]
        counts = (
            select(Order.user_id, func.count().label("orders"))
            .where(*cohort)
            .group_by(Order.user_id)
            .subquery()
        )
        frequency = [
            CountOut(name=str(count), count=users)
            for count, users in await session.execute(
                select(counts.c.orders, func.count())
                .group_by(counts.c.orders)
                .order_by(counts.c.orders)
            )
        ]
        refunded = (
            select(
                Refund.order_id,
                func.sum(Refund.amount_fen - func.coalesce(Refund.shipping_fen, 0)).label("amount"),
            )
            .where(Refund.status == "succeeded")
            .group_by(Refund.order_id)
            .subquery()
        )
        spending_rows = (
            select(
                Order.user_id,
                func.sum(
                    Order.total_fen - Order.shipping_fen - func.coalesce(refunded.c.amount, 0)
                ).label("net"),
            )
            .outerjoin(refunded, refunded.c.order_id == Order.id)
            .where(
                *cohort,
                Order.user_id.not_in(
                    select(Order.user_id).where(*cohort, Order.id.in_(incomplete))
                ),
            )
            .group_by(Order.user_id)
            .subquery()
        )
        bucket = case(
            (spending_rows.c.net <= 0, "0 元"),
            (spending_rows.c.net < 10000, "不足 100 元"),
            (spending_rows.c.net < 100000, "100 至 1000 元"),
            else_="1000 元及以上",
        )
        spending = [
            CountOut(name=name, count=count)
            for name, count in await session.execute(select(bucket, func.count()).group_by(bucket))
        ]
        spending_excluded = int(
            await session.scalar(
                select(func.count(func.distinct(Order.user_id))).where(
                    *cohort, Order.id.in_(incomplete)
                )
            )
            or 0
        )
        pending = int(
            await session.scalar(
                select(func.coalesce(func.sum(Order.refund_reserved_fen), 0)).where(*cohort)
            )
            or 0
        )
        pending_returns = int(
            await session.scalar(
                select(func.count(ReturnRequest.id))
                .join(Order)
                .where(*cohort, ReturnRequest.status.not_in(["rejected", "inspected"]))
            )
            or 0
        )
        limitations = [
            "商品毛利仅包含成本及售后归属完整的明细，不含运费、税费、平台费用。",
            "现金报表按发生时间，经营表现按支付订单群；退款与入库可能尚未完成。",
        ]
        if int(refunds[2]):
            limitations.append("历史退款存在金额分配未知项，已单独列示。")
        return MerchantReport(
            shop_id=shop_id,
            start=begin,
            end=finish,
            as_of=as_of,
            cash=CashOut(
                receipts_fen=paid[0],
                goods_receipts_fen=paid[0] - paid[1],
                shipping_receipts_fen=paid[1],
                refunds_fen=refunds[0],
                goods_refunds_fen=refunds[0] - refunds[1] - refunds[2],
                shipping_refunds_fen=refunds[1],
                unallocated_refunds_fen=refunds[2],
                net_receipts_fen=paid[0] - refunds[0],
            ),
            performance=performance,
            products=products,
            customers=CustomersOut(
                buyers=buyers[0],
                new_buyers=buyers[1],
                returning_buyers=buyers[0] - buyers[1],
                paid_orders=buyers[2],
                regions=regions,
                categories=categories,
                frequency=frequency,
                spending=spending,
                spending_excluded_buyers=spending_excluded,
            ),
            refund_pending_fen=pending,
            pending_returns=pending_returns,
            limitations=limitations,
        )
