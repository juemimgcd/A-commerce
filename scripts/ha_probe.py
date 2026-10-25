"""隔离 HA 验收的长连接探针，主机测试进程控制容器故障。"""

import asyncio
import json
import os

import aio_pika
from pamqp.commands import Basic
from sqlalchemy import text
from sqlalchemy.engine import make_url

from a_commerce.conf.resources import resources
from a_commerce.conf.settings import Settings


async def main() -> None:
    settings = Settings()
    if (
        os.environ.get("COMMERCE_HA_TEST") != "1"
        or make_url(settings.database_url.get_secret_value()).database != "commerce_load_acceptance"
    ):
        raise RuntimeError("probe requires disposable HA acceptance database")
    async with resources(settings) as r:
        connection = await aio_pika.connect_robust(
            settings.amqp_url.get_secret_value(),
            timeout=5,
            heartbeat=10,
            reconnect_interval=1,
            interleave=1,
            happy_eyeballs_delay=0.25,
            client_properties={"connection_name": "commerce-ha-probe"},
        )
        channel = await connection.channel(publisher_confirms=True, on_return_raises=True)
        queue = await channel.declare_queue(
            "ha.probe",
            durable=True,
            arguments={
                "x-queue-type": "quorum",
                "x-quorum-initial-group-size": 3,
                "x-queue-leader-locator": "client-local",
            },
        )
        held = None
        transaction = None
        received: list[dict[str, object]] = []
        print(json.dumps({"ready": True}), flush=True)
        try:
            while line := await asyncio.to_thread(input):
                command = json.loads(line)
                value: object = None
                result_message: dict[str, object]
                try:
                    async with asyncio.timeout(8):
                        action = command["action"]
                        if action == "exit":
                            break
                        if action == "sql":
                            async with r.engine.begin() as db:
                                result = await db.execute(text(command["sql"]))
                                value = (
                                    [dict(row) for row in result.mappings()]
                                    if result.returns_rows
                                    else []
                                )
                        elif action == "begin":
                            held = await r.engine.connect()
                            transaction = await held.begin()
                            await held.execute(text(command["sql"]))
                            value = "uncommitted"
                        elif action == "rollback":
                            assert transaction and held
                            try:
                                await transaction.rollback()
                            finally:
                                await held.close()
                                held = transaction = None
                            value = "rolled_back"
                        elif action == "publish":
                            confirmed = 0
                            for index in range(command["count"]):
                                confirmation = await channel.default_exchange.publish(
                                    aio_pika.Message(
                                        body=f"message-{index}".encode(),
                                        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                                    ),
                                    routing_key=queue.name,
                                    mandatory=True,
                                    timeout=3,
                                )
                                if not isinstance(confirmation, Basic.Ack):
                                    raise RuntimeError("publisher did not receive confirmation")
                                confirmed += 1
                            value = confirmed
                        elif action == "get":
                            message = await queue.get(timeout=3, fail=False)
                            value = None
                            if message is not None:
                                value = {
                                    "body": message.body.decode(),
                                    "redelivered": message.redelivered,
                                }
                                if command.get("ack", True):
                                    await message.ack()
                        elif action == "drain":
                            # 重连可能在一批消费中间打断操作，保留已交付记录，避免测试丢掉
                            # 已 ack 消息的证据；空队列也不代表旧连接的未 ack 消息已全部重投。
                            while message := await queue.get(timeout=3, fail=False):
                                received.append(
                                    {
                                        "body": message.body.decode(),
                                        "redelivered": message.redelivered,
                                    }
                                )
                                await message.ack()
                            value = received
                        else:
                            raise ValueError("unknown action")
                    result_message = {"ok": True, "value": value}
                except Exception as exc:
                    result_message = {"ok": False, "error": type(exc).__name__}
                print(json.dumps(result_message, default=str), flush=True)
        finally:
            if held is not None:
                await held.close()
            await connection.close()


if __name__ == "__main__":
    asyncio.run(main())
