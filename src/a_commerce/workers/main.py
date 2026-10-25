"""有界异步消费进程；数据库故障不 ack，退出时先取消订阅再等待在途任务。"""

import asyncio
import hashlib
import signal
from functools import partial
from uuid import UUID, uuid4

import aio_pika
import structlog
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from a_commerce.conf.resources import Resources, resources
from a_commerce.conf.settings import Settings
from a_commerce.messaging.envelope import Envelope
from a_commerce.messaging.topology import declare
from a_commerce.models.entities import MessageFailure, Operation, Outbox
from a_commerce.observability.background import heartbeat
from a_commerce.workers.handlers import handle

log = structlog.get_logger()


async def isolate(r: Resources, message: aio_pika.abc.AbstractIncomingMessage, reason: str) -> None:
    digest = hashlib.sha256(message.body).hexdigest()
    async with r.sessions.begin() as session:
        await session.execute(
            insert(MessageFailure)
            .values(digest=digest, message_id=(message.message_id or "")[:100], reason=reason[:200])
            .on_conflict_do_nothing(index_elements=["digest"])
        )
        # 只隔离能和数据库消息精确匹配的当前投递；伪造的 message_id 不能停掉任意订单。
        try:
            body = Envelope.model_validate_json(message.body)
            outbox = await session.get(Outbox, UUID(message.message_id or ""))
        except (ValueError, ValidationError):
            return
        if (
            outbox is None
            or outbox.id != body.message_id
            or outbox.operation_id != body.operation_id
        ):
            return
        operation = await session.scalar(
            select(Operation).where(Operation.id == body.operation_id).with_for_update()
        )
        if (
            operation is not None
            and operation.status != "done"
            and operation.dispatch_version == body.dispatch_version
            and outbox.dispatch_version == body.dispatch_version
            and outbox.kind == body.operation_type
        ):
            operation.status = "manual_review"
            operation.fence += 1
            operation.last_error = "isolated_message"


async def main() -> None:
    settings = Settings()
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    async with resources(settings) as r:
        connection = await aio_pika.connect_robust(
            settings.amqp_url.get_secret_value(),
            timeout=5,
            heartbeat=10,
            reconnect_interval=2,
            interleave=1,
            happy_eyeballs_delay=0.25,
        )
        channel = await connection.channel()
        # quorum queue 不支持 global QoS。每个订阅只预取一条，四个订阅合计最多四条，
        # 实际业务并发再由同一 semaphore 限制，不能按队列倍增进程预算。
        await channel.set_qos(prefetch_count=1)
        _, queues = await declare(channel)
        slots = asyncio.Semaphore(settings.worker_concurrency)
        active: set[asyncio.Task[None]] = set()

        async def consume(
            message: aio_pika.abc.AbstractIncomingMessage, *, isolation: bool = False
        ) -> None:
            task = asyncio.current_task()
            if task is not None:
                active.add(task)
            try:
                async with slots:
                    if isolation:
                        await isolate(r, message, "broker_dead_letter")
                        await message.ack()
                        return
                    try:
                        if len(message.body) > 8192:
                            raise ValueError("消息超过 8 KiB")
                        body = Envelope.model_validate_json(message.body)
                        if message.routing_key != body.operation_type:
                            raise ValueError("消息类型与路由不一致")
                        await handle(r, body)
                    except (ValidationError, ValueError):
                        await isolate(r, message, "invalid_envelope")
                        await message.reject(requeue=False)
                        return
                    await message.ack()
            except Exception as exc:
                log.error("consumer_failed", error_type=type(exc).__name__)
                # 退出并由进程管理器退避重启，关闭连接后 broker 重投未确认消息。
                stop.set()
            finally:
                if task is not None:
                    active.discard(task)

        tags = []
        instance = uuid4()
        try:
            for kind, queue in queues.items():
                allowed = {
                    "critical": {"payment", "close", "refund"},
                    "general": {"mail", "export", "cache", "isolation"},
                    "maintenance": {"reconcile", "housekeeping"},
                }[settings.worker_group]
                if kind not in allowed:
                    continue
                tag = await queue.consume(partial(consume, isolation=kind == "isolation"))
                tags.append((queue, tag))
            while not stop.is_set():
                await heartbeat(r, instance, settings.worker_group)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=15)
                except TimeoutError:
                    pass
            for queue, tag in tags:
                await queue.cancel(tag)
            if active:
                _, pending = await asyncio.wait(active, timeout=30)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
        finally:
            await connection.close()
            await heartbeat(r, instance, settings.worker_group, stopped=True)


if __name__ == "__main__":
    asyncio.run(main())
