"""多地址连接逐个限时，避免一个无响应节点耗尽整个 asyncpg 连接预算。"""

import asyncpg  # type: ignore[import-untyped] # 驱动没有发布类型声明。
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from a_commerce.errors import CommerceError


def database_engine(
    database_url: str, *, server_settings: dict[str, str] | None = None, **options: object
) -> AsyncEngine:
    url = make_url(database_url)
    hosts = url.normalized_query.get("host", ())
    if len(hosts) < 2:
        return create_async_engine(
            url, connect_args={"timeout": 5, "server_settings": server_settings}, **options
        )
    # 只拆分 SQLAlchemy 的重复 host 参数；URI 的认证、TLS 和目标角色参数继续由驱动处理。
    candidates = []
    for host in hosts:
        address = make_url("postgresql://" + host)
        if not address.host or address.port is None or address.username or address.database:
            raise ValueError("multi-host database URL requires host:port entries")
        candidates.append(
            url.difference_update_query(["host"])
            .set(drivername="postgresql", host=address.host, port=address.port)
            .render_as_string(hide_password=False)
        )
    preferred = 0

    async def connect() -> object:
        nonlocal preferred
        # 缓存的是优先尝试顺序，目标角色每次仍由 asyncpg read-write 检查。
        for index in [preferred, *(i for i in range(len(candidates)) if i != preferred)]:
            try:
                connection: object = await asyncpg.connect(
                    candidates[index], timeout=2, server_settings=server_settings
                )
            except (
                OSError,
                TimeoutError,
                asyncpg.PostgresConnectionError,
                asyncpg.CannotConnectNowError,
                asyncpg.TargetServerAttributeNotMatched,
            ):
                continue
            preferred = index
            return connection
        raise CommerceError("DATABASE_BUSY", "数据库暂不可用，请查询已有结果后重试。", 503, True)

    # 使用 SQLAlchemy 公共 async_creator 接口，不修改驱动内部实现，也不重放业务事务。
    return create_async_engine(url, async_creator=connect, **options)
