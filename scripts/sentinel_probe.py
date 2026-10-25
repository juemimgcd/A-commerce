"""仅供隔离验收的长连接探针；通过 stdin/stdout 控制，主机负责停止测试容器。"""

import asyncio
import json
import os

from redis.asyncio import Redis
from redis.exceptions import RedisError

from a_commerce.conf.resources import resources
from a_commerce.conf.settings import Settings
from a_commerce.conf.traffic import Quota
from a_commerce.errors import CommerceError
from a_commerce.traffic import Limit, enforce


async def main() -> None:
    if os.environ.get("COMMERCE_SENTINEL_TEST") != "1":
        raise RuntimeError("probe requires an isolated acceptance environment")
    settings = Settings()
    async with resources(settings) as first, resources(settings) as second:
        print(json.dumps({"ready": True}), flush=True)
        while line := await asyncio.to_thread(input):
            command = json.loads(line)
            r = second if command.get("second") else first
            client = r.rate_redis if command.get("rate") else r.redis
            try:
                async with asyncio.timeout(5):
                    action = command["action"]
                    if action == "exit":
                        return
                    if action == "set":
                        value = await client.set(command["key"], command["value"], ex=300)
                    elif action == "get":
                        value = await client.get(command["key"])
                    elif action == "info":
                        value = await client.info("replication")
                    elif action == "wait":
                        # pipeline 使用同一连接，在 SET 之后 WAIT 确保本测试标记已复制。
                        # 不将该测试屏障当作应用限流拥有同步复制保证。
                        async with client.pipeline(transaction=False) as pipe:
                            pipe.set(command["key"], command["value"], ex=300)
                            pipe.wait(2, 3000)
                            value = await pipe.execute()
                    elif action == "limit":
                        await enforce(
                            r,
                            [Limit("sentinel-test", command["key"], Quota(rate=0.001, burst=2))],
                            fail_open=command.get("fail_open", False),
                        )
                        value = "allowed"
                    elif action == "unauthenticated":
                        async with Redis(
                            host=command["host"], port=command["port"], socket_timeout=1
                        ) as unauthenticated:
                            value = await unauthenticated.ping()
                    else:
                        raise ValueError("unknown probe action")
                response = {"ok": True, "value": value}
            except CommerceError as exc:
                response = {"ok": False, "error": exc.code}
            except (RedisError, TimeoutError) as exc:
                # 不向制品输出地址/凭证/配置全文。
                response = {"ok": False, "error": type(exc).__name__}
            print(json.dumps(response), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
