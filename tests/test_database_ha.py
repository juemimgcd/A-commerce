"""真实 TCP 无响应节点不能阻止客户端尝试后续 PostgreSQL 地址。"""

import asyncio
import time

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from a_commerce.conf.database import database_engine

from .conftest import Backend


async def test_unresponsive_first_host_does_not_starve_live_database(backend: Backend) -> None:
    writers: set[asyncio.StreamWriter] = set()
    tasks: set[asyncio.Task] = set()
    attempts = 0

    async def silent(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal attempts
        attempts += 1
        writers.add(writer)
        task = asyncio.current_task()
        assert task
        tasks.add(task)
        try:
            while await reader.read(4096):
                pass
        finally:
            writer.close()
            await writer.wait_closed()
            writers.discard(writer)
            tasks.discard(task)

    server = await asyncio.start_server(silent, "127.0.0.1", 0)
    url = make_url(backend.r.settings.database_url.get_secret_value())
    port = server.sockets[0].getsockname()[1]
    multi = url.set(host=None, port=None).update_query_dict(
        {
            "host": (f"127.0.0.1:{port}", f"{url.host}:{url.port}"),
            "target_session_attrs": "read-write",
        }
    )
    engine = database_engine(multi.render_as_string(hide_password=False), poolclass=NullPool)
    try:
        started = time.monotonic()
        async with asyncio.timeout(5), engine.connect() as connection:
            assert await connection.scalar(text("SELECT 42")) == 42
        assert time.monotonic() - started >= 1.5
        assert attempts == 1
        # 真正新建第二条连接；优先尝试上次成功的地址，仍检查它是否可写。
        async with asyncio.timeout(1.5), engine.connect() as connection:
            assert await connection.scalar(text("SELECT pg_is_in_recovery()")) is False
        assert attempts == 1
    finally:
        await engine.dispose()
        server.close()
        await server.wait_closed()
        for writer in list(writers):
            writer.close()
        await asyncio.gather(*tasks)
