"""支付状态只接受已验证的提供方事实；迟到收款走补偿，不重新履约。"""

import hashlib
import hmac
import time
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from a_commerce.conf.resources import Resources
from a_commerce.crud.queries import order_locked
from a_commerce.errors import CommerceError, missing
from a_commerce.integrations.payments.client import ProviderResult, request
from a_commerce.models.base import now
from a_commerce.models.entities import Order, PaymentIntent, ProviderEvent
from a_commerce.schemas.api import PaymentCallback
from a_commerce.services.events import record_event
from a_commerce.services.identity import Principal, authorize
from a_commerce.services.inventory import move_order_stock
from a_commerce.services.refunds import reserve_refund
from a_commerce.services.reporting import record_money
from a_commerce.traffic import Limit, enforce


async def apply_payment(session: AsyncSession, order: Order, result: ProviderResult) -> None:
    # pending/closed 不能覆盖 paid。已经取消的订单收到成功收款也必须记账并退款。
    if result.state == "paid":
        if order.paid_at is not None:
            return
        order.paid_at = now()
        order.payment_status = "paid"
        if order.status == "pending_payment" and order.expires_at > now():
            await move_order_stock(session, order, "allocate")
            order.status = "paid"
        else:
            if order.status in {"pending_payment", "closing"}:
                await move_order_stock(session, order, "release_reserved")
            order.status = "cancelled"
            await reserve_refund(session, order, order.total_fen, f"late:{order.id}")
        order.version += 1
        await record_money(
            session, order, "payment", order.total_fen, f"payment:{result.intent_id}"
        )
        await record_event(session, order, "payment.paid")
    elif result.state == "closed" and order.paid_at is None and order.status != "cancelled":
        await move_order_stock(session, order, "release_reserved")
        order.status = "cancelled"
        order.payment_status = "closed"
        order.version += 1
        await record_event(session, order, "order.cancelled")
    elif result.state == "pending" and order.payment_status in {"preparing", "unknown"}:
        order.payment_status = "pending"
        order.version += 1
        await record_event(session, order, "payment.pending")


async def callback(r: Resources, body: bytes, timestamp: str, signature: str) -> None:
    try:
        fresh = abs(time.time() - int(timestamp)) <= 300
    except ValueError:
        fresh = False
    digest = hmac.new(
        r.settings.callback_secret.get_secret_value().encode(),
        timestamp.encode() + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    if not fresh or not hmac.compare_digest(digest, signature):
        raise CommerceError("INVALID_SIGNATURE", "回调签名无效或已过期。", 401)
    data = PaymentCallback.model_validate_json(body)
    # 先验签再消耗提供方额度，伪造请求不能耗尽合法回调桶。流控存储故障时
    # 保留资金回调通道，由专用 API 的并发/数据库池和网关本地预算继续保护。
    await enforce(
        r,
        [Limit("payment_callback", "simulator", r.settings.traffic.payment_callback)],
        fail_open=True,
    )
    async with r.sessions.begin() as session:
        intent = await session.get(PaymentIntent, data.intent_id)
        if intent is None:
            raise missing()
        order = await order_locked(session, intent.order_id)
        if data.amount_fen != order.total_fen or data.merchant_id != r.settings.merchant_id:
            raise CommerceError("PAYMENT_MISMATCH", "支付金额或商户不匹配。")
        inserted = await session.scalar(
            insert(ProviderEvent)
            .values(event_key=str(data.event_id), intent_id=intent.id, state=data.state)
            .on_conflict_do_nothing(index_elements=["event_key"])
            .returning(ProviderEvent.id)
        )
        if inserted is None:
            return
        await apply_payment(
            session,
            order,
            ProviderResult(
                id=intent.id,
                intent_id=intent.id,
                merchant_id=data.merchant_id,
                amount_fen=data.amount_fen,
                currency=data.currency,
                state=data.state,
            ),
        )


async def simulate_payment(r: Resources, p: Principal, order_id: UUID) -> None:
    if r.settings.environment != "development":
        raise missing()
    async with r.sessions() as session:
        await authorize(session, p)
        order = await session.get(Order, order_id)
        if order is None or order.user_id != p.id:
            raise missing()
        if order.status != "pending_payment" or order.expires_at <= now():
            raise CommerceError("ORDER_STATE_CONFLICT", "当前订单不能发起模拟付款。")
        intent = (
            await session.scalars(select(PaymentIntent).where(PaymentIntent.order_id == order_id))
        ).one()
        intent_id = intent.id
    # 仅通知独立提供方完成用户付款；本接口不直接将本地订单改成 paid。
    # 回调或 worker 的主动查询才会把真实支付结果同步回来。
    await request(r, "POST", f"/payments/{intent_id}/pay")
