"""通过真实 HTTP 与 PostgreSQL 验证交易结果、资金/库存不变量和权限。"""

import asyncio
from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select, update

from a_commerce.models.base import now
from a_commerce.models.entities import (
    SKU,
    Address,
    FinancialEntry,
    Operation,
    Order,
    Quote,
    Refund,
    Stock,
    StockMovement,
)
from a_commerce.workers.handlers import handle
from tests.conftest import Backend

pytestmark = pytest.mark.integration


async def test_idempotent_order_and_conflicting_parameters(backend: Backend) -> None:
    quote = await backend.quote()
    responses = await asyncio.gather(
        *(backend.place(quote, key="same-order-key") for _ in range(5))
    )
    assert {response.status_code for response in responses} == {201}
    assert len({response.json()["id"] for response in responses}) == 1
    other_quote = await backend.quote()
    conflict = await backend.place(other_quote, key="same-order-key")
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    async with backend.r.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Order)) == 1
        assert (
            await session.scalar(
                select(func.count()).select_from(Operation).where(Operation.kind == "payment")
            )
            == 1
        )
        assert (
            await session.scalar(select(Stock.reserved).where(Stock.sku_id == backend.sku_ids[0]))
            == 1
        )


async def test_last_item_race(backend: Backend) -> None:
    async with backend.r.sessions.begin() as session:
        await session.execute(update(Stock).values(on_hand=1))
    quotes = await asyncio.gather(backend.quote(), backend.quote(backend.other))
    results = await asyncio.gather(
        backend.place(quotes[0]), backend.place(quotes[1], buyer=backend.other)
    )
    assert sorted(response.status_code for response in results) == [201, 409]
    assert (
        next(response for response in results if response.status_code == 409).json()["error"][
            "code"
        ]
        == "OUT_OF_STOCK"
    )
    async with backend.r.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Order)) == 1
        assert (
            await session.scalar(select(Stock.reserved).where(Stock.sku_id == backend.sku_ids[0]))
            == 1
        )


async def test_multi_sku_failure_rolls_back_every_side_effect(backend: Backend) -> None:
    # 业务按 UUID 顺序处理；必须先预留有货 SKU，再在第二个 SKU 失败。
    backend.sku_ids.sort()
    async with backend.r.sessions.begin() as session:
        await session.execute(
            update(Stock).where(Stock.sku_id == backend.sku_ids[1]).values(on_hand=0)
        )
    quote = await backend.quote(quantities=(2, 1))
    response = await backend.place(quote)
    assert response.status_code == 409
    async with backend.r.sessions() as session:
        for model in (Order, Operation, StockMovement):
            assert await session.scalar(select(func.count()).select_from(model)) == 0
        assert list(await session.scalars(select(Stock.reserved))) == [0, 0]


@pytest.mark.parametrize("change", ["price", "address", "expiry"])
async def test_changed_or_expired_quote_is_rejected(backend: Backend, change: str) -> None:
    quote = await backend.quote()
    async with backend.r.sessions.begin() as session:
        if change == "price":
            await session.execute(
                update(SKU)
                .where(SKU.id == backend.sku_ids[0])
                .values(price_fen=2600, version=SKU.version + 1)
            )
        elif change == "address":
            await session.execute(
                update(Address)
                .where(Address.id == backend.buyer.address_id)
                .values(detail="新地址", version=Address.version + 1)
            )
        else:
            await session.execute(
                update(Quote)
                .where(Quote.id == UUID(quote))
                .values(expires_at=now() - timedelta(seconds=1))
            )
    result = await backend.place(quote)
    assert result.status_code == 409
    assert result.json()["error"]["code"] == (
        "QUOTE_EXPIRED" if change == "expiry" else "QUOTE_STALE"
    )
    async with backend.r.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Order)) == 0


async def test_object_and_role_isolation(backend: Backend) -> None:
    order = await backend.order()
    for path in (
        f"/api/v1/customer/orders/{order['id']}",
        f"/api/v1/customer/orders/{order['id']}/payment-session",
    ):
        response = await backend.client.get(path, headers=backend.other.headers)
        assert response.status_code == 404
    response = await backend.client.get("/api/v1/admin/orders", headers=backend.buyer.headers)
    assert response.status_code == 401
    response = await backend.place(await backend.quote(), buyer=backend.other)
    assert response.status_code == 404


async def test_paid_cancel_refund_is_idempotent(backend: Backend) -> None:
    order = await backend.pay(await backend.order(quantity=2))
    assert order["status"] == "paid"
    cancelled = await backend.client.post(
        f"/api/v1/customer/orders/{order['id']}/cancel",
        headers=backend.buyer.headers,
        json={"expected_version": order["version"], "reason": "取消购买"},
    )
    assert cancelled.status_code == 200, cancelled.text
    async with backend.r.sessions() as session:
        refund = (await session.scalars(select(Refund))).one()
        refund_id = refund.id
    message = await backend.dispatch("refund", refund_id)
    await handle(backend.r, message)
    result = await backend.get(order)
    assert result["status"] == "cancelled"
    assert result["refunded_fen"] == order["total_fen"]
    assert result["refund_reserved_fen"] == 0
    assert result["payment_status"] == "refunded"
    async with backend.r.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(FinancialEntry)) == 2
        stock = (
            await session.scalars(select(Stock).where(Stock.sku_id == backend.sku_ids[0]))
        ).one()
        assert (stock.on_hand, stock.reserved, stock.allocated) == (10, 0, 0)


async def test_cancel_and_pick_cannot_both_succeed(backend: Backend) -> None:
    order = await backend.pay(await backend.order())
    results = await asyncio.gather(
        backend.client.post(
            f"/api/v1/customer/orders/{order['id']}/cancel",
            headers=backend.buyer.headers,
            json={"expected_version": order["version"], "reason": "竞争取消"},
        ),
        backend.client.post(
            f"/api/v1/admin/orders/{order['id']}/pick",
            headers=backend.admin.headers,
            json={"expected_version": order["version"]},
        ),
    )
    assert sorted(response.status_code for response in results) == [200, 409]
    current = await backend.get(order)
    assert (current["status"], current["fulfillment_status"]) in {
        ("cancelled", "unfulfilled"),
        ("paid", "fulfilling"),
    }


async def test_two_partial_returns_do_not_duplicate_refund_or_stock(backend: Backend) -> None:
    order = await backend.pay(await backend.order(quantity=2))
    for action in ("pick", "ship", "deliver"):
        payload = {"expected_version": order["version"]}
        if action == "ship":
            payload.update(carrier="测试物流", tracking_number="TEST-TRACKING")
        response = await backend.client.post(
            f"/api/v1/admin/orders/{order['id']}/{action}",
            headers=backend.admin.headers,
            json=payload,
        )
        assert response.status_code == 200, response.text
        order = response.json()
    for index in range(2):
        order = await backend.get(order)
        payload = {
            "expected_version": order["version"],
            "reason": "部分退货",
            "items": [{"order_item_id": order["items"][0]["id"], "quantity": 1}],
        }
        headers = {**backend.buyer.headers, "Idempotency-Key": f"return-test-{index}"}
        response = await backend.client.post(
            f"/api/v1/customer/orders/{order['id']}/returns", headers=headers, json=payload
        )
        assert response.status_code == 201, response.text
        ident = response.json()["id"]
        replay = await backend.client.post(
            f"/api/v1/customer/orders/{order['id']}/returns", headers=headers, json=payload
        )
        assert replay.json()["id"] == ident
        for action in ("approve", "ship", "receive", "inspect"):
            buyer_action = action == "ship"
            body = (
                {"carrier": "测试物流", "tracking_number": f"RETURN-{index}"}
                if buyer_action
                else ({"resellable": True, "note": "完好"} if action == "inspect" else None)
            )
            response = await backend.client.post(
                f"/api/v1/{'customer' if buyer_action else 'admin'}/returns/{ident}/{action}",
                headers=backend.buyer.headers if buyer_action else backend.admin.headers,
                json=body,
            )
            assert response.status_code == 200, response.text
        refund_id = UUID(response.json()["refund_id"])
        repeat = await backend.client.post(
            f"/api/v1/admin/returns/{ident}/inspect",
            headers=backend.admin.headers,
            json={"resellable": True, "note": "再次验收"},
        )
        assert repeat.json()["refund_id"] == str(refund_id)
        await backend.dispatch("refund", refund_id)
    result = await backend.get(order)
    assert result["refunded_fen"] == 5000
    assert result["total_fen"] - result["refunded_fen"] == result["shipping_fen"] == 600
    assert result["items"][0]["returned_quantity"] == 2
    async with backend.r.sessions() as session:
        stock = (
            await session.scalars(select(Stock).where(Stock.sku_id == backend.sku_ids[0]))
        ).one()
        assert (stock.on_hand, stock.reserved, stock.allocated) == (10, 0, 0)
        assert await session.scalar(select(func.count()).select_from(Refund)) == 2
    excess = await backend.client.post(
        f"/api/v1/customer/orders/{order['id']}/returns",
        headers={**backend.buyer.headers, "Idempotency-Key": "third-return-test"},
        json={
            "expected_version": result["version"],
            "reason": "超量",
            "items": [{"order_item_id": order["items"][0]["id"], "quantity": 1}],
        },
    )
    assert excess.json()["error"]["code"] == "RETURN_QUANTITY_EXCEEDED"
