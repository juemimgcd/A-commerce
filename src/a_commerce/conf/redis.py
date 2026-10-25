"""直连用于宿主机开发；部署通过 Sentinel 发现两个独立组的当前主节点。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from pydantic import BaseModel, Field, SecretStr, model_validator
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.asyncio.sentinel import Sentinel
from redis.backoff import NoBackoff


class RedisSentinelSettings(BaseModel):
    # JSON 数组或环境变量嵌套字段；禁止把 Docker 宿主机映射端口当成发现地址。
    nodes: list[tuple[str, int]] = Field(default_factory=list)
    cache_master: str = "commerce-cache"
    rate_master: str = "commerce-rate"
    password: SecretStr | None = None
    cache_password: SecretStr | None = None
    rate_password: SecretStr | None = None

    @model_validator(mode="after")
    def validate_topology(self) -> "RedisSentinelSettings":
        if self.nodes:
            if len(set(self.nodes)) < 3:
                raise ValueError("Sentinel mode requires at least three distinct endpoints")
            if any(not host or not 1 <= port <= 65535 for host, port in self.nodes):
                raise ValueError("Invalid Sentinel endpoint")
            if (
                not self.cache_master
                or not self.rate_master
                or self.cache_master == self.rate_master
            ):
                raise ValueError("Cache and rate limiting require distinct master names")
            for secret in (self.password, self.cache_password, self.rate_password):
                if secret is None or len(secret.get_secret_value()) < 32:
                    raise ValueError(
                        "Sentinel and both Redis groups require passwords of 32+ chars"
                    )
        return self


@asynccontextmanager
async def redis_client(
    url: SecretStr,
    sentinel: RedisSentinelSettings,
    *,
    rate: bool,
    max_connections: int,
) -> AsyncIterator[Redis]:
    manager = None
    # 不重放不确定是否执行成功的 Lua 扣减；下一次请求重新发现主节点。
    # 限流调用外层仍有包含发现/连接的 350ms 总预算。
    timeout = 0.2 if rate else 1.0
    if sentinel.nodes:
        assert sentinel.password and sentinel.cache_password and sentinel.rate_password
        manager = Sentinel(  # type: ignore[no-untyped-call] # redis-py 6.4 constructor lacks types
            sentinel.nodes,
            min_other_sentinels=1,
            sentinel_kwargs={
                "password": sentinel.password.get_secret_value(),
                "socket_connect_timeout": 0.08,
                "socket_timeout": 0.08,
                "max_connections": max_connections,
                "retry": Retry(NoBackoff(), 0),
            },
        )
        client = manager.master_for(
            sentinel.rate_master if rate else sentinel.cache_master,
            password=(
                sentinel.rate_password if rate else sentinel.cache_password
            ).get_secret_value(),
            socket_connect_timeout=timeout,
            socket_timeout=timeout,
            max_connections=max_connections,
            decode_responses=True,
            retry=Retry(NoBackoff(), 0),
        )
    else:
        client = Redis.from_url(
            url.get_secret_value(),
            max_connections=max_connections,
            socket_connect_timeout=timeout,
            socket_timeout=timeout,
            decode_responses=True,
            retry=Retry(NoBackoff(), 0),
        )
    try:
        yield client
    finally:
        await client.aclose()
        if manager is not None:
            for node in manager.sentinels:
                await node.aclose()
