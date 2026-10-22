"""公开目录的有界 cache-aside。缓存永不参与报价、库存和授权判断。"""

import asyncio
import random
from collections.abc import Awaitable, Callable

from redis.exceptions import RedisError

from a_commerce.conf.resources import Resources
from a_commerce.errors import CommerceError
from a_commerce.schemas.api import ProductPage


async def cached_catalog(
    r: Resources, key: str, load: Callable[[], Awaitable[ProductPage]]
) -> ProductPage:
    try:
        generation = await r.redis.get("catalog:generation") or "0"
        key = f"{key}:g{generation}"
        cached = await r.redis.get(key)
        if cached:
            return ProductPage.model_validate_json(cached)
    except RedisError:
        pass
    # 所有 cache miss 共用小预算；Redis 故障时不把全部流量直接转给数据库。
    try:
        async with asyncio.timeout(0.25):
            await r.origin_slots.acquire()
    except TimeoutError as exc:
        raise CommerceError("CATALOG_BUSY", "目录繁忙，请稍后重试。", 503, True) from exc
    try:
        # 排队后再次检查，合并同一热点 key 已经完成的回源。
        try:
            cached = await r.redis.get(key)
            if cached:
                return ProductPage.model_validate_json(cached)
        except RedisError:
            pass
        result = await load()
        try:
            await r.redis.set(
                key, result.model_dump_json(), ex=r.settings.cache_seconds + random.randint(0, 5)
            )
        except RedisError:
            pass
        return result
    finally:
        r.origin_slots.release()
