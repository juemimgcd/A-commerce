"""HA 部署门禁：三节点复制、可写主节点、三成员 quorum 队列。"""

import argparse
import asyncio
import os
from urllib.parse import unquote, urlsplit

import httpx
from sqlalchemy import text

from a_commerce.conf.database import database_engine


async def check(bootstrap: bool) -> None:
    engine = database_engine(os.environ["COMMERCE_DATABASE_URL"])
    broker = urlsplit(os.environ["COMMERCE_AMQP_URL"])
    auth = httpx.BasicAuth(unquote(broker.username or ""), unquote(broker.password or ""))
    try:
        async with httpx.AsyncClient(timeout=3, auth=auth) as http, asyncio.timeout(180):
            while True:
                try:
                    async with engine.connect() as connection:
                        if await connection.scalar(text("SELECT pg_is_in_recovery()")):
                            raise RuntimeError("database connection is not primary")
                        if (
                            await connection.scalar(
                                text("SELECT count(*) FROM pg_stat_replication")
                            )
                            != 2
                        ):
                            raise RuntimeError("two PostgreSQL replicas required")
                        if not await connection.scalar(text("SHOW synchronous_standby_names")):
                            raise RuntimeError("synchronous standby required")
                    for node in ("pg-1", "pg-2", "pg-3"):
                        response = await http.get(f"http://{node}:8008/health")
                        response.raise_for_status()
                    response = await http.get("http://rabbit-ha:15672/api/nodes")
                    response.raise_for_status()
                    nodes = response.json()
                    if len(nodes) != 3 or not all(node["running"] for node in nodes):
                        raise RuntimeError("three RabbitMQ nodes required")
                    if not bootstrap:
                        response = await http.get("http://rabbit-ha:15672/api/queues/commerce")
                        response.raise_for_status()
                        queues = response.json()
                        business = [
                            queue for queue in queues if queue["name"].startswith("commerce.")
                        ]
                        if len(business) != 9 or any(
                            queue["type"] != "quorum"
                            or len(queue.get("members", [])) != 3
                            or len(queue.get("online", [])) != 3
                            for queue in business
                        ):
                            raise RuntimeError("all nine queues require three online members")
                    break
                except Exception:
                    await asyncio.sleep(1)
        print("HA ready: writable PostgreSQL + two replicas, RabbitMQ three-node quorum")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootstrap", action="store_true", help="队列声明前只检查节点")
    asyncio.run(check(parser.parse_args().bootstrap))
