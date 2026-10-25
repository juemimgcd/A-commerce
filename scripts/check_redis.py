"""容器内的 Redis 部署门禁：发现、认证、主节点和两条复制连接。"""

import asyncio

from redis.asyncio.sentinel import Sentinel
from redis.exceptions import RedisError

from a_commerce.conf.resources import resources
from a_commerce.conf.settings import Settings


async def check() -> None:
    settings = Settings()
    config = settings.redis_sentinel
    if not config.nodes or config.password is None:
        raise RuntimeError("Deployment requires Sentinel mode")
    sentinel = Sentinel(  # type: ignore[no-untyped-call] # redis-py 6.4 constructor lacks types
        config.nodes,
        sentinel_kwargs={
            "password": config.password.get_secret_value(),
            "socket_connect_timeout": 1,
            "socket_timeout": 1,
        },
    )
    try:
        async with resources(settings) as r, asyncio.timeout(90):
            while True:
                try:
                    for name, client in (
                        (config.cache_master, r.redis),
                        (config.rate_master, r.rate_redis),
                    ):
                        for node in sentinel.sentinels:
                            await node.execute_command("SENTINEL", "CKQUORUM", name)  # type: ignore[no-untyped-call]
                        info = await client.info("replication")
                        if info["role"] != "master" or info["connected_slaves"] < 2:
                            raise RuntimeError("replication not ready")
                        if any(info[f"slave{index}"]["state"] != "online" for index in range(2)):
                            raise RuntimeError("replicas still synchronizing")
                        await client.ping()
                    break
                except (RedisError, RuntimeError):
                    await asyncio.sleep(1)
        print(
            "Redis Sentinel: both groups discovered, authenticated, quorum and two replicas ready"
        )
    finally:
        for node in sentinel.sentinels:
            await node.aclose()


if __name__ == "__main__":
    asyncio.run(check())
