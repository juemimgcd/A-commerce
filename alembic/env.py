"""迁移使用独立连接，不借用 API 连接池，也不在每个 API 副本启动时运行。"""

import asyncio

from sqlalchemy import Connection, pool

from a_commerce.conf.database import database_engine
from a_commerce.conf.settings import Settings
from a_commerce.models import entities, merchant_ops, support  # noqa: F401
from a_commerce.models.base import Base
from alembic import context


def migrate(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def online() -> None:
    engine = database_engine(
        Settings().database_url.get_secret_value(),
        poolclass=pool.NullPool,
    )
    async with engine.connect() as connection:
        await connection.run_sync(migrate)
    await engine.dispose()


if context.is_offline_mode():
    context.configure(
        url=Settings().database_url.get_secret_value(),
        target_metadata=Base.metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(online())
