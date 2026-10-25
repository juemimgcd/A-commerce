"""中断、重复投递和乱序回调不能破坏资金、库存或事件恢复。"""

import asyncio
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import aio_pika
import httpx
import pytest
from sqlalchemy import func, select, update

from a_commerce.dispatch.main import relay_one
from a_commerce.integrations.payments.client import ProviderResult
from a_commerce.integrations.payments.simulator import transact
from a_commerce.messaging.envelope import Envelope
from a_commerce.messaging.topology import declare
from a_commerce.models.base import now
from a_commerce.models.entities import (
    Event,
    EventArchive,
    FinancialEntry,
    MessageFailure,
    Operation,
    Order,
    Outbox,
    ReconciliationCase,
    ReconciliationRun,
    Refund,
    Stock,
)
from a_commerce.services import event_recovery, operations, reconciliation
from a_commerce.workers.handlers import finish, handle
from tests.conftest import Backend

pytestmark = pytest.mark.integration


async def test_shipping_duplicate_and_late_transit_cannot_revert_delivery(backend: Backend) -> None:
    order = await backend.pay(await backend.order())
    for action in ("pick", "ship"):
        payload = {"expected_version": order["version"]}
        if action == "ship":
            payload.update(carrier="测试物流", tracking_number="SHIP-TEST")
        response = await backend.client.post(
            f"/api/v1/admin/orders/{order['id']}/{action}",
            headers=backend.admin.headers,
            json=payload,
        )
        assert response.status_code == 200
        order = response.json()
    shipment = await backend.client.get(
        f"/api/v1/customer/orders/{order['id']}/shipment", headers=backend.buyer.headers
    )
    assert shipment.status_code == 200
    payload = {
        "event_id": str(uuid4()),
        "shipment_id": shipment.json()["id"],
        "tracking_number": "SHIP-TEST",
        "state": "delivered",
        "occurred_at": now().isoformat(),
    }
    for _ in range(2):
        assert (await signed_callback(backend, payload, path="shipping")).status_code == 204
    late = {**payload, "event_id": str(uuid4()), "state": "in_transit"}
    assert (await signed_callback(backend, late, path="shipping")).status_code == 204
    result = await backend.get(order)
    assert result["fulfillment_status"] == "delivered"
    latest = await backend.client.get(
        f"/api/v1/customer/orders/{order['id']}/shipment", headers=backend.buyer.headers
    )
    assert len(latest.json()["events"]) == 2


async def test_broker_restart_keeps_unconfirmed_outbox_recoverable(
    backend: Backend, isolated_compose: list[str]
) -> None:
    order = await backend.order()
    connection = await aio_pika.connect_robust(
        backend.r.settings.amqp_url.get_secret_value(), reconnect_interval=1
    )
    try:
        channel = await connection.channel(publisher_confirms=True, on_return_raises=True)
        exchange, queues = await declare(channel)
        await asyncio.to_thread(subprocess.run, [*isolated_compose, "stop", "rabbitmq"], check=True)
        async with asyncio.timeout(7):
            assert not await relay_one(backend.r, exchange)
        async with backend.r.sessions() as session:
            outbox = (await session.scalars(select(Outbox))).one()
            assert outbox.sent_at is None
        await asyncio.to_thread(
            subprocess.run, [*isolated_compose, "start", "--wait", "rabbitmq"], check=True
        )
        async with asyncio.timeout(20):
            await connection.ready()
            await channel.ready()
        async with backend.r.sessions.begin() as session:
            await session.execute(update(Outbox).values(available_at=now() - timedelta(seconds=1)))
        assert await relay_one(backend.r, exchange)
        message = await queues["payment"].get(timeout=10)
        await handle(backend.r, Envelope.model_validate_json(message.body))
        await message.ack()
        assert (await backend.get(order))["payment_status"] == "pending"
    finally:
        await connection.close()
        await asyncio.to_thread(
            subprocess.run, [*isolated_compose, "start", "--wait", "rabbitmq"], check=True
        )


async def signed_callback(
    backend: Backend, payload: dict, *, age: int = 0, path: str = "payments"
) -> httpx.Response:
    body = json.dumps(payload, separators=(",", ":")).encode()
    timestamp = str(int(time.time()) - age)
    signature = hmac.new(
        backend.r.settings.callback_secret.get_secret_value().encode(),
        timestamp.encode() + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    return await backend.client.post(
        f"/api/v1/callbacks/{path}/simulator",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Timestamp": timestamp,
            "X-Signature": signature,
        },
    )


async def test_duplicate_and_out_of_order_callbacks(backend: Backend) -> None:
    order = await backend.pay(await backend.order())
    payload = {
        "event_id": str(uuid4()),
        "intent_id": str(await backend.intent(order)),
        "merchant_id": "a-commerce",
        "amount_fen": order["total_fen"],
        "currency": "CNY",
        "state": "paid",
    }
    for _ in range(2):
        response = await signed_callback(backend, payload)
        assert response.status_code == 204
    for state in ("pending", "closed"):
        response = await signed_callback(
            backend, {**payload, "event_id": str(uuid4()), "state": state}
        )
        assert response.status_code == 204
    assert (await backend.get(order))["status"] == "paid"
    async with backend.r.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(FinancialEntry)) == 1
        stock = (
            await session.scalars(select(Stock).where(Stock.sku_id == backend.sku_ids[0]))
        ).one()
        assert (stock.reserved, stock.allocated) == (0, 1)


@pytest.mark.parametrize("invalid", ["signature", "expired", "amount", "merchant"])
async def test_untrusted_callback_cannot_confirm_payment(backend: Backend, invalid: str) -> None:
    order = await backend.order()
    payload = {
        "event_id": str(uuid4()),
        "intent_id": str(await backend.intent(order)),
        "merchant_id": "other" if invalid == "merchant" else "a-commerce",
        "amount_fen": 1 if invalid == "amount" else order["total_fen"],
        "currency": "CNY",
        "state": "paid",
    }
    if invalid == "signature":
        response = await backend.client.post(
            "/api/v1/callbacks/payments/simulator",
            json=payload,
            headers={"X-Timestamp": str(int(time.time())), "X-Signature": "0" * 64},
        )
    else:
        response = await signed_callback(backend, payload, age=301 if invalid == "expired" else 0)
    assert response.status_code == (401 if invalid in {"signature", "expired"} else 409)
    assert (await backend.get(order))["status"] == "pending_payment"
    async with backend.r.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(FinancialEntry)) == 0


async def test_payment_after_expiry_creates_one_compensation_refund(backend: Backend) -> None:
    order = await backend.order()
    intent = await backend.intent(order)
    await backend.dispatch("payment", intent)
    async with backend.r.sessions.begin() as session:
        await session.execute(
            update(Order)
            .where(Order.id == UUID(order["id"]))
            .values(expires_at=now() - timedelta(seconds=1))
        )
    paid = await backend.provider.post(f"/payments/{intent}/pay")
    assert paid.status_code == 200
    current = await backend.get(order)
    assert current["status"] == "cancelled"
    assert current["refund_reserved_fen"] == current["total_fen"]
    # 新 event_id 的重复 paid 也不能多建退款。
    response = await signed_callback(
        backend,
        {
            "event_id": str(uuid4()),
            "intent_id": str(intent),
            "merchant_id": "a-commerce",
            "amount_fen": order["total_fen"],
            "currency": "CNY",
            "state": "paid",
        },
    )
    assert response.status_code == 204
    async with backend.r.sessions() as session:
        refunds = list(await session.scalars(select(Refund)))
        assert len(refunds) == 1
        stock = (
            await session.scalars(select(Stock).where(Stock.sku_id == backend.sku_ids[0]))
        ).one()
        assert (stock.reserved, stock.allocated) == (0, 0)
    await backend.dispatch("refund", refunds[0].id)
    assert (await backend.get(order))["refunded_fen"] == order["total_fen"]


async def test_closing_before_provider_create_never_reopens_payment(backend: Backend) -> None:
    order = await backend.order()
    intent = await backend.intent(order)
    response = await backend.client.post(
        f"/api/v1/customer/orders/{order['id']}/cancel",
        headers=backend.buyer.headers,
        json={"expected_version": order["version"], "reason": "付款前取消"},
    )
    assert response.json()["status"] == "closing"
    await backend.dispatch("close", intent)
    await backend.dispatch("payment", intent)
    result = await backend.provider.post(f"/payments/{intent}/pay")
    assert result.status_code == 409
    assert (await backend.get(order))["status"] == "cancelled"


async def test_worker_lease_recovery_rejects_stale_fence(backend: Backend) -> None:
    order = await backend.order()
    intent = await backend.intent(order)
    # 提供方已创建、本地写回前进程退出：留下真实外部事实和到期租约，不伪造业务结果。
    result = await backend.provider.put(
        f"/payments/{intent}",
        json={
            "intent_id": str(intent),
            "amount_fen": order["total_fen"],
            "merchant_id": "a-commerce",
            "currency": "CNY",
        },
    )
    assert result.status_code == 200
    async with backend.r.sessions.begin() as session:
        operation = (
            await session.scalars(select(Operation).where(Operation.kind == "payment"))
        ).one()
        operation.status, operation.fence = "running", 1
        operation.lease_until = now() - timedelta(seconds=1)
        operation.next_attempt_at = now() - timedelta(seconds=1)
        operation_id = operation.id
    await operations.scan(backend.r)
    message = await backend.dispatch("payment", intent)
    assert message.dispatch_version == 2
    # 保持新租约 running，让旧响应确实由 fence 拦截，而不是被终态检查提前挡住。
    async with backend.r.sessions.begin() as session:
        await session.execute(
            update(Operation)
            .where(Operation.id == operation_id)
            .values(status="running", lease_until=now() + timedelta(seconds=60))
        )
    result = await backend.provider.post(
        f"/payments/{intent}/close",
        json={
            "intent_id": str(intent),
            "amount_fen": order["total_fen"],
            "merchant_id": "a-commerce",
            "currency": "CNY",
        },
    )
    assert result.status_code == 200
    await finish(
        backend.r,
        operation_id,
        1,
        UUID(order["id"]),
        intent,
        "payment",
        ProviderResult.model_validate(result.json()),
    )
    async with backend.r.sessions() as session:
        operation = await session.get(Operation, operation_id)
        assert operation.fence == 2
        assert operation.status == "running"
        assert (
            await session.scalar(select(Stock.reserved).where(Stock.sku_id == backend.sku_ids[0]))
            == 1
        )
    assert (await backend.get(order))["payment_status"] == "pending"
    # 相同提供方事实交给当前租约才允许关闭和释放库存。
    await finish(
        backend.r,
        operation_id,
        2,
        UUID(order["id"]),
        intent,
        "payment",
        ProviderResult.model_validate(result.json()),
    )
    assert (await backend.get(order))["status"] == "cancelled"
    async with backend.r.sessions() as session:
        assert (
            await session.scalar(select(Stock.reserved).where(Stock.sku_id == backend.sku_ids[0]))
            == 0
        )


async def test_outbox_publish_confirm_and_duplicate_delivery(backend: Backend) -> None:
    order = await backend.order()
    connection = await aio_pika.connect_robust(backend.r.settings.amqp_url.get_secret_value())
    async with connection:
        channel = await connection.channel(publisher_confirms=True, on_return_raises=True)
        exchange, queues = await declare(channel)
        assert await relay_one(backend.r, exchange)
        message = await queues["payment"].get(timeout=10)
        body = message.body
        envelope = Envelope.model_validate_json(body)
        await handle(backend.r, envelope)
        # 提交后 ack 丢失：关闭 channel，broker 重投；不重做业务副作用。
        await channel.close()
        channel = await connection.channel()
        _, queues = await declare(channel)
        message = await queues["payment"].get(timeout=10)
        assert message.redelivered
        await handle(backend.r, Envelope.model_validate_json(message.body))
        await message.ack()
    async with backend.r.sessions() as session:
        row = (await session.scalars(select(Outbox))).one()
        assert row.sent_at is not None
        assert (await session.get(Operation, row.operation_id)).fence == 1
    assert (await backend.get(order))["payment_status"] == "pending"


async def test_unroutable_publish_is_not_marked_sent(backend: Backend) -> None:
    order = await backend.order()
    connection = await aio_pika.connect_robust(backend.r.settings.amqp_url.get_secret_value())
    async with connection:
        channel = await connection.channel(publisher_confirms=True, on_return_raises=True)
        exchange = await channel.declare_exchange(f"unbound-{uuid4()}", auto_delete=True)
        assert not await relay_one(backend.r, exchange)
    async with backend.r.sessions() as session:
        row = (await session.scalars(select(Outbox))).one()
        assert row.sent_at is None
        assert row.attempts == 1
        assert row.available_at > now()
    assert (await backend.get(order))["payment_status"] == "preparing"


async def test_real_worker_process_and_poison_message(backend: Backend, tmp_path: Path) -> None:
    order = await backend.order()
    log_path = tmp_path / "worker.log"
    with log_path.open("wb") as log:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "a_commerce.workers.main",
            cwd=tmp_path,
            env=dict(os.environ),
            stdout=log,
            stderr=log,
        )
        try:
            connection = await aio_pika.connect_robust(
                backend.r.settings.amqp_url.get_secret_value()
            )
            async with connection:
                channel = await connection.channel(publisher_confirms=True, on_return_raises=True)
                exchange, _ = await declare(channel)
                await exchange.publish(aio_pika.Message(body=b"not-json"), routing_key="payment")
                await relay_one(backend.r, exchange)
                async with asyncio.timeout(20):
                    while (await backend.get(order))["payment_status"] != "pending":
                        assert process.returncode is None, log_path.read_text()
                        await asyncio.sleep(0.05)
                async with asyncio.timeout(20):
                    while True:
                        async with backend.r.sessions() as session:
                            count = await session.scalar(
                                select(func.count()).select_from(MessageFailure)
                            )
                        if count:
                            break
                        await asyncio.sleep(0.05)
        finally:
            if process.returncode is None:
                process.terminate()
            async with asyncio.timeout(40):
                await process.wait()
        assert process.returncode == 0, log_path.read_text()


async def test_archive_expired_cursor_snapshot_and_new_increment(backend: Backend) -> None:
    order = await backend.order()
    async with backend.r.sessions.begin() as session:
        await session.execute(update(Event).values(created_at=now() - timedelta(days=91)))
    await event_recovery.archive_batch(backend.r)
    response = await backend.client.get("/api/v1/customer/events", headers=backend.buyer.headers)
    assert response.status_code == 410
    snapshot = await event_recovery.create_snapshot(backend.r, backend.buyer.principal)
    page = await event_recovery.snapshot_page(
        backend.r, backend.buyer.principal, snapshot.id, None, 1
    )
    assert page.items[0]["id"] == order["id"]
    assert page.through_sequence == 1
    await backend.pay(order)
    response = await backend.client.get(
        "/api/v1/customer/events",
        headers=backend.buyer.headers,
        params={"after": page.through_sequence},
    )
    assert response.status_code == 200
    assert [event["kind"] for event in response.json()] == ["payment.pending", "payment.paid"]
    async with backend.r.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(EventArchive)) == 1


async def test_reconciliation_recovers_missing_local_confirmation(backend: Backend) -> None:
    order = await backend.order()
    intent = await backend.intent(order)
    await backend.dispatch("payment", intent)
    # 只阻断回调 HTTP；提供方正常记下付款，本地对账再主动读取提供方事实。
    await asyncio.to_thread(transact, os.environ["SIMULATOR_DATABASE_PATH"], "pay", intent)
    run = await reconciliation.create(backend.r, backend.admin.principal)
    for _ in range(5):
        await backend.dispatch("reconcile", run.id)
        async with backend.r.sessions.begin() as session:
            row = await session.get(ReconciliationRun, run.id)
            if row.status == "done":
                break
            await session.execute(
                update(Operation)
                .where(Operation.kind == "reconcile")
                .values(next_attempt_at=now() - timedelta(seconds=1))
            )
        await operations.scan(backend.r)
    async with backend.r.sessions() as session:
        assert (await session.get(ReconciliationRun, run.id)).status == "done"
        case = (await session.scalars(select(ReconciliationCase))).one()
        assert case.reason == "local_confirmation_missing"
        case_id = case.id
    result = await reconciliation.recheck(
        backend.r, backend.admin.principal, case_id, "主动查单确认"
    )
    assert result.status == "resolved"
    assert (await backend.get(order))["status"] == "paid"
