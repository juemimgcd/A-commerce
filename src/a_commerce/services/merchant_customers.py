"""本店支付行为分群；成员只保存在后端，不返回全局用户标识。"""

from collections import defaultdict
from datetime import timedelta
from statistics import median
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from a_commerce.conf.resources import Resources
from a_commerce.errors import missing
from a_commerce.models.base import now
from a_commerce.models.entities import Order, OrderItem, Refund, RefundItem
from a_commerce.models.merchant_ops import CustomerSegment, SegmentMember
from a_commerce.schemas.merchant_ops import (
    PeriodIn,
    RepeatOut,
    SegmentIn,
    SegmentOut,
    SegmentPreview,
)
from a_commerce.services.identity import Principal
from a_commerce.services.merchant_analysis import ZONE, bounds
from a_commerce.services.merchant_common import remember, replay, snapshot_write, writer
from a_commerce.services.shop_access import audit, scope


async def members(
    session: AsyncSession, shop_id: UUID, data: SegmentIn
) -> tuple[SegmentPreview, list[UUID]]:
    as_of = now()
    orders = list(
        await session.scalars(
            select(Order)
            .where(Order.shop_id == shop_id, Order.paid_at.is_not(None), Order.paid_at <= as_of)
            .order_by(Order.paid_at, Order.id)
        )
    )
    by_user: dict[UUID, list[Order]] = defaultdict(list)
    for order in orders:
        by_user[order.user_id].append(order)
    begin = as_of - timedelta(days=data.window_days)
    recent = {o.id: o for o in orders if o.paid_at is not None and o.paid_at >= begin}
    recent_ids = select(Order.id).where(
        Order.shop_id == shop_id, Order.paid_at >= begin, Order.paid_at <= as_of
    )
    unknown = set(
        await session.scalars(
            select(Refund.order_id).where(
                Refund.order_id.in_(recent_ids), Refund.shipping_fen.is_(None)
            )
        )
    )
    refund_by_order: dict[UUID, int] = defaultdict(int)
    for oid, amount in await session.execute(
        select(Refund.order_id, RefundItem.amount_fen)
        .join(RefundItem)
        .where(Refund.order_id.in_(recent_ids), Refund.status == "succeeded")
    ):
        refund_by_order[oid] += amount
    categories: dict[UUID, set[UUID]] = defaultdict(set)
    if data.category_id:
        for uid, category in await session.execute(
            select(Order.user_id, OrderItem.category_id)
            .join(OrderItem)
            .where(
                Order.shop_id == shop_id,
                Order.paid_at.is_not(None),
                OrderItem.category_id == data.category_id,
            )
        ):
            categories[uid].add(category)
    ids: list[UUID] = []
    recencies: list[int] = []
    total_orders = total_spending = excluded = 0
    for uid, history in by_user.items():
        last = history[-1].paid_at
        if last is None:
            continue
        recency = (as_of.astimezone(ZONE).date() - last.astimezone(ZONE).date()).days
        window = [o for o in history if o.id in recent]
        if (
            recency < data.min_recency_days
            or (data.max_recency_days is not None and recency > data.max_recency_days)
            or len(window) < data.min_orders
        ):
            continue
        if data.category_id and data.category_id not in categories[uid]:
            continue
        if any(o.id in unknown for o in window):
            excluded += 1
            continue
        spending = sum(o.total_fen - o.shipping_fen - refund_by_order[o.id] for o in window)
        if data.min_spending_fen is not None and spending < data.min_spending_fen:
            continue
        if data.max_spending_fen is not None and spending > data.max_spending_fen:
            continue
        ids.append(uid)
        recencies.append(recency)
        total_orders += len(window)
        total_spending += spending
    return SegmentPreview(
        as_of=as_of,
        members=len(ids),
        excluded_unknown_spending=excluded,
        median_recency_days=median(recencies) if recencies else None,
        paid_orders=total_orders,
        known_net_goods_fen=total_spending,
        rules=data,
    ), ids


async def preview(r: Resources, p: Principal, shop_id: UUID, data: SegmentIn) -> SegmentPreview:
    async with r.sessions.begin() as session:
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        await scope(session, p, shop_id, "audit")
        result, _ = await members(session, shop_id, data)
        return result


@snapshot_write
async def save(
    r: Resources, p: Principal, shop_id: UUID, data: SegmentIn, key: str, run_id: UUID | None = None
) -> SegmentOut:
    async with r.sessions.begin() as session:
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
        await writer(session, p, shop_id, run_id)
        prior = await replay(session, p, shop_id, "segment.save", key, data.model_dump(mode="json"))
        row = await session.get(CustomerSegment, prior) if prior else None
        if row is None:
            result, ids = await members(session, shop_id, data)
            row = CustomerSegment(
                shop_id=shop_id,
                name=data.name,
                rules=data.model_dump(mode="json"),
                as_of=result.as_of,
                member_count=len(ids),
            )
            session.add(row)
            await session.flush()
            session.add_all(SegmentMember(segment_id=row.id, user_id=uid) for uid in ids)
            remember(session, p, shop_id, "segment.save", key, data.model_dump(mode="json"), row.id)
            session.add(
                audit(
                    p, shop_id, "segment.saved", row.id, f"固定客户快照人数 {len(ids)}，未触达客户"
                )
            )
        return SegmentOut.model_validate(row)


async def segments(
    r: Resources, p: Principal, shop_id: UUID, cursor: UUID | None, limit: int
) -> list[SegmentOut]:
    async with r.sessions() as session:
        await scope(session, p, shop_id, "audit")
        query = select(CustomerSegment).where(CustomerSegment.shop_id == shop_id)
        if cursor:
            query = query.where(CustomerSegment.id > cursor)
        return [
            SegmentOut.model_validate(x)
            for x in await session.scalars(query.order_by(CustomerSegment.id).limit(limit))
        ]


async def deactivate(r: Resources, p: Principal, shop_id: UUID, ident: UUID) -> SegmentOut:
    async with r.sessions.begin() as session:
        await writer(session, p, shop_id)
        row = await session.scalar(
            select(CustomerSegment)
            .where(CustomerSegment.id == ident, CustomerSegment.shop_id == shop_id)
            .with_for_update()
        )
        if row is None:
            raise missing()
        row.active = False
        session.add(
            audit(p, shop_id, "segment.deactivated", row.id, "停止新的定向资格，已创建订单不变")
        )
        return SegmentOut.model_validate(row)


async def repeat(
    r: Resources, p: Principal, shop_id: UUID, data: PeriodIn, days: int = 30
) -> RepeatOut:
    begin, end = bounds(data.start, data.end)
    async with r.sessions.begin() as session:
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        await scope(session, p, shop_id, "audit")
        as_of = now()
        histories: dict[UUID, list[Order]] = defaultdict(list)
        for order in await session.scalars(
            select(Order)
            .where(Order.shop_id == shop_id, Order.paid_at.is_not(None), Order.paid_at <= as_of)
            .order_by(Order.paid_at, Order.id)
        ):
            histories[order.user_id].append(order)
        cohort = mature = repeats = 0
        for history in histories.values():
            first = history[0].paid_at
            if first is None or not begin <= first < end:
                continue
            cohort += 1
            deadline = first + timedelta(days=days)
            if deadline <= as_of:
                mature += 1
                if any(o.paid_at is not None and o.paid_at < deadline for o in history[1:]):
                    repeats += 1
        return RepeatOut(
            as_of=as_of,
            cohort_buyers=cohort,
            mature_buyers=mature,
            immature_buyers=cohort - mature,
            repeat_buyers=repeats,
            repeat_rate=repeats / mature if mature else None,
            observation_days=days,
        )
