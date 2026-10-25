"""真实 Redis 上验证原子额度、跨实例共享和依赖中断时的读写策略。"""

import asyncio
import subprocess
from dataclasses import replace

import pytest
from redis.asyncio import Redis

from a_commerce.cache.catalog import cached_catalog
from a_commerce.conf.resources import resources
from a_commerce.conf.traffic import Quota
from a_commerce.errors import CommerceError
from a_commerce.schemas.api import ProductPage
from a_commerce.traffic import Limit, enforce
from tests.conftest import Backend

pytestmark = pytest.mark.integration


async def test_redis_process_outage_preserves_queries_and_signed_money_callbacks(
    backend: Backend, isolated_compose: list[str]
) -> None:
    order = await backend.order()
    intent = await backend.intent(order)
    await backend.dispatch("payment", intent)
    try:
        await asyncio.to_thread(subprocess.run, [*isolated_compose, "stop", "redis"], check=True)
        query = await backend.client.get("/api/v1/customer/products")
        assert query.status_code == 200
        rejected = await backend.client.post(
            "/api/v1/customer/quotes",
            headers=backend.buyer.headers,
            json={
                "address_id": str(backend.buyer.address_id),
                "items": [{"sku_id": str(backend.sku_ids[0]), "quantity": 1}],
            },
        )
        assert rejected.status_code == 503
        assert rejected.json()["error"]["code"] == "RATE_LIMIT_UNAVAILABLE"
        assert rejected.headers["Retry-After"] == "1"
        paid = await backend.provider.post(f"/payments/{intent}/pay")
        assert paid.status_code == 200
        assert paid.headers.get("X-Callback-Delivered") != "false"
        assert (await backend.get(order))["status"] == "paid"
    finally:
        await asyncio.to_thread(
            subprocess.run,
            [*isolated_compose, "up", "-d", "--no-deps", "--no-recreate", "--wait", "redis"],
            check=True,
        )


async def test_shared_user_budget_across_two_resource_pools(backend: Backend) -> None:
    quota = Quota(rate=0.001, burst=20)
    async with resources(backend.r.settings) as second:

        async def request(index: int) -> str:
            try:
                await enforce(
                    backend.r if index % 2 else second, [Limit("shared_test", "user", quota)]
                )
                return "allowed"
            except CommerceError as exc:
                assert exc.code == "RATE_LIMITED"
                assert exc.retry_after >= 1
                return "rejected"

        results = await asyncio.gather(*(request(index) for index in range(32)))
    assert results.count("allowed") == 20
    assert results.count("rejected") == 12


async def test_multi_sku_rejection_does_not_charge_other_buckets(backend: Backend) -> None:
    quota = Quota(rate=0.001, burst=1)
    await enforce(backend.r, [Limit("atomic", "sku-a", quota)])
    with pytest.raises(CommerceError) as error:
        await enforce(
            backend.r, [Limit("atomic", name, quota) for name in ("global", "sku-a", "sku-b")]
        )
    assert error.value.code == "RATE_LIMITED"
    await enforce(backend.r, [Limit("atomic", "global", quota), Limit("atomic", "sku-b", quota)])


async def test_rate_redis_outage_fails_closed_for_writes_only(backend: Backend) -> None:
    unavailable = Redis.from_url(
        "redis://127.0.0.1:1/0",
        socket_connect_timeout=0.05,
        socket_timeout=0.05,
        decode_responses=True,
    )
    try:
        broken = replace(backend.r, rate_redis=unavailable)
        limits = [Limit("outage", "user", Quota(rate=1, burst=1))]
        await enforce(broken, limits, fail_open=True)
        with pytest.raises(CommerceError) as error:
            await enforce(broken, limits)
        assert error.value.code == "RATE_LIMIT_UNAVAILABLE"
        assert error.value.retry_after == 1
    finally:
        await unavailable.aclose()


async def test_catalog_cache_hit_and_bounded_fallback(backend: Backend) -> None:
    calls = 0

    async def load() -> ProductPage:
        nonlocal calls
        calls += 1
        return ProductPage(items=[])

    for _ in range(2):
        await cached_catalog(backend.r, "catalog-test", load)
    assert calls == 1
    unavailable = Redis.from_url(
        "redis://127.0.0.1:1/0",
        socket_connect_timeout=0.05,
        socket_timeout=0.05,
        decode_responses=True,
    )
    try:
        broken = replace(backend.r, redis=unavailable, origin_slots=asyncio.Semaphore(1))
        await cached_catalog(broken, "catalog-test", load)
        assert calls == 2
        await broken.origin_slots.acquire()
        try:
            with pytest.raises(CommerceError) as error:
                await cached_catalog(broken, "catalog-test", load)
            assert error.value.code == "CATALOG_BUSY"
        finally:
            broken.origin_slots.release()
    finally:
        await unavailable.aclose()
