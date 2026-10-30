"""退款先占用余额再交给 worker；成功前不能把占用额当作已退款额。"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from a_commerce.errors import CommerceError
from a_commerce.models.base import now
from a_commerce.models.entities import Order, OrderItem, Refund, RefundItem
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
    session: AsyncSession,
    order: Order,
    amount: int,
    reference: str,
    items: list[tuple[OrderItem, int]] | None = None,
) -> Refund:
    existing = await session.scalar(select(Refund).where(Refund.reference == reference))
    if existing:
        if existing.order_id != order.id or existing.amount_fen != amount:
            raise CommerceError("REFUND_CONFLICT", "退款引用与原请求不一致。", 409)
        return existing
    if (
        order.paid_at is None
        or amount <= 0
        or amount + order.refunded_fen + order.refund_reserved_fen > order.total_fen
    ):
        raise CommerceError("REFUND_BALANCE_EXCEEDED", "退款金额超过可退余额。")
    shipping = 0
    if items is None:
        if not reference.startswith(("cancel:", "late:")) or amount != order.total_fen:
            raise CommerceError("REFUND_ALLOCATION_REQUIRED", "退款必须明确关联原成交明细。", 409)
        items = [
            (item, item.quantity)
            for item in await session.scalars(
                select(OrderItem).where(OrderItem.order_id == order.id).order_by(OrderItem.id)
            )
        ]
        shipping = order.shipping_fen
    if sum(item.price_fen * quantity for item, quantity in items) + shipping != amount:
        raise CommerceError("REFUND_ALLOCATION_MISMATCH", "退款分配与金额不一致。", 409)
    for item, quantity in items:
        reserved = await session.scalar(
            select(func.coalesce(func.sum(RefundItem.amount_fen), 0)).where(
                RefundItem.order_item_id == item.id
            )
        )
        if (
            item.order_id != order.id
            or quantity <= 0
            or int(reserved or 0) + item.price_fen * quantity > item.price_fen * item.quantity
        ):
            raise CommerceError("REFUND_LINE_EXCEEDED", "退款超过原明细可退金额。", 409)
    order.refund_reserved_fen += amount
    refund = Refund(
        order_id=order.id, amount_fen=amount, reference=reference, shipping_fen=shipping
    )
    session.add(refund)
    await session.flush()
    session.add_all(
        [
            RefundItem(
                refund_id=refund.id,
                order_item_id=item.id,
                quantity=quantity,
                amount_fen=item.price_fen * quantity,
            )
            for item, quantity in items
        ]
    )
    await enqueue(session, "refund", refund.id)
    return refund
