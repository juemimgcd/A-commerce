"""Outbox relay 与扫描入口：发布等待在事务外，确认丢失允许重复投递。"""

import asyncio
import random
import signal
import time
from datetime import timedelta
from uuid import uuid4

import aio_pika
import structlog
from pamqp.commands import Basic
from sqlalchemy import or_, select

from a_commerce.conf.resources import Resources, resources
from a_commerce.conf.settings import Settings
from a_commerce.messaging.envelope import Envelope
from a_commerce.messaging.topology import declare
from a_commerce.models.base import now
from a_commerce.models.entities import Outbox
from a_commerce.observability.background import heartbeat
from a_commerce.services.operations import scan
from a_commerce.services.orders import expire
from a_commerce.services.scheduling import schedule

log = structlog.get_logger()


async def relay_one(r: Resources, exchange: aio_pika.abc.AbstractExchange) -> bool:
    async with r.sessions.begin() as session:
        row = await session.scalar(
            select(Outbox)
            .where(
                Outbox.sent_at.is_(None),
                Outbox.available_at <= now(),
                or_(Outbox.lease_until.is_(None), Outbox.lease_until <= now()),
            )
            .order_by(Outbox.available_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if row is None:
            return False
        token = uuid4()
        row.lease_token = token
        row.lease_until = now() + timedelta(seconds=30)
        envelope = Envelope.model_validate(
            {
                "message_id": row.id,
                "operation_id": row.operation_id,
                "operation_type": row.kind,
                "dispatch_version": row.dispatch_version,
            }
        )
    confirmed = False
    try:
        result = await exchange.publish(
            aio_pika.Message(
                body=envelope.model_dump_json().encode(),
                content_type="application/json",
                message_id=str(envelope.message_id),
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            ),
            routing_key=envelope.operation_type,
            mandatory=True,
            timeout=5,
        )
        confirmed = isinstance(result, Basic.Ack)
    except Exception as exc:
        log.warning(
            "outbox_publish_failed",
            message_id=str(envelope.message_id),
            error_type=type(exc).__name__,
        )
    async with r.sessions.begin() as session:
        row = await session.scalar(
            select(Outbox).where(Outbox.id == envelope.message_id).with_for_update()
        )
        if row is not None and row.lease_token == token:
            row.lease_until = None
            row.attempts += 1
            if confirmed:
                row.sent_at = now()
            else:
                row.available_at = now() + timedelta(
                    seconds=min(60, 2 ** min(row.attempts, 6)) + random.random()
                )
    return True


async def main() -> None:
    settings = Settings()
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    async with resources(settings) as r:
        connection = await aio_pika.connect_robust(
            settings.amqp_url.get_secret_value(), timeout=5, heartbeat=30
        )
        instance, last_heartbeat = uuid4(), 0.0
        try:
            channel = await connection.channel(publisher_confirms=True, on_return_raises=True)
            exchange, _ = await declare(channel)
            while not stop.is_set():
                try:
                    if time.monotonic() - last_heartbeat >= 15:
                        await heartbeat(r, instance, "relay")
                        last_heartbeat = time.monotonic()
                    await scan(r)
                    await expire(r)
                    await schedule(r)
                    for _ in range(50):
                        if stop.is_set() or not await relay_one(r, exchange):
                            break
                except Exception as exc:
                    log.error("relay_cycle_failed", error_type=type(exc).__name__)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=1)
                except TimeoutError:
                    pass
        finally:
            await connection.close()
            await heartbeat(r, instance, "relay", stopped=True)


if __name__ == "__main__":
    asyncio.run(main())
