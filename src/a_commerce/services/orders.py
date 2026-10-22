"""订单查询与取消。取消和履约竞争同一订单锁，胜出者决定后续合法动作。"""

import base64
from datetime import datetime
from uuid import UUID

from sqlalchemy import literal, select, tuple_

from a_commerce.conf.resources import Resources
from a_commerce.crud.queries import order_locked, order_output, order_outputs
from a_commerce.errors import CommerceError, missing
from a_commerce.models.base import now
from a_commerce.models.entities import Audit, IdempotencyRecord, Order, PaymentIntent
from a_commerce.schemas.api import CancelIn, OrderFilter, OrderOut, OrderPage, PaymentOut
from a_commerce.services.events import record_event
from a_commerce.services.identity import Principal, authorize
from a_commerce.services.inventory import move_order_stock
from a_commerce.services.operations import enqueue
from a_commerce.services.refunds import reserve_refund


async def expire(r: Resources) -> None:
    """到期只进入关闭中；和用户取消一样留下事件，库存仍等待提供方终态确认。"""
    # 一个事务只持有一个用户的事件序号锁，避免多个扫描器跨用户倒序加锁。
    for _ in range(50):
        async with r.sessions.begin() as session:
            order = await session.scalar(
                select(Order)
                .where(Order.status == "pending_payment", Order.expires_at <= now())
                .order_by(Order.expires_at, Order.id)
                .limit(1)
                # 与 order_locked 保持相同锁强度；仍跳过其他正在处理的订单。
                .with_for_update(skip_locked=True, key_share=True)
            )
            if order is None:
                return
            order.status = "closing"
            order.version += 1
            intent = (
                await session.scalars(
                    select(PaymentIntent).where(PaymentIntent.order_id == order.id)
                )
            ).one()
            await enqueue(session, "close", intent.id)
            await record_event(session, order, "order.expired_closing")


async def by_request(r: Resources, p: Principal, key: str) -> OrderOut:
    async with r.sessions() as session:
        await authorize(session, p)
        record = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.actor_id == p.id,
                IdempotencyRecord.action == "order.create",
                IdempotencyRecord.key == key,
            )
        )
        order = await session.get(Order, record.result_id) if record else None
        if order is None:
            raise missing()
        return await order_output(session, order)


async def get_order(r: Resources, p: Principal, order_id: UUID, *, admin: bool = False) -> OrderOut:
    async with r.sessions() as session:
        await authorize(session, p, "fulfillment" if admin else None)
        order = await session.get(Order, order_id)
        if order is None or (not admin and order.user_id != p.id):
            raise missing()
        return await order_output(session, order)


async def list_orders(
    r: Resources,
    p: Principal,
    cursor: str | None,
    limit: int,
    *,
    admin: bool = False,
    filters: OrderFilter | None = None,
    user_id: UUID | None = None,
) -> OrderPage:
    async with r.sessions() as session:
        await authorize(session, p, "fulfillment" if admin else None)
        query = select(Order)
        if not admin:
            query = query.where(Order.user_id == p.id)
        elif user_id:
            query = query.where(Order.user_id == user_id)
        if filters:
            for field in ("status", "payment_status", "fulfillment_status"):
                value = getattr(filters, field)
                if value is not None:
                    query = query.where(getattr(Order, field) == value)
            if filters.created_from:
                query = query.where(Order.created_at >= filters.created_from)
            if filters.created_to:
                query = query.where(Order.created_at <= filters.created_to)
        if cursor:
            try:
                timestamp, ident = base64.urlsafe_b64decode(cursor).decode().split("|")
                date = datetime.fromisoformat(timestamp)
                if date.tzinfo is None:
                    raise ValueError("missing timezone")
                query = query.where(
                    tuple_(Order.created_at, Order.id) < tuple_(literal(date), literal(UUID(ident)))
                )
            except (ValueError, UnicodeError) as exc:
                raise CommerceError("INVALID_CURSOR", "无效的分页游标。", 422) from exc
        rows = list(
            (
                await session.scalars(
                    query.order_by(Order.created_at.desc(), Order.id.desc()).limit(limit + 1)
                )
            ).all()
        )
        items = await order_outputs(session, rows[:limit])
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = base64.urlsafe_b64encode(
                f"{last.created_at.isoformat()}|{last.id}".encode()
            ).decode()
        return OrderPage(items=items, next_cursor=next_cursor)


async def cancel(r: Resources, p: Principal, order_id: UUID, data: CancelIn) -> OrderOut:
    async with r.sessions.begin() as session:
        await authorize(session, p, lock=True)
        order = await order_locked(session, order_id)
        if order.user_id != p.id:
            raise missing()
        if order.status in {"cancelled", "closing"}:
            return await order_output(session, order)
        if order.version != data.expected_version or order.fulfillment_status != "unfulfilled":
            raise CommerceError("ORDER_STATE_CONFLICT", "订单已变化或已进入履约，不能取消。")
        if order.status == "pending_payment":
            order.status = "closing"
            intent = (
                await session.scalars(
                    select(PaymentIntent).where(PaymentIntent.order_id == order.id)
                )
            ).one()
            await enqueue(session, "close", intent.id)
        elif order.status == "paid":
            await move_order_stock(session, order, "release_allocated")
            order.status = "cancelled"
            await reserve_refund(session, order, order.total_fen, f"cancel:{order.id}")
        else:
            raise CommerceError("ORDER_STATE_CONFLICT", "当前订单不可取消。")
        order.version += 1
        session.add(
            Audit(actor_id=p.id, action="order.cancel", resource_id=order.id, detail=data.reason)
        )
        await record_event(session, order, "order.cancel_requested")
        return await order_output(session, order)


async def payment_session(r: Resources, p: Principal, order_id: UUID) -> PaymentOut:
    async with r.sessions() as session:
        await authorize(session, p)
        order = await session.get(Order, order_id)
        if order is None or order.user_id != p.id:
            raise missing()
        intent = (
            await session.scalars(select(PaymentIntent).where(PaymentIntent.order_id == order_id))
        ).one()
        return PaymentOut(
            intent_id=intent.id,
            status=order.payment_status,
            payment_path=f"/api/v1/customer/orders/{order_id}/simulate-payment",
        )
