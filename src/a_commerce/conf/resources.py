"""进程生命周期内复用资源；网络等待和数据库等待都有明确上限。"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from a_commerce.conf.settings import Settings


@dataclass
class Resources:
    settings: Settings
    sessions: async_sessionmaker[AsyncSession]
    redis: Redis
    http: httpx.AsyncClient
    password_slots: asyncio.Semaphore
    origin_slots: asyncio.Semaphore


@asynccontextmanager
async def resources(settings: Settings) -> AsyncIterator[Resources]:
    engine = create_async_engine(
        settings.database_url.get_secret_value(),
        pool_size=settings.db_pool_size,
        max_overflow=0,
        pool_timeout=settings.db_pool_timeout,
        pool_pre_ping=True,
        connect_args={
            "server_settings": {
                "statement_timeout": "5000",
                "lock_timeout": "1500",
                "idle_in_transaction_session_timeout": "10000",
            }
        },
    )
    redis = Redis.from_url(
        settings.redis_url.get_secret_value(),
        max_connections=settings.redis_connections,
        socket_connect_timeout=1,
        socket_timeout=1,
        decode_responses=True,
    )
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(10, connect=2, pool=2),
            limits=httpx.Limits(
                max_connections=settings.http_connections,
                max_keepalive_connections=settings.http_connections,
            ),
            follow_redirects=False,
        ) as http:
            yield Resources(
                settings,
                async_sessionmaker(engine, expire_on_commit=False),
                redis,
                http,
                asyncio.Semaphore(settings.password_concurrency),
                asyncio.Semaphore(settings.cache_origin_concurrency),
            )
    finally:
        await redis.aclose()
        await engine.dispose()
