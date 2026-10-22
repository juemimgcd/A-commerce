"""资金入账时增量更新日报，查询报表不扫描历史订单，也不把净收款称为利润。"""

from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from a_commerce.conf.resources import Resources
from a_commerce.errors import CommerceError
from a_commerce.models.base import now
from a_commerce.models.entities import (
    DailyReport,
    FinancialEntry,
    Order,
    OrderItem,
    ProductDailyReport,
)
from a_commerce.schemas.api import Schema
from a_commerce.services.identity import Principal, authorize


class DailyOut(Schema):
    day: date
    sales_fen: int
    refunds_fen: int
    net_receipts_fen: int
    paid_orders: int


class RankingOut(Schema):
    sku_id: UUID
    paid_quantity: int
    sales_fen: int


async def ranking(
    r: Resources, p: Principal, start: date, end: date, limit: int
) -> list[RankingOut]:
    if end < start or end - start > timedelta(days=366):
        raise CommerceError("REPORT_RANGE", "统计范围应在 366 天以内。", 422)
    async with r.sessions() as session:
        await authorize(session, p, "audit")
        quantity = func.sum(ProductDailyReport.quantity)
        sales = func.sum(ProductDailyReport.sales_fen)
        rows = await session.execute(
            select(ProductDailyReport.sku_id, quantity, sales)
            .where(ProductDailyReport.day.between(start, end))
            .group_by(ProductDailyReport.sku_id)
            .order_by(quantity.desc(), ProductDailyReport.sku_id)
            .limit(limit)
        )
        return [
            RankingOut(sku_id=sku_id, paid_quantity=count, sales_fen=amount)
            for sku_id, count, amount in rows
        ]


async def record_money(
    session: AsyncSession, order: Order, kind: str, amount: int, reference: str
) -> None:
    inserted = await session.scalar(
        insert(FinancialEntry)
        .values(reference=reference, order_id=order.id, kind=kind, amount_fen=amount)
        .on_conflict_do_nothing(index_elements=["reference"])
        .returning(FinancialEntry.id)
    )
    if inserted is None:
        return
    day = now().date()
    sales = amount if kind == "payment" else 0
    refunds = amount if kind == "refund" else 0
    count = 1 if kind == "payment" else 0
    await session.execute(
        insert(DailyReport)
        .values(day=day, sales_fen=sales, refunds_fen=refunds, paid_orders=count)
        .on_conflict_do_update(
            index_elements=["day"],
            set_={
                "sales_fen": DailyReport.sales_fen + sales,
                "refunds_fen": DailyReport.refunds_fen + refunds,
                "paid_orders": DailyReport.paid_orders + count,
            },
        )
    )
    if kind == "payment":
        items = await session.scalars(
            select(OrderItem).where(OrderItem.order_id == order.id).order_by(OrderItem.sku_id)
        )
        for item in items:
            value = item.price_fen * item.quantity
            await session.execute(
                insert(ProductDailyReport)
                .values(day=day, sku_id=item.sku_id, quantity=item.quantity, sales_fen=value)
                .on_conflict_do_update(
                    index_elements=["day", "sku_id"],
                    set_={
                        "quantity": ProductDailyReport.quantity + item.quantity,
                        "sales_fen": ProductDailyReport.sales_fen + value,
                    },
                )
            )


async def daily(r: Resources, p: Principal, start: date, end: date) -> list[DailyOut]:
    if end < start or end - start > timedelta(days=366):
        raise CommerceError("REPORT_RANGE", "统计范围应在 366 天以内。", 422)
    async with r.sessions() as session:
        await authorize(session, p, "audit")
        rows = await session.scalars(
            select(DailyReport).where(DailyReport.day.between(start, end)).order_by(DailyReport.day)
        )
        return [
            DailyOut(
                day=row.day,
                sales_fen=row.sales_fen,
                refunds_fen=row.refunds_fen,
                net_receipts_fen=row.sales_fen - row.refunds_fen,
                paid_orders=row.paid_orders,
            )
            for row in rows
        ]
