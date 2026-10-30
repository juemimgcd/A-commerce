"""仅供公开目录的筛选/分页缓存回源使用；交易路径始终使用主库会话。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg  # type: ignore[import-untyped] # 驱动连接阶段的异常尚未包装为 SQLAlchemy 异常。
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.ext.asyncio import AsyncSession

from a_commerce.conf.resources import Resources
from a_commerce.errors import CommerceError


@asynccontextmanager
async def catalog_read_session(r: Resources) -> AsyncIterator[AsyncSession]:
    if r.catalog_read_sessions is None:
        # 仅未启用读写分离的单机模式使用主库，不是副本故障回退。
        async with r.sessions() as session:
            yield session
        return
    try:
        async with r.catalog_read_sessions.begin() as session:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            # 连接池可能复用已晋升节点的连接，不能只在建立连接时验证角色。
            if not await session.scalar(text("SELECT pg_is_in_recovery()")):
                await session.invalidate()
                raise CommerceError(
                    "CATALOG_READ_UNAVAILABLE",
                    "商品查询副本暂不可用，请稍后重试。",
                    503,
                    True,
                    retry_after=1,
                )
            yield session
    except (
        DBAPIError,
        PoolTimeout,
        OSError,
        TimeoutError,
        asyncpg.PostgresError,
        asyncpg.TargetServerAttributeNotMatched,
    ) as exc:
        raise CommerceError(
            "CATALOG_READ_UNAVAILABLE",
            "商品查询副本暂不可用，请稍后重试。",
            503,
            True,
            retry_after=1,
        ) from exc
