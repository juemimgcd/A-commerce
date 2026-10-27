"""公开目录逻辑过期与有界回源；缓存永不参与报价、库存和授权判断。"""

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from uuid import uuid4

from prometheus_client import Counter, Histogram
from pydantic import BaseModel, ValidationError
from redis.exceptions import RedisError

from a_commerce.conf.resources import Resources
from a_commerce.errors import CommerceError
from a_commerce.schemas.api import ProductPage

CACHE = Counter("commerce_catalog_cache_total", "目录缓存结果", ["outcome"])
ORIGIN = Histogram("commerce_catalog_origin_seconds", "目录回源耗时")
LOGGER = logging.getLogger(__name__)
# 租约大于回源超时；发布和释放都验证所有权，防止慢刷新覆盖新值。
PUBLISH = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
redis.call('SET', KEYS[2], ARGV[2], 'EX', ARGV[3])
redis.call('DEL', KEYS[1])
return 1
"""
RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end
return 0
"""


class CachedPage(BaseModel):
    fresh_until: float
    stale_until: float
    page: ProductPage


async def read_page(r: Resources, key: str) -> CachedPage | None:
    value = await r.redis.get(key)
    if not value:
        return None
    try:
        entry = CachedPage.model_validate_json(value)
    except ValidationError:
        CACHE.labels("invalid").inc()
        return None
    return entry if time.time() < entry.stale_until else None


async def load_page(
    r: Resources, key: str, token: str | None, load: Callable[[], Awaitable[ProductPage]]
) -> tuple[ProductPage, bool]:
    # 从读取数据库前计龄，慢查询不能给早期快照增加额外的新鲜时间。
    started = time.time()
    try:
        with ORIGIN.time():
            async with asyncio.timeout(15):
                result = await load()
    except TimeoutError as exc:
        CACHE.labels("origin_timeout").inc()
        raise CommerceError(
            "CATALOG_TIMEOUT", "目录查询超时，请稍后重试。", 503, True, retry_after=1
        ) from exc
    published = False
    if token is not None:
        fresh_seconds = r.settings.cache_seconds + random.randint(0, 5)
        entry = CachedPage(
            fresh_until=started + fresh_seconds,
            stale_until=started + fresh_seconds + r.settings.cache_stale_seconds,
            page=result,
        )
        try:
            pending = r.redis.eval(
                PUBLISH,
                2,
                f"{key}:refresh",
                key,
                token,
                entry.model_dump_json(),
                str(fresh_seconds + r.settings.cache_stale_seconds),
            )
            reply = await pending if isinstance(pending, Awaitable) else pending
            published = int(reply) == 1
            if not published:
                CACHE.labels("lease_lost").inc()
        except RedisError:
            CACHE.labels("unavailable").inc()
            CACHE.labels("publish_failed").inc()
    return result, published


async def refresh_page(r: Resources, key: str, load: Callable[[], Awaitable[ProductPage]]) -> None:
    token = uuid4().hex
    acquired = False
    release = False
    # 冷却期间保留任务记录，不占回源槽位，也不无限增加按 key 存储的状态。
    cooldown = random.uniform(1, 2)
    try:
        # 不建立等待队列；冷回源和后台刷新共享预算。
        if r.origin_slots.locked():
            CACHE.labels("refresh_busy").inc()
            return
        async with r.origin_slots:
            acquired = bool(await r.redis.set(f"{key}:refresh", token, nx=True, ex=20))
            if not acquired:
                return
            entry = await read_page(r, key)
            if entry is not None and time.time() < entry.fresh_until:
                release = True
                cooldown = 0
                return
            _, published = await load_page(r, key, token, load)
            if published:
                release = True
                cooldown = 0
                CACHE.labels("refreshed").inc()
            else:
                cooldown = random.uniform(2, 5)
                CACHE.labels("refresh_failed").inc()
    except Exception:
        cooldown = random.uniform(2, 5)
        CACHE.labels("refresh_failed").inc()
        LOGGER.exception("Catalog background refresh failed")
    except asyncio.CancelledError:
        release = True
        cooldown = 0
        raise
    finally:
        try:
            try:
                # 失败时保留剩余租约（最多 20 秒），阻止其他实例立即再次回源。
                if acquired and release:
                    pending = r.redis.eval(RELEASE, 1, f"{key}:refresh", token)
                    if isinstance(pending, Awaitable):
                        await pending
            except RedisError:
                pass
            if cooldown:
                await asyncio.sleep(cooldown)
        finally:
            r.cache_refreshes.pop(key, None)


async def cached_catalog(
    r: Resources, key: str, load: Callable[[], Awaitable[ProductPage]]
) -> ProductPage:
    available = True
    try:
        generation = await r.redis.get("catalog:generation") or "0"
        key = f"{key}:logical-v1:g{generation}"
        entry = await read_page(r, key)
        if entry is not None:
            if time.time() < entry.fresh_until:
                CACHE.labels("hit").inc()
            else:
                CACHE.labels("stale").inc()
                if (
                    key not in r.cache_refreshes
                    and len(r.cache_refreshes) < r.settings.cache_origin_concurrency
                ):
                    r.cache_refreshes[key] = asyncio.create_task(refresh_page(r, key, load))
            return entry.page
    except RedisError:
        available = False
        CACHE.labels("unavailable").inc()
    CACHE.labels("miss").inc()
    try:
        async with asyncio.timeout(0.25):
            await r.origin_slots.acquire()
    except TimeoutError as exc:
        CACHE.labels("busy").inc()
        raise CommerceError("CATALOG_BUSY", "目录繁忙，请稍后重试。", 503, True) from exc
    token = uuid4().hex
    acquired = False
    try:
        if available:
            try:
                # 只有持有回源名额的请求轮询；其他请求受入口和本地预算限制。
                async with asyncio.timeout(0.25):
                    while True:
                        entry = await read_page(r, key)
                        if entry is not None:
                            CACHE.labels("coalesced").inc()
                            return entry.page
                        acquired = bool(await r.redis.set(f"{key}:refresh", token, nx=True, ex=20))
                        if acquired:
                            # 上一刷新者可能在读缓存与抢锁之间发布，必须再检查。
                            entry = await read_page(r, key)
                            if entry is not None:
                                CACHE.labels("coalesced").inc()
                                return entry.page
                            break
                        await asyncio.sleep(0.025)
            except RedisError:
                CACHE.labels("unavailable").inc()
            except TimeoutError as exc:
                CACHE.labels("busy").inc()
                raise CommerceError("CATALOG_BUSY", "目录繁忙，请稍后重试。", 503, True) from exc
        page, _ = await load_page(r, key, token if acquired else None, load)
        return page
    finally:
        try:
            if acquired:
                pending = r.redis.eval(RELEASE, 1, f"{key}:refresh", token)
                if isinstance(pending, Awaitable):
                    await pending
        except RedisError:
            pass
        finally:
            r.origin_slots.release()
