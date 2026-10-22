"""退款先占用余额再交给 worker；成功前不能把占用额当作已退款额。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a_commerce.errors import CommerceError
from a_commerce.models.base import now
from a_commerce.models.entities import Order, Refund
from a_commerce.services.events import record_event
from a_commerce.services.operations import enqueue
from a_commerce.services.reporting import record_money


async def apply_refund(session: AsyncSession, order: Order, refund: Refund) -> None:
    """调用方必须先锁订单并验证提供方身份/金额；重复确认不重复释放余额或记账。"""
    if refund.status == "succeeded":
        return
    refund.status = "succeeded"
    refund.confirmed_at = now()
    order.refund_reserved_fen -= refund.amount_fen
    order.refunded_fen += refund.amount_fen
    order.version += 1
    if order.refunded_fen == order.total_fen:
        order.payment_status = "refunded"
    await record_money(session, order, "refund", refund.amount_fen, f"refund:{refund.id}")
    await record_event(session, order, "refund.succeeded")


async def reserve_refund(
    session: AsyncSession, order: Order, amount: int, reference: str
) -> Refund:
    existing = await session.scalar(select(Refund).where(Refund.reference == reference))
    if existing:
        return existing
    if (
        order.paid_at is None
        or amount <= 0
        or amount + order.refunded_fen + order.refund_reserved_fen > order.total_fen
    ):
        raise CommerceError("REFUND_BALANCE_EXCEEDED", "退款金额超过可退余额。")
    order.refund_reserved_fen += amount
    refund = Refund(order_id=order.id, amount_fen=amount, reference=reference)
    session.add(refund)
    await session.flush()
    await enqueue(session, "refund", refund.id)
    return refund
