"""共享令牌桶：先判断一组额度，再统一扣减；限流不能代替库存事务或幂等。"""

import asyncio
import hashlib
import math
from collections.abc import Awaitable
from dataclasses import dataclass

from prometheus_client import Counter
from redis.exceptions import NoScriptError, RedisError

from a_commerce.conf.resources import Resources
from a_commerce.conf.traffic import Quota
from a_commerce.errors import CommerceError

DECISIONS = Counter(
    "commerce_rate_limit_total", "流控结果；不以用户、IP 或 SKU 作为标签", ["policy", "outcome"]
)

# 用 Redis 时间避免两个 API 主机的时钟偏差。拒绝时不写入任何桶，避免
# 一个热点 SKU 耗尽时仍扣掉其他 SKU / 全局额度；拒绝请求也不延长 key 寿命。
# 所有 key 使用相同 hash tag；当前部署是独立 Redis，不能靠增加 Redis 分片扩容此脚本。
# Lua 的原子执行不意味着运行错误会回滚：OOM 等错误一律拒绝本次写请求，
# 已扣的部分额度不补发，最多造成保守拒绝，不能因为存储故障绕过保护。
TOKEN_BUCKET = """
local clock = redis.call('TIME')
local now = tonumber(clock[1]) * 1000 + tonumber(clock[2]) / 1000
local balances = {}
local timestamps = {}
local wait = 0
local blocked = 0
for i, key in ipairs(KEYS) do
    local rate = tonumber(ARGV[i * 2 - 1])
    local capacity = tonumber(ARGV[i * 2])
    local state = redis.call('HMGET', key, 'tokens', 'updated')
    local previous = tonumber(state[2]) or now
    local current = math.max(now, previous)
    local tokens = math.min(capacity,
        (tonumber(state[1]) or capacity) + (current - previous) * rate / 1000)
    balances[i] = tokens
    timestamps[i] = current
    if tokens < 1 then
        local delay = math.ceil((1 - tokens) / rate * 1000)
        if delay > wait then wait = delay; blocked = i end
    end
end
if blocked > 0 then return {0, wait, blocked} end
for i, key in ipairs(KEYS) do
    local rate = tonumber(ARGV[i * 2 - 1])
    local capacity = tonumber(ARGV[i * 2])
    redis.call('HSET', key, 'tokens', balances[i] - 1, 'updated', timestamps[i])
    redis.call('PEXPIRE', key, math.ceil(capacity / rate * 1000))
end
return {1, 0, 0}
"""
TOKEN_BUCKET_SHA = hashlib.sha1(TOKEN_BUCKET.encode(), usedforsecurity=False).hexdigest()


@dataclass(frozen=True)
class Limit:
    policy: str
    subject: str
    quota: Quota


async def enforce(r: Resources, limits: list[Limit], *, fail_open: bool = False) -> None:
    """只有允许降级的查询和已验签回调可以 fail_open；写入默认失败关闭。"""
    if not limits:
        return
    keys = [
        f"commerce:rate:v1:{{traffic}}:{item.policy}:"
        f"{hashlib.sha256(item.subject.encode()).hexdigest()}"
        for item in limits
    ]
    arguments = [str(value) for item in limits for value in (item.quota.rate, item.quota.burst)]
    try:
        # 包含连接建立、连接池与重试的总预算；不在 API 内排队等待令牌。
        async with asyncio.timeout(0.35):
            try:
                pending = r.rate_redis.evalsha(TOKEN_BUCKET_SHA, len(keys), *keys, *arguments)
                result = await pending if isinstance(pending, Awaitable) else pending
            except NoScriptError:
                # 仅 NOSCRIPT 可重发：服务器没有执行扣减。连接/超时错误仍不重试。
                pending = r.rate_redis.eval(TOKEN_BUCKET, len(keys), *keys, *arguments)
                result = await pending if isinstance(pending, Awaitable) else pending
    except (RedisError, TimeoutError) as exc:
        for policy in {item.policy for item in limits}:
            DECISIONS.labels(
                policy, "unavailable_open" if fail_open else "unavailable_closed"
            ).inc()
        if fail_open:
            return
        raise CommerceError(
            "RATE_LIMIT_UNAVAILABLE",
            "流量保护暂时不可用，请稍后重试。",
            503,
            True,
            retry_after=1,
        ) from exc
    if int(result[0]) != 1:
        policy = limits[int(result[2]) - 1].policy
        DECISIONS.labels(policy, "rejected").inc()
        raise CommerceError(
            "RATE_LIMITED",
            "请求超过当前操作额度，请等待后重试。",
            429,
            True,
            retry_after=max(1, math.ceil(int(result[1]) / 1000)),
        )
    for policy in {item.policy for item in limits}:
        DECISIONS.labels(policy, "allowed").inc()
