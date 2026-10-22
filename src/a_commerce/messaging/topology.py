"""版本化持久队列与隔离队列；生产死信策略通过部署配置单独核对。"""

from aio_pika import ExchangeType
from aio_pika.abc import AbstractChannel, AbstractExchange, AbstractQueue


async def declare(channel: AbstractChannel) -> tuple[AbstractExchange, dict[str, AbstractQueue]]:
    exchange = await channel.declare_exchange(
        "commerce.commands.v1", ExchangeType.DIRECT, durable=True
    )
    dlx = await channel.declare_exchange("commerce.isolation.v1", ExchangeType.FANOUT, durable=True)
    isolation = await channel.declare_queue(
        "commerce.isolation.v1",
        durable=True,
        arguments={"x-queue-type": "quorum", "x-max-length": 10000, "x-overflow": "reject-publish"},
    )
    await isolation.bind(dlx)
    queues: dict[str, AbstractQueue] = {"isolation": isolation}
    for kind in (
        "payment",
        "close",
        "refund",
        "mail",
        "export",
        "cache",
        "reconcile",
        "housekeeping",
    ):
        queue = await channel.declare_queue(
            f"commerce.{kind}.v1",
            durable=True,
            arguments={
                "x-queue-type": "quorum",
                "x-max-length": 10000,
                "x-overflow": "reject-publish",
                "x-delivery-limit": 20,
                "x-dead-letter-exchange": "commerce.isolation.v1",
                "x-dead-letter-strategy": "at-least-once",
            },
        )
        await queue.bind(exchange, routing_key=kind)
        queues[kind] = queue
    return exchange, queues
