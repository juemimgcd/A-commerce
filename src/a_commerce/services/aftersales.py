"""按成交行申请部分退货，收件验收后分别处理实物入库与退款余额。"""

import hashlib
from datetime import timedelta
from typing import Literal
from uuid import UUID

from pydantic import Field
from sqlalchemy import select

from a_commerce.conf.resources import Resources
from a_commerce.crud.queries import order_locked
from a_commerce.errors import CommerceError, missing
from a_commerce.models.base import now
from a_commerce.models.entities import (
    Audit,
    IdempotencyRecord,
    OrderItem,
    ReturnItem,
    ReturnRequest,
    Stock,
    StockMovement,
)
from a_commerce.schemas.api import InspectionIn, ReturnIn, ReturnOut, Schema
from a_commerce.services.events import record_event
from a_commerce.services.identity import Principal, authorize
from a_commerce.services.refunds import reserve_refund


class ReturnParcelIn(Schema):
    carrier: str = Field(min_length=1, max_length=80)
    tracking_number: str = Field(min_length=1, max_length=100)


class ReturnOption(Schema):
    order_item_id: UUID
    quantity_available: int
    unit_refund_fen: int


async def return_options(r: Resources, p: Principal, order_id: UUID) -> list[ReturnOption]:
    async with r.sessions() as session:
        await authorize(session, p)
        order = await order_locked(session, order_id)
        if order.user_id != p.id:
            raise missing()
        if order.delivered_at is None or now() > order.delivered_at + timedelta(
            days=int(order.policy["return_days"])
        ):
            return []
        rows = await session.scalars(
            select(OrderItem).where(OrderItem.order_id == order_id).order_by(OrderItem.id)
        )
        return [
            ReturnOption(
                order_item_id=row.id,
                quantity_available=row.quantity - row.returned_quantity,
                unit_refund_fen=row.price_fen,
            )
            for row in rows
        ]


async def progress(
    r: Resources,
    p: Principal,
    return_id: UUID,
    action: Literal["approve", "reject", "ship", "receive"],
    reason: str = "",
    parcel: ReturnParcelIn | None = None,
) -> ReturnOut:
    async with r.sessions.begin() as session:
        await authorize(session, p, None if action == "ship" else "aftersales", lock=True)
        record = await session.get(ReturnRequest, return_id)
        if record is None or (action == "ship" and record.user_id != p.id):
            raise missing()
        order = await order_locked(session, record.order_id)
        await session.refresh(record, with_for_update=True)
        before, after = {
            "approve": ("requested", "approved"),
            "reject": ("requested", "rejected"),
            "ship": ("approved", "shipped"),
            "receive": ("shipped", "received"),
        }[action]
        if record.status == after:
            return ReturnOut.model_validate(record)
        if record.status != before:
            raise CommerceError("RETURN_STATE_CONFLICT", "退货当前状态不允许该操作。")
        if action == "reject":
            rows = await session.execute(
                select(ReturnItem, OrderItem)
                .join(OrderItem, OrderItem.id == ReturnItem.order_item_id)
                .where(ReturnItem.return_id == record.id)
            )
            for line, item in rows:
                item.returned_quantity -= line.quantity
            record.inspection_note = reason
        if action == "ship":
            if parcel is None:
                raise CommerceError("PARCEL_REQUIRED", "请填写寄回物流信息。", 422)
            record.carrier = parcel.carrier
            record.tracking_number = parcel.tracking_number
        record.status = after
        order.version += 1
        session.add(
            Audit(
                actor_id=p.id,
                action=f"return.{action}",
                resource_id=record.id,
                detail=reason or f"退货状态 {before} → {after}",
            )
        )
        await record_event(session, order, f"return.{after}")
        return ReturnOut.model_validate(record)


async def request_return(
    r: Resources, p: Principal, order_id: UUID, data: ReturnIn, key: str
) -> ReturnOut:
    fingerprint = hashlib.sha256(f"{order_id}:{data.model_dump_json()}".encode()).hexdigest()
    async with r.sessions.begin() as session:
        await authorize(session, p, lock=True)
        order = await order_locked(session, order_id)
        if order.user_id != p.id:
            raise missing()
        previous = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.actor_id == p.id,
                IdempotencyRecord.action == "return.create",
                IdempotencyRecord.key == key,
            )
        )
        if previous:
            if previous.fingerprint != fingerprint:
                raise CommerceError("IDEMPOTENCY_CONFLICT", "同一幂等键不能用于不同退货申请。")
            return ReturnOut.model_validate(await session.get(ReturnRequest, previous.result_id))
        if order.version != data.expected_version or order.fulfillment_status != "delivered":
            raise CommerceError("ORDER_STATE_CONFLICT", "订单版本已变化或尚未签收。")
        if order.delivered_at is None or now() > order.delivered_at + timedelta(
            days=int(order.policy["return_days"])
        ):
            raise CommerceError("RETURN_WINDOW_EXPIRED", "已超过订单快照中的售后期限。")
        items = {
            i.id: i
            for i in await session.scalars(
                select(OrderItem)
                .where(OrderItem.order_id == order.id)
                .order_by(OrderItem.id)
                .with_for_update()
            )
        }
        record = ReturnRequest(order_id=order.id, user_id=p.id, reason=data.reason)
        session.add(record)
        await session.flush()
        for line in data.items:
            item = items.get(line.order_item_id)
            if item is None or item.returned_quantity + line.quantity > item.quantity:
                raise CommerceError("RETURN_QUANTITY_EXCEEDED", "退货数量超过尚可申请的数量。")
            # returned_quantity 包含已申请但未完成的占用，防止多个并发申请重复退同一件。
            item.returned_quantity += line.quantity
            session.add(
                ReturnItem(return_id=record.id, order_item_id=item.id, quantity=line.quantity)
            )
        order.version += 1
        session.add(
            IdempotencyRecord(
                actor_id=p.id,
                action="return.create",
                key=key,
                fingerprint=fingerprint,
                result_id=record.id,
            )
        )
        await record_event(session, order, "return.requested")
        return ReturnOut.model_validate(record)


async def get_return(
    r: Resources, p: Principal, return_id: UUID, *, admin: bool = False
) -> ReturnOut:
    async with r.sessions() as session:
        await authorize(session, p, "aftersales" if admin else None)
        record = await session.get(ReturnRequest, return_id)
        if record is None or (not admin and record.user_id != p.id):
            raise missing()
        return ReturnOut.model_validate(record)


async def inspect(r: Resources, p: Principal, return_id: UUID, data: InspectionIn) -> ReturnOut:
    async with r.sessions.begin() as session:
        await authorize(session, p, "aftersales", lock=True)
        record = await session.get(ReturnRequest, return_id)
        if record is None:
            raise missing()
        order = await order_locked(session, record.order_id)
        await session.refresh(record, with_for_update=True)
        if record.status == "inspected":
            return ReturnOut.model_validate(record)
        if record.status != "received":
            raise CommerceError("RETURN_STATE_CONFLICT", "当前退货不可验收。")
        rows = list(
            (
                await session.execute(
                    select(ReturnItem, OrderItem)
                    .join(OrderItem, OrderItem.id == ReturnItem.order_item_id)
                    .where(ReturnItem.return_id == record.id)
                    .order_by(OrderItem.sku_id)
                )
            ).all()
        )
        amount = sum(item.quantity * original.price_fen for item, original in rows)
        if data.resellable:
            stocks = {
                s.sku_id: s
                for s in await session.scalars(
                    select(Stock)
                    .where(Stock.sku_id.in_([original.sku_id for _, original in rows]))
                    .order_by(Stock.sku_id)
                    .with_for_update()
                )
            }
            for item, original in rows:
                stocks[original.sku_id].on_hand += item.quantity
                session.add(
                    StockMovement(
                        reference=f"return:{record.id}:{original.sku_id}",
                        sku_id=original.sku_id,
                        on_hand_delta=item.quantity,
                        reserved_delta=0,
                        allocated_delta=0,
                        reason="退货验收可再次销售",
                    )
                )
        # 本版快照明确部分退货不退运费，取消整单才退全额，避免重复退运费。
        refund = await reserve_refund(session, order, amount, f"return:{record.id}")
        record.status = "inspected"
        record.inspection_note = data.note
        record.refund_id = refund.id
        order.version += 1
        session.add(
            Audit(actor_id=p.id, action="return.inspect", resource_id=record.id, detail=data.note)
        )
        await record_event(session, order, "return.inspected")
        return ReturnOut.model_validate(record)
