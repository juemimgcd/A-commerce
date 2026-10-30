"""进程生命周期内复用资源；网络等待和数据库等待都有明确上限。"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import httpx
from prometheus_client import start_http_server
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from a_commerce.conf.database import database_engine
from a_commerce.conf.redis import redis_client
from a_commerce.conf.settings import Settings
from a_commerce.observability.runtime import MeasuredPool, RuntimeMetrics, instrument


@dataclass
class Resources:
    settings: Settings
    sessions: async_sessionmaker[AsyncSession]
    redis: Redis
    rate_redis: Redis
    http: httpx.AsyncClient
    password_slots: asyncio.Semaphore
    origin_slots: asyncio.Semaphore
    engine: AsyncEngine
    catalog_generation_batch: asyncio.Task[str] | None = None
    catalog_reads: set[asyncio.Task[str]] = field(default_factory=set)
    cache_refreshes: dict[str, asyncio.Task[None]] = field(default_factory=dict)


@asynccontextmanager
async def resources(settings: Settings) -> AsyncIterator[Resources]:
    engine = database_engine(
        settings.database_url.get_secret_value(),
        poolclass=MeasuredPool,
        pool_size=settings.db_pool_size,
        max_overflow=0,
        pool_timeout=settings.db_pool_timeout,
        pool_pre_ping=True,
        server_settings={
            "statement_timeout": "5000",
            "lock_timeout": "1500",
            "idle_in_transaction_session_timeout": "10000",
        },
    )
    instrument(engine)
    metrics_server = None
    metrics_thread = None
    try:
        if settings.metrics_port:
            metrics_server, metrics_thread = start_http_server(
                settings.metrics_port, registry=RuntimeMetrics(engine)
            )
        async with (
            redis_client(
                settings.redis_url,
                settings.redis_sentinel,
                rate=False,
                max_connections=settings.redis_connections,
            ) as redis,
            redis_client(
                settings.rate_redis_url,
                settings.redis_sentinel,
                rate=True,
                max_connections=settings.redis_connections,
            ) as rate_redis,
            httpx.AsyncClient(
                timeout=httpx.Timeout(10, connect=2, pool=2),
                limits=httpx.Limits(
                    max_connections=settings.http_connections,
                    max_keepalive_connections=settings.http_connections,
                ),
                follow_redirects=False,
            ) as http,
        ):
            shared = Resources(
                settings,
                async_sessionmaker(engine, expire_on_commit=False),
                redis,
                rate_redis,
                http,
                asyncio.Semaphore(settings.password_concurrency),
                asyncio.Semaphore(settings.cache_origin_concurrency),
                engine,
            )
            try:
                yield shared
            finally:
                tasks = [*shared.cache_refreshes.values(), *shared.catalog_reads]
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        if metrics_server is not None and metrics_thread is not None:
            await asyncio.to_thread(metrics_server.shutdown)
            metrics_server.server_close()
            await asyncio.to_thread(metrics_thread.join, 5)
        await engine.dispose()
