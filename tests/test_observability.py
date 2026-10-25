"""运行中的 HTTP/后台指标、连接池超时与 SQL 错误有真实观测值。"""

import socket

import httpx
import pytest
from prometheus_client import REGISTRY
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeout

from a_commerce.conf.resources import resources
from tests.conftest import Backend

pytestmark = pytest.mark.integration


async def test_api_metrics_are_valid_and_have_no_sensitive_labels(backend: Backend) -> None:
    for _ in range(2):
        response = await backend.client.get("/api/v1/customer/products")
        assert response.status_code == 200
    response = await backend.client.get("/metrics")
    assert response.status_code == 200
    for name in (
        "commerce_db_query_seconds_count",
        "commerce_db_acquire_seconds_count",
        "commerce_db_connections_used",
        "commerce_db_pool_size",
        "commerce_background_alive",
        "commerce_catalog_cache_total",
    ):
        assert name in response.text
    assert backend.buyer.token not in response.text
    assert str(backend.sku_ids[0]) not in response.text
    assert "SELECT " not in response.text


async def test_pool_saturation_and_sql_errors_are_counted(backend: Backend) -> None:
    settings = backend.r.settings.model_copy(update={"db_pool_size": 1, "db_pool_timeout": 0.1})
    before = REGISTRY.get_sample_value("commerce_db_pool_timeouts_total") or 0
    errors = REGISTRY.get_sample_value("commerce_db_errors_total") or 0
    async with resources(settings) as r:
        async with r.engine.connect():
            with pytest.raises(PoolTimeout):
                async with r.engine.connect():
                    pytest.fail("pool must remain bounded")
        async with r.engine.connect() as connection:
            with pytest.raises(DBAPIError):
                await connection.execute(text("SELECT 1 / 0"))
    assert REGISTRY.get_sample_value("commerce_db_pool_timeouts_total") == before + 1
    assert REGISTRY.get_sample_value("commerce_db_errors_total") == errors + 1


async def test_background_metrics_endpoint_can_start_and_stop(backend: Backend) -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    settings = backend.r.settings.model_copy(update={"metrics_port": port})
    async with resources(settings):
        async with httpx.AsyncClient() as client:
            response = await client.get(f"http://127.0.0.1:{port}/metrics")
            assert response.status_code == 200
            assert "commerce_db_pool_size 5.0" in response.text
    # 同一端口可重新绑定，验证退出时关闭指标线程与监听 socket。
    async with resources(settings):
        pass
