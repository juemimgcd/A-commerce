"""短事务领取 → 事务外 HTTP → 带 fence 写回；失败先保存核对责任再 ack。"""

import asyncio
import random
from datetime import timedelta
from uuid import UUID

import httpx
from pydantic import ValidationError
from sqlalchemy import select

from a_commerce.conf.resources import Resources
from a_commerce.crud.queries import order_locked
from a_commerce.integrations.payments.client import ProviderResult, request
from a_commerce.messaging.envelope import Envelope
from a_commerce.models.base import now
from a_commerce.models.entities import Operation, Order, Outbox, PaymentIntent, Refund
from a_commerce.services.events import record_event
from a_commerce.services.payments import apply_payment
from a_commerce.services.refunds import apply_refund
from a_commerce.workers.general import execute as execute_general
from a_commerce.workers.maintenance import execute as execute_maintenance


async def handle(r: Resources, message: Envelope) -> None:
    async with r.sessions.begin() as session:
        operation = await session.scalar(
            select(Operation).where(Operation.id == message.operation_id).with_for_update()
        )
        outbox = await session.get(Outbox, message.message_id)
        if (
            operation is None
            or outbox is None
            or outbox.operation_id != operation.id
            or outbox.dispatch_version != message.dispatch_version
            or operation.kind != message.operation_type
            or outbox.kind != message.operation_type
        ):
            raise ValueError("消息与数据库投递记录不匹配")
        if (
            operation.dispatch_version != message.dispatch_version
            or operation.status != "pending"
            or (operation.lease_until is not None and operation.lease_until > now())
        ):
            return
        operation.fence += 1
        fence = operation.fence
        operation.status = "running"
        operation.lease_until = now() + timedelta(seconds=r.settings.operation_lease_seconds)
        operation.next_attempt_at = operation.lease_until
        resource_id = operation.resource_id
        kind = operation.kind
    if kind in {"mail", "export", "cache"}:
        await execute_general(r, message.operation_id, fence, kind, resource_id)
        return
    if kind in {"reconcile", "housekeeping"}:
        await execute_maintenance(r, message.operation_id, fence, kind, resource_id)
        return
    order_id: UUID | None = None
    try:
        # 读取后释放 session；绝不在持有订单锁/数据库连接时等待提供方。
        async with r.sessions() as session:
            if kind == "refund":
                refund = await session.get(Refund, resource_id)
                if refund is None:
                    raise ValueError("退款记录不存在")
                order_id = refund.order_id
                amount = refund.amount_fen
                intent = (
                    await session.scalars(
                        select(PaymentIntent).where(PaymentIntent.order_id == order_id)
                    )
                ).one()
            else:
                intent_row = await session.get(PaymentIntent, resource_id)
                if intent_row is None:
                    raise ValueError("支付意图不存在")
                intent = intent_row
                order_id = intent.order_id
                order_row = await session.get(Order, order_id)
                if order_row is None:
                    raise ValueError("订单不存在")
                amount = order_row.total_fen
            intent_id = intent.id
        payload: dict[str, str | int] = {
            "intent_id": str(intent_id),
            "amount_fen": amount,
            "currency": "CNY",
            "merchant_id": r.settings.merchant_id,
        }
        async with asyncio.timeout(25):
            if kind == "refund":
                result = await request(r, "GET", f"/refunds/{resource_id}")
                if result is None:
                    result = await request(r, "PUT", f"/refunds/{resource_id}", payload)
            elif kind == "close":
                # 提供方支持关闭不存在的支付 ID：建立关闭墓碑，阻止迟到的 create 再开放付款。
                result = await request(r, "POST", f"/payments/{intent_id}/close", payload)
            else:
                result = await request(r, "GET", f"/payments/{intent_id}")
                if result is None:
                    result = await request(r, "PUT", f"/payments/{intent_id}", payload)
        if (
            result is None
            or result.intent_id != intent_id
            or result.amount_fen != amount
            or result.merchant_id != r.settings.merchant_id
            or result.id != (resource_id if kind == "refund" else intent_id)
            or (kind == "refund" and result.state != "succeeded")
            or (kind != "refund" and result.state == "succeeded")
        ):
            raise ValueError("提供方结果与操作不匹配")
        await finish(r, message.operation_id, fence, order_id, resource_id, kind, result)
    except (httpx.HTTPError, TimeoutError, ValidationError, ValueError) as exc:
        async with r.sessions.begin() as session:
            # 与正常写回保持订单 → 操作的锁顺序，用户端也明确看到资金结果未知。
            failed_order = await order_locked(session, order_id) if order_id is not None else None
            operation = await session.scalar(
                select(Operation).where(Operation.id == message.operation_id).with_for_update()
            )
            if operation is None or operation.fence != fence or operation.status != "running":
                return
            operation.attempts += 1
            operation.status = (
                "manual_review"
                if isinstance(exc, ValueError)
                or operation.attempts >= r.settings.operation_max_attempts
                else "unknown"
            )
            operation.last_error = type(exc).__name__
            changed_event: str | None = None
            if (
                failed_order is not None
                and kind != "refund"
                and failed_order.paid_at is None
                and failed_order.payment_status in {"preparing", "pending"}
            ):
                failed_order.payment_status = "unknown"
                changed_event = "payment.unknown"
            if kind == "refund":
                failed_refund = await session.get(Refund, resource_id)
                if failed_refund is not None and failed_refund.status not in {
                    "succeeded",
                    "unknown",
                }:
                    failed_refund.status = "unknown"
                    changed_event = "refund.unknown"
            if failed_order is not None and changed_event:
                failed_order.version += 1
                await record_event(session, failed_order, changed_event)
            operation.lease_until = None
            operation.next_attempt_at = now() + timedelta(
                seconds=min(300, 2 ** min(operation.attempts, 8)) + random.random()
            )


async def finish(
    r: Resources,
    operation_id: UUID,
    fence: int,
    order_id: UUID,
    resource_id: UUID,
    kind: str,
    result: ProviderResult,
) -> None:
    async with r.sessions.begin() as session:
        order = await order_locked(session, order_id)
        operation = (
            await session.scalars(
                select(Operation).where(Operation.id == operation_id).with_for_update()
            )
        ).one()
        if operation.fence != fence or operation.status != "running":
            return
        if kind == "refund":
            refund = (
                await session.scalars(
                    select(Refund).where(Refund.id == resource_id).with_for_update()
                )
            ).one()
            await apply_refund(session, order, refund)
        else:
            await apply_payment(session, order, result)
        operation.status = "retry_wait" if result.state == "pending" else "done"
        operation.lease_until = None
        operation.last_error = None
        operation.attempts = 0
        operation.next_attempt_at = now() + timedelta(seconds=15)
