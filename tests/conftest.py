"""真实 PostgreSQL/Redis + 本机 TCP API/provider。破坏性清理只允许一次性测试库。"""

import asyncio
import hashlib
import os
import secrets
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import aio_pika
import httpx
import pytest
import pytest_asyncio
import uvicorn
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.engine import make_url

from a_commerce.conf.resources import Resources, resources
from a_commerce.conf.settings import Settings
from a_commerce.integrations.payments.simulator import create_app as provider_app
from a_commerce.main import create_app
from a_commerce.messaging.envelope import Envelope
from a_commerce.messaging.topology import declare
from a_commerce.models.base import Base, now
from a_commerce.models.entities import (
    SKU,
    Address,
    Category,
    Operation,
    Outbox,
    PaymentIntent,
    Product,
    Session,
    Staff,
    Stock,
    User,
)
from a_commerce.services.identity import DUMMY_HASH, Principal
from a_commerce.workers.handlers import handle


@pytest.fixture
def isolated_compose() -> list[str]:
    project = os.environ["COMMERCE_TEST_PROJECT"]
    assert project.startswith("commerce-test-")
    return [
        "docker",
        "compose",
        "--project-name",
        project,
        "--env-file",
        os.environ["COMMERCE_TEST_ENV_FILE"],
        "-f",
        str(Path(__file__).resolve().parents[1] / "deploy/compose.test.yaml"),
    ]


@asynccontextmanager
async def serve(app: FastAPI) -> AsyncIterator[str]:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(10):
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError("test HTTP server exited before startup")
                await asyncio.sleep(0.01)
        yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    finally:
        server.should_exit = True
        try:
            async with asyncio.timeout(10):
                await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            sock.close()


@dataclass
class Buyer:
    principal: Principal
    token: str
    address_id: UUID

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


@dataclass
class Backend:
    r: Resources
    client: httpx.AsyncClient
    provider: httpx.AsyncClient
    buyer: Buyer
    other: Buyer
    admin: Buyer
    sku_ids: list[UUID]
    product_id: UUID

    async def quote(self, buyer: Buyer | None = None, *, quantities: tuple[int, ...] = (1,)) -> str:
        buyer = buyer or self.buyer
        response = await self.client.post(
            "/api/v1/customer/quotes",
            headers=buyer.headers,
            json={
                "address_id": str(buyer.address_id),
                "items": [
                    {"sku_id": str(sku), "quantity": quantity}
                    for sku, quantity in zip(self.sku_ids, quantities, strict=False)
                ],
            },
        )
        assert response.status_code == 201, response.text
        return str(response.json()["id"])

    async def order(self, buyer: Buyer | None = None, *, quantity: int = 1) -> dict:
        buyer = buyer or self.buyer
        quote = await self.quote(buyer, quantities=(quantity,))
        response = await self.place(quote, buyer=buyer)
        assert response.status_code == 201, response.text
        return response.json()

    async def place(
        self, quote: str, *, key: str | None = None, buyer: Buyer | None = None
    ) -> httpx.Response:
        return await self.client.post(
            "/api/v1/customer/orders",
            json={"quote_id": quote},
            headers={
                **(buyer or self.buyer).headers,
                "Idempotency-Key": key or secrets.token_hex(16),
            },
        )

    async def get(self, order: dict) -> dict:
        response = await self.client.get(
            f"/api/v1/customer/orders/{order['id']}", headers=self.buyer.headers
        )
        assert response.status_code == 200, response.text
        return response.json()

    async def dispatch(self, kind: str, resource_id: UUID) -> Envelope:
        async with self.r.sessions() as session:
            operation = (
                await session.scalars(
                    select(Operation).where(
                        Operation.kind == kind, Operation.resource_id == resource_id
                    )
                )
            ).one()
            outbox = (
                await session.scalars(
                    select(Outbox).where(
                        Outbox.operation_id == operation.id,
                        Outbox.dispatch_version == operation.dispatch_version,
                    )
                )
            ).one()
            message = Envelope(
                message_id=outbox.id,
                operation_id=operation.id,
                operation_type=kind,
                dispatch_version=operation.dispatch_version,
            )
        await handle(self.r, message)
        return message

    async def intent(self, order: dict) -> UUID:
        async with self.r.sessions() as session:
            return (
                await session.scalars(
                    select(PaymentIntent.id).where(PaymentIntent.order_id == UUID(order["id"]))
                )
            ).one()

    async def pay(self, order: dict) -> dict:
        intent = await self.intent(order)
        await self.dispatch("payment", intent)
        response = await self.provider.post(f"/payments/{intent}/pay")
        assert response.status_code == 200, response.text
        assert response.headers.get("X-Callback-Delivered") != "false"
        return await self.get(order)


@pytest_asyncio.fixture
async def backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Backend]:
    # 先校验目标，再 TRUNCATE/FLUSHDB。CI 也必须显式选择一次性 commerce_test 库。
    url = os.environ.get("COMMERCE_DATABASE_URL", "")
    if os.environ.get("COMMERCE_TEST_ISOLATED") != "1" or make_url(url).database != "commerce_test":
        pytest.fail("Use uv run python scripts/test_backend.py; refusing non-test database")
    for name in list(os.environ):
        if name.startswith("COMMERCE_") and name not in {
            "COMMERCE_DATABASE_URL",
            "COMMERCE_REDIS_URL",
            "COMMERCE_RATE_REDIS_URL",
            "COMMERCE_AMQP_URL",
            "COMMERCE_TEST_ISOLATED",
            "COMMERCE_TEST_PROJECT",
            "COMMERCE_TEST_ENV_FILE",
        }:
            monkeypatch.delenv(name)
    values = {
        "environment": "development",
        "provider_key": secrets.token_hex(32),
        "callback_secret": secrets.token_hex(32),
        "files_directory": str(tmp_path / "files"),
        "mail_enabled": False,
        "storage_backend": "local",
        "api_role": "all",
        "reconciliation_enabled": False,
    }
    defaults = Settings(_env_file=None, **values)
    traffic = {
        name: {"rate": 10000, "burst": 10000} for name in type(defaults.traffic).model_fields
    }
    values["traffic"] = traffic
    settings = Settings(_env_file=None, **values)
    # 把完整测试配置传递给 HTTP 工厂与真实 worker 子进程，覆盖工作目录中的业务 .env。
    for key in type(settings).model_fields:
        value = getattr(settings, key)
        if value is None:
            # .env 中的可选值不会生效：测试脚本在临时 cwd 运行子进程。
            continue
        serialized = (
            value.get_secret_value()
            if isinstance(value, SecretStr)
            else (value.model_dump_json() if key in {"traffic", "redis_sentinel"} else str(value))
        )
        monkeypatch.setenv(f"COMMERCE_{key.upper()}", serialized)
    monkeypatch.setenv("SIMULATOR_DATABASE_PATH", str(tmp_path / "provider.sqlite3"))
    monkeypatch.setenv("SIMULATOR_API_KEY", settings.provider_key.get_secret_value())
    monkeypatch.setenv("SIMULATOR_CALLBACK_SECRET", settings.callback_secret.get_secret_value())
    async with resources(settings) as r:
        async with r.sessions.begin() as session:
            tables = ", ".join(f'"{name}"' for name in Base.metadata.tables)
            await session.execute(text(f"TRUNCATE {tables} CASCADE"))
        await r.redis.flushdb()
        await r.rate_redis.flushdb()
        connection = await aio_pika.connect_robust(settings.amqp_url.get_secret_value())
        async with connection:
            channel = await connection.channel()
            _, queues = await declare(channel)
            for queue in queues.values():
                await queue.purge()

        async def account(*, staff: bool = False) -> Buyer:
            token = secrets.token_hex(24)
            async with r.sessions.begin() as session:
                user = (
                    Staff(
                        email=f"{token}@example.com",
                        password_hash=DUMMY_HASH,
                        permissions=["catalog", "fulfillment", "aftersales", "audit", "identity"],
                    )
                    if staff
                    else User(email=f"{token}@example.com", password_hash=DUMMY_HASH)
                )
                session.add(user)
                await session.flush()
                auth = Session(
                    token_hash=hashlib.sha256(token.encode()).hexdigest(),
                    user_id=None if staff else user.id,
                    staff_id=user.id if staff else None,
                    expires_at=now() + timedelta(hours=1),
                )
                session.add(auth)
                address = (
                    Address(
                        user_id=user.id,
                        recipient="Test",
                        phone="12345678",
                        region="测试区域",
                        detail="测试地址",
                    )
                    if not staff
                    else None
                )
                if address:
                    session.add(address)
                await session.flush()
                return Buyer(
                    Principal(user.id, auth.id, "staff" if staff else "customer"),
                    token,
                    address.id if address else user.id,
                )

        buyer, other, admin = await account(), await account(), await account(staff=True)
        async with r.sessions.begin() as session:
            category = Category(name="测试分类")
            session.add(category)
            await session.flush()
            product = Product(category_id=category.id, title="测试商品", active=True)
            session.add(product)
            await session.flush()
            skus = [
                SKU(product_id=product.id, code=f"TEST-{index}", attributes={}, price_fen=2500)
                for index in range(2)
            ]
            session.add_all(skus)
            await session.flush()
            session.add_all([Stock(sku_id=sku.id, on_hand=10) for sku in skus])
        app = create_app(settings.model_copy(deep=True))
        async with serve(app) as api_url:
            monkeypatch.setenv(
                "SIMULATOR_CALLBACK_URL", api_url + "/api/v1/callbacks/payments/simulator"
            )
            async with serve(provider_app()) as provider_url:
                settings.provider_url = provider_url
                app.state.resources.settings.provider_url = provider_url
                monkeypatch.setenv("COMMERCE_PROVIDER_URL", provider_url)
                # API 也使用新提供方地址；只调整配置，不替换 HTTP、数据库或业务函数。
                async with httpx.AsyncClient(base_url=api_url, timeout=15) as client:
                    async with httpx.AsyncClient(
                        base_url=provider_url,
                        timeout=15,
                        headers={
                            "Authorization": f"Bearer {settings.provider_key.get_secret_value()}"
                        },
                    ) as provider:
                        yield Backend(
                            r,
                            client,
                            provider,
                            buyer,
                            other,
                            admin,
                            [sku.id for sku in skus],
                            product.id,
                        )
