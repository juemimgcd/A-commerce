"""拣货、发货和签收状态机。库存只在真正发货时扣减实物数量。"""

from uuid import UUID

from a_commerce.conf.resources import Resources
from a_commerce.crud.queries import order_locked, order_output
from a_commerce.errors import CommerceError
from a_commerce.models.base import now
from a_commerce.models.entities import Audit, Shipment
from a_commerce.schemas.api import OrderOut, ShipmentIn, VersionIn
from a_commerce.services.events import record_event
from a_commerce.services.identity import Principal, authorize
from a_commerce.services.inventory import move_order_stock


async def transition(
    r: Resources, p: Principal, order_id: UUID, action: str, data: VersionIn | ShipmentIn
) -> OrderOut:
    async with r.sessions.begin() as session:
        await authorize(session, p, "fulfillment", lock=True)
        order = await order_locked(session, order_id)
        allowed = {
            "pick": ("unfulfilled", "fulfilling"),
            "ship": ("fulfilling", "shipped"),
            "deliver": ("shipped", "delivered"),
        }
        before, after = allowed[action]
        if (
            order.version != data.expected_version
            or order.status != "paid"
            or order.fulfillment_status != before
        ):
            raise CommerceError("ORDER_STATE_CONFLICT", "订单版本或履约状态不允许该操作。")
        if action == "ship":
            if not isinstance(data, ShipmentIn):
                raise CommerceError("INVALID_SHIPMENT", "缺少物流信息。", 422)
            await move_order_stock(session, order, "ship")
            session.add(
                Shipment(
                    order_id=order.id, carrier=data.carrier, tracking_number=data.tracking_number
                )
            )
        elif action == "deliver":
            order.delivered_at = now()
        order.fulfillment_status = after
        order.version += 1
        session.add(
            Audit(
                actor_id=p.id,
                action=f"fulfillment.{action}",
                resource_id=order.id,
                detail=f"{before} → {after}",
            )
        )
        await record_event(session, order, f"order.{after}")
        return await order_output(session, order)
