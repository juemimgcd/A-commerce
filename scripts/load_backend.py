"""执行有独立身份、思考时间的混合购买路径；输出实际负载，不预设容量成绩。"""

import argparse
import asyncio
import json
import random
import secrets
import time
from collections import Counter
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
from uuid import UUID

import httpx
from pydantic import BaseModel, Field, SecretStr

from a_commerce.schemas.api import OrderOut, PaymentOut, QuoteOut


class LoadUser(BaseModel):
    token: SecretStr
    address_id: UUID


class LoadProduct(BaseModel):
    product_id: UUID
    sku_id: UUID


class Dataset(BaseModel):
    users: list[LoadUser] = Field(min_length=1)
    products: list[LoadProduct] = Field(min_length=1)


class LoadConfig(BaseModel):
    base_url: str
    mode: Literal["users", "arrival"] = "users"
    users: int = Field(default=10, ge=1, le=10000)
    duration: float = Field(default=30, gt=0)
    arrival_rate: float = Field(default=1, gt=0)
    think_min: float = Field(default=3, ge=0)
    think_max: float = Field(default=7, ge=0)
    browse_steps: int = Field(default=17, ge=1, le=100)
    order_fraction: float = Field(default=0.25, ge=0, le=1)
    pay_fraction: float = Field(default=0.7, ge=0, le=1)
    hotspot_fraction: float = Field(default=0.1, ge=0, le=1)
    result_timeout: float = Field(default=30, gt=0, le=300)
    seed: int = 42


class Measurements:
    def __init__(self) -> None:
        self.status: Counter[str] = Counter()
        self.routes: Counter[str] = Counter()
        self.business: Counter[str] = Counter()
        self.latencies: list[float] = []
        self.requests = 0
        self.dropped = 0
        self.maximum_active = 0
        self.random = random.Random(42)

    def record(self, route: str, status: str, seconds: float) -> None:
        self.requests += 1
        self.status[status] += 1
        self.routes[route] += 1
        # 有界 reservoir，避免长稳运行把所有延迟样本留在内存。
        if len(self.latencies) < 100000:
            self.latencies.append(seconds)
        else:
            index = self.random.randrange(self.requests)
            if index < len(self.latencies):
                self.latencies[index] = seconds

    def report(self, seconds: float, config: LoadConfig) -> dict[str, object]:
        ordered = sorted(self.latencies)
        percentiles = {
            f"p{percent}": ordered[min(len(ordered) - 1, int(len(ordered) * percent / 100))]
            if ordered
            else None
            for percent in (50, 95, 99)
        }
        return {
            "config": config.model_dump(),
            "wall_seconds": seconds,
            "requests": self.requests,
            "rps": self.requests / seconds,
            "status_counts": dict(self.status),
            "route_counts": dict(self.routes),
            "business": dict(self.business),
            "latency_seconds": percentiles,
            "latency_sample_count": len(self.latencies),
            "dropped_journeys": self.dropped,
            "maximum_active_users": self.maximum_active,
            "successful_order_tps": self.business["orders_created"] / seconds,
        }


async def run_load(config: LoadConfig, data: Dataset) -> dict[str, object]:
    if config.users > len(data.users) or config.think_max < config.think_min:
        raise ValueError("需要每用户独立身份，且 think_max >= think_min")
    stats = Measurements()
    started = time.monotonic()
    end = started + config.duration
    available: asyncio.Queue[int] = asyncio.Queue()
    for index in range(config.users):
        available.put_nowait(index)
    active: set[asyncio.Task[None]] = set()
    failures: list[BaseException] = []

    async with httpx.AsyncClient(
        base_url=config.base_url,
        # 本机隔离压测不得经过系统代理，否则代理容量/502 会冒充应用瓶颈。
        trust_env=False,
        timeout=10,
        limits=httpx.Limits(
            max_connections=config.users * 2, max_keepalive_connections=config.users
        ),
    ) as client:

        async def request(
            method: str,
            path: str,
            route: str,
            user: LoadUser,
            body: dict[str, object] | None = None,
            key: str | None = None,
        ) -> httpx.Response | None:
            headers = {"Authorization": f"Bearer {user.token.get_secret_value()}"}
            if key:
                headers["Idempotency-Key"] = key
            before = time.monotonic()
            try:
                response = await client.request(method, path, json=body, headers=headers)
            except httpx.HTTPError:
                stats.record(route, "transport_error", time.monotonic() - before)
                return None
            stats.record(route, str(response.status_code), time.monotonic() - before)
            return response

        async def journey(index: int, cycle: int) -> None:
            user = data.users[index]
            rng = random.Random(config.seed + index * 1000003 + cycle)
            product = (
                data.products[0]
                if rng.random() < config.hotspot_fraction
                else rng.choice(data.products)
            )

            async def think() -> None:
                await asyncio.sleep(rng.uniform(config.think_min, config.think_max))

            stats.business["journeys_started"] += 1
            for step in range(config.browse_steps):
                path = (
                    f"/api/v1/customer/products?cursor={product.product_id}"
                    if step % 2 == 0
                    else (f"/api/v1/customer/products/{product.product_id}")
                )
                response = await request("GET", path, "catalog", user)
                if response is None or response.status_code != 200:
                    stats.business["journeys_failed"] += 1
                    return
                await think()
            for path in ("cart", "orders"):
                response = await request("GET", f"/api/v1/customer/{path}", "private_read", user)
                if response is None or response.status_code != 200:
                    stats.business["journeys_failed"] += 1
                    return
                await think()
            response = await request(
                "PUT",
                f"/api/v1/customer/cart/items/{product.sku_id}",
                "cart_write",
                user,
                {"quantity": 1},
            )
            if response is None or response.status_code != 204:
                stats.business["journeys_failed"] += 1
                return
            await think()
            response = await request(
                "POST",
                "/api/v1/customer/quotes",
                "quote",
                user,
                {
                    "address_id": str(user.address_id),
                    "items": [{"sku_id": str(product.sku_id), "quantity": 1}],
                },
            )
            if response is None or response.status_code != 201:
                stats.business["journeys_failed"] += 1
                return
            quote = QuoteOut.model_validate(response.json())
            await think()
            if rng.random() >= config.order_fraction:
                stats.business["quotes_abandoned"] += 1
                return
            key = secrets.token_hex(16)
            response = await request(
                "POST",
                "/api/v1/customer/orders",
                "order_create",
                user,
                {"quote_id": str(quote.id)},
                key,
            )
            # 超时/503 可能已经提交，只查询原 key，不能改 key 重试造成重复购买。
            if response is None or response.status_code >= 500:
                response = await request(
                    "GET", f"/api/v1/customer/order-requests/{key}", "order_recover", user
                )
            if response is None or response.status_code not in {200, 201}:
                stats.business["orders_rejected"] += 1
                return
            order = OrderOut.model_validate(response.json())
            stats.business["orders_created"] += 1
            if rng.random() >= config.pay_fraction:
                stats.business["orders_abandoned"] += 1
                return
            deadline = time.monotonic() + config.result_timeout
            while time.monotonic() < deadline:
                response = await request(
                    "GET",
                    f"/api/v1/customer/orders/{order.id}/payment-session",
                    "payment_status",
                    user,
                )
                if response is not None and response.status_code == 200:
                    payment = PaymentOut.model_validate(response.json())
                    if payment.status == "pending":
                        break
                await asyncio.sleep(0.5)
            else:
                stats.business["payment_unresolved"] += 1
                return
            response = await request(
                "POST",
                f"/api/v1/customer/orders/{order.id}/simulate-payment",
                "simulated_pay",
                user,
            )
            if response is None or response.status_code != 202:
                stats.business["payment_submit_failed"] += 1
                return
            while time.monotonic() < deadline:
                response = await request(
                    "GET", f"/api/v1/customer/orders/{order.id}", "order_status", user
                )
                if response is not None and response.status_code == 200:
                    current = OrderOut.model_validate(response.json())
                    if current.payment_status == "paid" and current.status == "paid":
                        stats.business["payments_confirmed"] += 1
                        return
                    if current.status == "cancelled":
                        stats.business["payment_cancelled"] += 1
                        return
                await asyncio.sleep(0.5)
            stats.business["payment_unresolved"] += 1

        async def user_loop(index: int) -> None:
            cycle = 0
            stats.maximum_active = config.users
            while time.monotonic() < end:
                await journey(index, cycle)
                cycle += 1

        if config.mode == "users":
            await asyncio.gather(*(user_loop(index) for index in range(config.users)))
        else:

            async def once(index: int, cycle: int) -> None:
                try:
                    await journey(index, cycle)
                finally:
                    available.put_nowait(index)

            cycle = 0
            while time.monotonic() < end:
                try:
                    index = available.get_nowait()
                except asyncio.QueueEmpty:
                    stats.dropped += 1
                else:
                    task = asyncio.create_task(once(index, cycle))
                    active.add(task)

                    def completed(task: asyncio.Task[None]) -> None:
                        active.discard(task)
                        if not task.cancelled():
                            error = task.exception()
                            if error is not None and not failures:
                                failures.append(error)

                    task.add_done_callback(completed)
                    stats.maximum_active = max(
                        stats.maximum_active, config.users - available.qsize()
                    )
                cycle += 1
                # 固定到达时刻；服务变慢时不自动降低注入率，资源不够记 dropped。
                await asyncio.sleep(
                    max(0, started + cycle / config.arrival_rate - time.monotonic())
                )
            if active:
                await asyncio.gather(*active)
            if failures:
                raise failures[0]
    return stats.report(time.monotonic() - started, config)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=["users", "arrival"], default="users")
    parser.add_argument("--users", type=int, default=10)
    parser.add_argument("--duration", type=float, default=30)
    parser.add_argument("--arrival-rate", type=float, default=1)
    parser.add_argument("--think-min", type=float, default=3)
    parser.add_argument("--think-max", type=float, default=7)
    parser.add_argument("--browse-steps", type=int, default=17)
    parser.add_argument("--order-fraction", type=float, default=0.25)
    parser.add_argument("--pay-fraction", type=float, default=0.7)
    parser.add_argument("--hotspot-fraction", type=float, default=0.1)
    args = parser.parse_args()
    # CLI 默认只接受 loopback，避免误把模拟付款负载发往外部服务。
    if urlparse(args.base_url).hostname not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("仅接受本地隔离环境；正式远端压测需独立部署与授权")
    config = LoadConfig(
        **{key: value for key, value in vars(args).items() if key not in {"dataset", "output"}}
    )
    data = Dataset.model_validate_json(args.dataset.read_text())
    report = asyncio.run(run_load(config, data))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"Load report: {args.output.resolve()}")
    business = report["business"]
    statuses = report["status_counts"]
    if isinstance(business, dict) and isinstance(statuses, dict):
        failed = sum(
            int(business.get(key, 0))
            for key in (
                "journeys_failed",
                "orders_rejected",
                "payment_unresolved",
                "payment_submit_failed",
            )
        )
        failed += sum(
            int(count)
            for status, count in statuses.items()
            if status.startswith("5") or status == "transport_error"
        )
        raise SystemExit(1 if failed or report["dropped_journeys"] else 0)


if __name__ == "__main__":
    main()
