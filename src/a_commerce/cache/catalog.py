"""公开目录逻辑过期与有界回源；缓存永不参与报价、库存和授权判断。"""

import asyncio
import hashlib
import logging
import random
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from uuid import uuid4

from prometheus_client import Counter, Histogram
from pydantic import BaseModel, ValidationError
from redis.exceptions import NoScriptError, RedisError

from a_commerce.conf.resources import Resources
from a_commerce.errors import CommerceError
from a_commerce.schemas.api import ProductPage

CACHE = Counter("commerce_catalog_cache_total", "目录缓存结果", ["outcome"])
ORIGIN = Histogram("commerce_catalog_origin_seconds", "目录回源耗时")
LOGGER = logging.getLogger(__name__)
# HTTP 响应复用只能继承数据快照的截止时间，不能重新计龄延长旧数据寿命。
CATALOG_DEADLINE: ContextVar[float | None] = ContextVar("catalog_deadline", default=None)
# Sentinel 主节点上原子读取版本及对应快照，避免每次命中两次网络往返。
# 此目录缓存使用单 Redis 复制组；动态版本 key 不适用于 Redis Cluster 分片。
READ = """
local generation = redis.call('GET', KEYS[1]) or '0'
local key = KEYS[2] .. ':logical-v1:g' .. generation
return {key, redis.call('GET', key)}
"""
READ_SHA = hashlib.sha1(READ.encode(), usedforsecurity=False).hexdigest()
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


async def read_generation(r: Resources) -> str:
    """只合并尚未发出的读取，不复用已开始或已完成的版本查询。"""
    task = r.catalog_generation_batch
    if task is None:
        # 请求取消后共享读取仍可能在途；限制后台任务数量，沿用连接池等待预算。
        if len(r.catalog_reads) >= r.settings.redis_connections:
            return str(await r.redis.get("catalog:generation") or "0")

        async def fetch() -> str:
            # 从此刻起到达的请求必须进入下一批，不能加入已经开始的 Redis 查询。
            r.catalog_generation_batch = None
            return str(await r.redis.get("catalog:generation") or "0")

        task = asyncio.create_task(fetch())
        r.catalog_generation_batch = task
        r.catalog_reads.add(task)

        def finished(done: asyncio.Task[str]) -> None:
            r.catalog_reads.discard(done)
            # 即使所有等待者已取消，也取回异常，避免后台任务异常无人处理。
            if not done.cancelled():
                done.exception()

        task.add_done_callback(finished)
    # 某个 HTTP 请求取消，不能取消同一批其他请求仍需使用的读取。
    return await asyncio.shield(task)


async def read_snapshot(r: Resources, key: str) -> tuple[str, str | None]:
    try:
        pending = r.redis.evalsha(READ_SHA, 2, "catalog:generation", key)
        current = await pending if isinstance(pending, Awaitable) else pending
    except NoScriptError:
        # NOSCRIPT 表示命令尚未执行；允许在首次请求或切主后安装脚本。
        pending = r.redis.eval(READ, 2, "catalog:generation", key)
        current = await pending if isinstance(pending, Awaitable) else pending
    return current[0], current[1]


async def read_page(r: Resources, key: str) -> CachedPage | None:
    value = await r.redis.get(key)
    return parse_page(value)


def parse_page(value: str | None) -> CachedPage | None:
    if not value:
        return None
    try:
        entry = CachedPage.model_validate_json(value)
    except ValidationError:
        CACHE.labels("invalid").inc()
        return None
    return entry if time.time() < entry.stale_until else None


async def load_page(
    r: Resources,
    key: str,
    token: str | None,
    load: Callable[[], Awaitable[ProductPage]],
    *,
    query: bool = False,
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
        fresh_seconds = (
            r.settings.cache_query_seconds if query else r.settings.cache_seconds
        ) + random.randint(0, 5)
        stale_seconds = 0 if query else r.settings.cache_stale_seconds
        entry = CachedPage(
            fresh_until=started + fresh_seconds,
            stale_until=started + fresh_seconds + stale_seconds,
            page=result,
        )
        CATALOG_DEADLINE.set(entry.stale_until)
        try:
            pending = r.redis.eval(
                PUBLISH,
                2,
                f"{key}:refresh",
                key,
                token,
                entry.model_dump_json(),
                str(fresh_seconds + stale_seconds),
            )
            reply = await pending if isinstance(pending, Awaitable) else pending
            published = int(reply) == 1
            if not published:
                CACHE.labels("lease_lost").inc()
        except RedisError:
            CACHE.labels("unavailable").inc()
            CACHE.labels("publish_failed").inc()
    return result, published


async def refresh_page(
    r: Resources,
    key: str,
    load: Callable[[], Awaitable[ProductPage]],
    *,
    query: bool = False,
) -> None:
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
            refresh_ahead = min(3, r.settings.cache_query_seconds / 2) if query else 0
            if entry is not None and time.time() < entry.fresh_until - refresh_ahead:
                release = True
                cooldown = 0
                return
            _, published = await load_page(r, key, token, load, query=query)
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
    r: Resources,
    key: str,
    load: Callable[[], Awaitable[ProductPage]],
    *,
    query: bool = False,
) -> ProductPage:
    available = True
    try:
        key, value = await read_snapshot(r, key)
        entry = parse_page(value)
        if entry is not None:
            CATALOG_DEADLINE.set(entry.stale_until)
            now = time.time()
            if now < entry.fresh_until:
                CACHE.labels("hit").inc()
            else:
                CACHE.labels("stale").inc()
            # 查询不延长原有期限；在最后几秒用同一租约和回源预算提前刷新，
            # 减少热点查询到期时所有请求同时等待数据库的尖峰。
            refresh_ahead = min(3, r.settings.cache_query_seconds / 2) if query else 0
            if now >= entry.fresh_until - refresh_ahead:
                if (
                    key not in r.cache_refreshes
                    and len(r.cache_refreshes) < r.settings.cache_origin_concurrency
                ):
                    r.cache_refreshes[key] = asyncio.create_task(
                        refresh_page(r, key, load, query=query)
                    )
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
                            CATALOG_DEADLINE.set(entry.stale_until)
                            CACHE.labels("coalesced").inc()
                            return entry.page
                        acquired = bool(await r.redis.set(f"{key}:refresh", token, nx=True, ex=20))
                        if acquired:
                            # 上一刷新者可能在读缓存与抢锁之间发布，必须再检查。
                            entry = await read_page(r, key)
                            if entry is not None:
                                CATALOG_DEADLINE.set(entry.stale_until)
                                CACHE.labels("coalesced").inc()
                                return entry.page
                            break
                        await asyncio.sleep(0.025)
            except RedisError:
                CACHE.labels("unavailable").inc()
            except TimeoutError as exc:
                CACHE.labels("busy").inc()
                raise CommerceError("CATALOG_BUSY", "目录繁忙，请稍后重试。", 503, True) from exc
        page, _ = await load_page(r, key, token if acquired else None, load, query=query)
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
