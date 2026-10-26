"""物流回调去重与乱序处理；签收终态不被较早的运输中事件覆盖。"""

import hashlib
import hmac
import time
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from a_commerce.conf.resources import Resources
from a_commerce.crud.queries import order_locked
from a_commerce.errors import CommerceError, missing
from a_commerce.models.base import now
from a_commerce.models.entities import Order, Shipment, ShippingEvent
from a_commerce.schemas.api import Schema
from a_commerce.services.events import record_event
from a_commerce.services.identity import Principal, authorize
from a_commerce.traffic import Limit, enforce


class ShippingCallback(Schema):
    event_id: UUID
    shipment_id: UUID
    tracking_number: str = Field(max_length=100)
    state: Literal["in_transit", "exception", "delivered"]
    occurred_at: AwareDatetime


class ShipmentOut(Schema):
    id: UUID
    order_id: UUID
    carrier: str
    tracking_number: str


class ShippingEventOut(Schema):
    id: UUID
    state: str
    occurred_at: datetime


class ShipmentDetail(ShipmentOut):
    events: list[ShippingEventOut]


async def get_shipment(
    r: Resources, p: Principal, order_id: UUID, *, admin: bool = False
) -> ShipmentDetail:
    async with r.sessions() as session:
        await authorize(session, p, "fulfillment" if admin else None)
        order = await session.get(Order, order_id)
        if order is None or (not admin and order.user_id != p.id):
            raise missing()
        shipment = await session.scalar(select(Shipment).where(Shipment.order_id == order_id))
        if shipment is None:
            raise missing()
        rows = await session.scalars(
            select(ShippingEvent)
            .where(ShippingEvent.shipment_id == shipment.id)
            .order_by(ShippingEvent.occurred_at.desc())
            .limit(100)
        )
        return ShipmentDetail(
            id=shipment.id,
            order_id=order.id,
            carrier=shipment.carrier,
            tracking_number=shipment.tracking_number,
            events=[ShippingEventOut.model_validate(row) for row in rows],
        )


async def callback(r: Resources, body: bytes, timestamp: str, signature: str) -> None:
    try:
        fresh = abs(time.time() - int(timestamp)) <= 300
    except ValueError:
        fresh = False
    expected = hmac.new(
        r.settings.callback_secret.get_secret_value().encode(),
        timestamp.encode() + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    if not fresh or not hmac.compare_digest(expected, signature):
        raise CommerceError("INVALID_SIGNATURE", "物流回调签名无效。", 401)
    data = ShippingCallback.model_validate_json(body)
    # 与支付回调使用不同额度；先验签，Redis 故障时依靠回调进程的本地预算继续处理。
    await enforce(
        r,
        [Limit("shipping_callback", "simulator", r.settings.traffic.shipping_callback)],
        fail_open=True,
    )
    async with r.sessions.begin() as session:
        shipment = await session.get(Shipment, data.shipment_id)
        if shipment is None or shipment.tracking_number != data.tracking_number:
            raise missing()
        order = await order_locked(session, shipment.order_id)
        if data.occurred_at < shipment.created_at or data.occurred_at > now():
            raise CommerceError("INVALID_EVENT_TIME", "物流事件时间不在合理范围内。", 422)
        inserted = await session.scalar(
            insert(ShippingEvent)
            .values(
                event_key=str(data.event_id),
                shipment_id=shipment.id,
                state=data.state,
                occurred_at=data.occurred_at,
            )
            .on_conflict_do_nothing(index_elements=["event_key"])
            .returning(ShippingEvent.id)
        )
        if inserted is None or order.fulfillment_status == "delivered":
            return
        if data.state == "delivered":
            order.fulfillment_status = "delivered"
            order.delivered_at = data.occurred_at
        order.version += 1
        await record_event(session, order, f"shipping.{data.state}")
