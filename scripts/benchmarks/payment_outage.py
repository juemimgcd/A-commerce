"""Measure accepted orders and payment-session recovery during a real simulator outage."""

import asyncio
import hashlib
import json
import math
import os
import secrets
import socket
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import aio_pika
import httpx
from pydantic import SecretStr
from sqlalchemy import insert, text

from a_commerce.conf.resources import resources
from a_commerce.conf.settings import Settings
from a_commerce.messaging.topology import declare
from a_commerce.models.entities import (
    SKU,
    Address,
    Category,
    Product,
    ScheduledTask,
    Session,
    Shop,
    Stock,
    User,
)

ROOT = Path(__file__).resolve().parents[2]


def stats(values: list[float]) -> dict[str, float]:
    values = sorted(values)
    return {
        "mean": round(sum(values) / len(values), 3),
        "p50": round(values[math.ceil(len(values) * 0.5) - 1], 3),
        "p95": round(values[math.ceil(len(values) * 0.95) - 1], 3),
        "max": round(values[-1], 3),
    }


async def main() -> None:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ").lower()
    project = f"commerce-payment-outage-{stamp}-{secrets.token_hex(2)}"
    output = ROOT / "var" / "validation" / project
    output.mkdir(parents=True, mode=0o700)
    print(f"Evidence: {output}", flush=True)
    # A fresh Compose project and explicit loopback ports prevent writes to business services.
    ports: dict[str, int] = {}
    sockets = []
    for name in ("db", "redis", "amqp", "management", "api", "provider"):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        ports[name] = sock.getsockname()[1]
        sockets.append(sock)
    for sock in sockets:
        sock.close()
    password = secrets.token_hex(24)
    compose_data = {
        "services": {
            "db": {
                "image": "postgres:18.6",
                "environment": {
                    "POSTGRES_DB": "commerce_payment_test",
                    "POSTGRES_USER": "commerce",
                    "POSTGRES_PASSWORD": password,
                },
                "ports": [f"127.0.0.1:{ports['db']}:5432"],
                "cpus": 2,
                "mem_limit": "1g",
                "healthcheck": {
                    "test": ["CMD", "pg_isready", "-U", "commerce", "-d", "commerce_payment_test"],
                    "interval": "2s",
                    "timeout": "3s",
                    "retries": 60,
                },
            },
            "redis": {
                "image": "redis:7.4.9-alpine",
                "ports": [f"127.0.0.1:{ports['redis']}:6379"],
                "cpus": 1,
                "mem_limit": "256m",
                "healthcheck": {
                    "test": ["CMD", "redis-cli", "ping"],
                    "interval": "2s",
                    "timeout": "3s",
                    "retries": 60,
                },
            },
            "rabbitmq": {
                "image": "rabbitmq:4.4.0-management-alpine",
                "environment": {
                    "RABBITMQ_DEFAULT_USER": "commerce",
                    "RABBITMQ_DEFAULT_PASS": password,
                    "RABBITMQ_DEFAULT_VHOST": "commerce_payment_test",
                    "RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS": (
                        "-rabbit collect_statistics_interval 1000"
                    ),
                },
                "ports": [
                    f"127.0.0.1:{ports['amqp']}:5672",
                    f"127.0.0.1:{ports['management']}:15672",
                ],
                "cpus": 2,
                "mem_limit": "1g",
                "healthcheck": {
                    "test": ["CMD", "rabbitmq-diagnostics", "-q", "ping"],
                    "interval": "3s",
                    "timeout": "5s",
                    "retries": 60,
                },
            },
        }
    }
    compose_file = output / "compose.json"
    compose_file.write_text(json.dumps(compose_data, indent=2))
    compose_file.chmod(0o600)
    empty_env = output / "empty.env"
    empty_env.touch()
    compose = [
        "docker",
        "compose",
        "--project-name",
        project,
        "--env-file",
        str(empty_env),
        "-f",
        str(compose_file),
    ]
    base_env = {
        k: v for k, v in os.environ.items() if not k.startswith(("COMMERCE_", "SIMULATOR_"))
    }
    for key in list(os.environ):
        if key.startswith("COMMERCE_"):
            del os.environ[key]
    settings = Settings(
        _env_file=None,
        database_url=SecretStr(
            f"postgresql+asyncpg://commerce:{password}@127.0.0.1:{ports['db']}/commerce_payment_test"
        ),
        redis_url=SecretStr(f"redis://127.0.0.1:{ports['redis']}/0"),
        rate_redis_url=SecretStr(f"redis://127.0.0.1:{ports['redis']}/1"),
        amqp_url=SecretStr(
            f"amqp://commerce:{password}@127.0.0.1:{ports['amqp']}/commerce_payment_test"
        ),
        provider_key=SecretStr(secrets.token_hex(32)),
        callback_secret=SecretStr(secrets.token_hex(32)),
        files_directory=output / "files",
        worker_group="critical",
        provider_url=f"http://127.0.0.1:{ports['provider']}",
        reconciliation_enabled=False,
        mail_enabled=False,
    )

    def environment(config: Settings) -> dict[str, str]:
        env = dict(base_env, PYTHONPATH=str(ROOT / "src"))
        for key in type(config).model_fields:
            value = getattr(config, key)
            if value is not None:
                serialized = (
                    value.get_secret_value()
                    if isinstance(value, SecretStr)
                    else value.model_dump_json()
                    if hasattr(value, "model_dump_json")
                    else str(value)
                )
                env[f"COMMERCE_{key.upper()}"] = serialized
        env.update(
            SIMULATOR_DATABASE_PATH=str(output / "provider.sqlite3"),
            SIMULATOR_API_KEY=config.provider_key.get_secret_value(),
            SIMULATOR_CALLBACK_SECRET=config.callback_secret.get_secret_value(),
            SIMULATOR_CALLBACK_URL=f"http://127.0.0.1:{ports['api']}/api/v1/callbacks/payments/simulator",
        )
        return env

    processes: list[subprocess.Popen[bytes]] = []
    logs: list[Any] = []

    def start(name: str, module: str, config: Settings, *args: str) -> subprocess.Popen[bytes]:
        log = (output / f"{name}.log").open("wb")
        logs.append(log)
        process = subprocess.Popen(
            [sys.executable, "-m", module, *args],
            cwd=output,
            env=environment(config),
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        processes.append(process)
        return process

    async def ready(client: httpx.AsyncClient, process: subprocess.Popen[bytes], path: str) -> None:
        # Startup is asynchronous; early process exit or a deadline is a real setup failure.
        for _ in range(150):
            if process.poll() is not None:
                raise RuntimeError("Process exited during startup; inspect its log")
            try:
                response = await client.get(path)
                if response.status_code == 200:
                    return
            except httpx.ConnectError:
                pass
            await asyncio.sleep(0.2)
        raise TimeoutError("Process did not become ready in 30 seconds")

    snapshots: list[dict[str, Any]] = []
    broker_stats: list[dict[str, Any]] = []
    sample_stop = asyncio.Event()
    sampler: asyncio.Task[None] | None = None
    summary: dict[str, Any] = {
        "project": project,
        "planned_orders": 100,
        "order_rps": 10,
        "planned_outage_s": 20,
        "traffic": settings.traffic.model_dump(),
    }
    try:
        with (output / "setup.log").open("wb") as setup_log:
            await asyncio.to_thread(
                subprocess.run,
                [*compose, "up", "-d", "--wait", "--wait-timeout", "180"],
                check=True,
                stdout=setup_log,
                stderr=subprocess.STDOUT,
            )
            await asyncio.to_thread(
                subprocess.run,
                [
                    str(ROOT / ".venv/bin/alembic"),
                    "-c",
                    str(ROOT / "alembic.ini"),
                    "upgrade",
                    "head",
                ],
                cwd=ROOT,
                env=environment(settings),
                check=True,
                stdout=setup_log,
                stderr=subprocess.STDOUT,
            )
        today = datetime.now(UTC)
        buyers = [
            {"id": uuid4(), "address": uuid4(), "token": secrets.token_hex(24)} for _ in range(100)
        ]
        catalog = [{"product": uuid4(), "sku": uuid4()} for _ in range(10)]
        shop_id, category_id = uuid4(), uuid4()
        async with resources(settings) as r:
            async with r.engine.begin() as conn:
                await conn.execute(
                    insert(User),
                    [
                        {
                            "id": buyer["id"],
                            "email": f"buyer-{i}@example.invalid",
                            "password_hash": "!disabled",
                        }
                        for i, buyer in enumerate(buyers)
                    ],
                )
                await conn.execute(
                    insert(Session),
                    [
                        {
                            "user_id": buyer["id"],
                            "token_hash": hashlib.sha256(str(buyer["token"]).encode()).hexdigest(),
                            "expires_at": today + timedelta(hours=1),
                        }
                        for buyer in buyers
                    ],
                )
                await conn.execute(
                    insert(Address),
                    [
                        {
                            "id": buyer["address"],
                            "user_id": buyer["id"],
                            "recipient": "隔离测试",
                            "phone": "12345678",
                            "region": "测试区域",
                            "detail": "测试地址",
                        }
                        for buyer in buyers
                    ],
                )
                await conn.execute(
                    insert(Shop),
                    [
                        {
                            "id": shop_id,
                            "owner_user_id": buyers[0]["id"],
                            "name": "隔离测试店铺",
                            "status": "active",
                            "updated_at": today,
                        }
                    ],
                )
                await conn.execute(insert(Category), [{"id": category_id, "name": "测试商品"}])
                await conn.execute(
                    insert(Product),
                    [
                        {
                            "id": item["product"],
                            "shop_id": shop_id,
                            "category_id": category_id,
                            "title": f"隔离商品 {i}",
                            "active": True,
                        }
                        for i, item in enumerate(catalog)
                    ],
                )
                await conn.execute(
                    insert(SKU),
                    [
                        {
                            "id": item["sku"],
                            "product_id": item["product"],
                            "code": f"OUTAGE-{i}",
                            "price_fen": 2500,
                            "attributes": {},
                        }
                        for i, item in enumerate(catalog)
                    ],
                )
                await conn.execute(
                    insert(Stock), [{"sku_id": item["sku"], "on_hand": 1000} for item in catalog]
                )
                await conn.execute(
                    insert(ScheduledTask),
                    [
                        {
                            "name": "housekeeping",
                            "next_run_at": today + timedelta(days=1),
                        }
                    ],
                )
            connection = await aio_pika.connect_robust(settings.amqp_url.get_secret_value())
            async with connection:
                channel = await connection.channel()
                await declare(channel)
                api = start(
                    "api",
                    "uvicorn",
                    settings,
                    "a_commerce.main:create_app",
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(ports["api"]),
                    "--no-access-log",
                )
                provider_args = (
                    "a_commerce.integrations.payments.simulator:create_app",
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(ports["provider"]),
                )
                provider = start("provider-before", "uvicorn", settings, *provider_args)
                relay = start("relay", "a_commerce.dispatch.main", settings)
                worker = start("worker", "a_commerce.workers.main", settings)
                async with (
                    httpx.AsyncClient(
                        base_url=f"http://127.0.0.1:{ports['api']}",
                        trust_env=False,
                        timeout=15,
                        limits=httpx.Limits(max_connections=100, max_keepalive_connections=100),
                    ) as client,
                    httpx.AsyncClient(
                        base_url=settings.provider_url,
                        trust_env=False,
                        timeout=2,
                        headers={
                            "Authorization": "Bearer " + settings.provider_key.get_secret_value()
                        },
                    ) as provider_client,
                    httpx.AsyncClient(
                        base_url=f"http://127.0.0.1:{ports['management']}",
                        auth=("commerce", password),
                        trust_env=False,
                        timeout=5,
                    ) as management,
                ):
                    await ready(client, api, "/health/ready")
                    await ready(provider_client, provider, "/ledger")
                    epoch = time.perf_counter()
                    database_path = output / "provider.sqlite3"

                    def payment_rows() -> list[tuple[str, int, str, str]]:
                        # Read the simulator's real durable facts, not just HTTP response counts.
                        with closing(
                            sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
                        ) as db:
                            return db.execute(
                                "SELECT id, amount, merchant, state FROM payments"
                            ).fetchall()

                    async def observe() -> None:
                        last_broker = -1.0
                        with (output / "snapshots.jsonl").open("w") as stream:
                            while not sample_stop.is_set():
                                sampled = time.perf_counter() - epoch
                                async with r.engine.connect() as conn:
                                    row = (
                                        (
                                            await conn.execute(
                                                text("""
                                        SELECT
                                          (SELECT count(*) FROM orders) AS orders,
                                          (SELECT count(*) FROM orders
                                            WHERE payment_status='pending') AS pending_payment,
                                          (SELECT count(*) FROM orders
                                            WHERE payment_status='unknown') AS unknown_payment,
                                          (SELECT count(*) FROM outbox_events
                                            WHERE kind='payment'
                                              AND sent_at IS NULL) AS outbox_unsent,
                                          (SELECT count(*) FROM async_operations
                                            WHERE kind='payment'
                                              AND status='unknown') AS unknown_tasks,
                                          (SELECT count(*) FROM async_operations
                                            WHERE kind='payment'
                                              AND status='pending') AS pending_tasks,
                                          (SELECT count(*) FROM async_operations
                                            WHERE kind='payment'
                                              AND status='running') AS running,
                                          (SELECT count(*) FROM async_operations
                                            WHERE kind='payment'
                                              AND status='retry_wait') AS retry_wait,
                                          (SELECT count(*) FROM async_operations
                                            WHERE kind='payment'
                                              AND status='manual_review') AS manual_review
                                    """)
                                            )
                                        )
                                        .mappings()
                                        .one()
                                    )
                                provider_rows = await asyncio.to_thread(payment_rows)
                                snap = dict(
                                    row, elapsed_s=sampled, provider_created=len(provider_rows)
                                )
                                snapshots.append(snap)
                                stream.write(json.dumps(snap) + "\n")
                                stream.flush()
                                if sampled - last_broker >= 1:
                                    response = await management.get(
                                        "/api/queues/commerce_payment_test/commerce.payment.v1"
                                    )
                                    response.raise_for_status()
                                    body = response.json()
                                    broker_stats.append(
                                        {
                                            "elapsed_s": sampled,
                                            "ready": body.get("messages_ready"),
                                            "unacked": body.get("messages_unacknowledged"),
                                            "consumers": body.get("consumers"),
                                            "message_stats": body.get("message_stats", {}),
                                        }
                                    )
                                    last_broker = sampled
                                await asyncio.sleep(0.1)

                    sampler = asyncio.create_task(observe())
                    quotes = []
                    for i, buyer in enumerate(buyers):
                        response = await client.post(
                            "/api/v1/customer/quotes",
                            headers={"Authorization": f"Bearer {buyer['token']}"},
                            json={
                                "address_id": str(buyer["address"]),
                                "items": [
                                    {
                                        "sku_id": str(catalog[i % 10]["sku"]),
                                        "quantity": 1,
                                    }
                                ],
                            },
                        )
                        response.raise_for_status()
                        quotes.append(response.json()["id"])
                        await asyncio.sleep(0.1)
                    print("100 quotes ready; stopping payment simulator", flush=True)
                    provider.terminate()
                    await asyncio.to_thread(provider.wait, 15)
                    # Only start the outage workload after proving the provider is unreachable.
                    try:
                        await provider_client.get("/ledger")
                    except httpx.ConnectError:
                        outage_began = time.perf_counter()
                    else:
                        raise RuntimeError(
                            "Simulator is still reachable; outage was not established"
                        )
                    summary["outage_began_s"] = outage_began - epoch

                    async def order(index: int, scheduled: float) -> dict[str, Any]:
                        began = time.perf_counter()
                        result: dict[str, Any] = {
                            "index": index,
                            "began_s": began - epoch,
                            "schedule_lag_ms": (began - scheduled) * 1000,
                        }
                        try:
                            response = await client.post(
                                "/api/v1/customer/orders",
                                headers={
                                    "Authorization": f"Bearer {buyers[index]['token']}",
                                    "Idempotency-Key": f"outage-order-{index}-{project}",
                                },
                                json={"quote_id": quotes[index]},
                            )
                            result.update(status=response.status_code, body=response.json())
                        except httpx.TransportError as exc:
                            result.update(status="transport_error", error=repr(exc))
                        result["latency_ms"] = (time.perf_counter() - began) * 1000
                        result["finished_s"] = time.perf_counter() - epoch
                        return result

                    requests = []
                    for index in range(100):
                        scheduled = outage_began + index / 10
                        await asyncio.sleep(max(0, scheduled - time.perf_counter()))
                        requests.append(asyncio.create_task(order(index, scheduled)))
                    results = await asyncio.gather(*requests)
                    (output / "orders.json").write_text(json.dumps(results, indent=2))
                    accepted = [x for x in results if x["status"] == 201]
                    summary["order_statuses"] = dict(Counter(str(x["status"]) for x in results))
                    summary["N"] = len(accepted)
                    summary["order_http_ms"] = stats([x["latency_ms"] for x in results])
                    summary["schedule_lag_ms"] = stats([x["schedule_lag_ms"] for x in results])
                    await asyncio.sleep(max(0, outage_began + 20 - time.perf_counter()))
                    if sampler.done():
                        await sampler
                    summary["before_restore"] = snapshots[-1]
                    outage_provider_rows = await asyncio.to_thread(payment_rows)
                    if not accepted or outage_provider_rows:
                        raise RuntimeError(
                            "Need accepted orders and zero provider payments during outage"
                        )
                    restored_at = time.perf_counter()
                    summary["restore_command_s"] = restored_at - epoch
                    summary["actual_outage_before_restart_s"] = restored_at - outage_began
                    provider = start("provider-after", "uvicorn", settings, *provider_args)
                    await ready(provider_client, provider, "/ledger")
                    ready_at = time.perf_counter()
                    summary["provider_ready_s"] = ready_at - epoch
                    print(f"Provider restarted; accepted N={len(accepted)} orders", flush=True)
                    # Completion requires provider sessions AND local pending-payment state.
                    # Tasks remain retry_wait while awaiting actual customer payment.
                    deadline = restored_at + 120
                    while time.perf_counter() < deadline:
                        if sampler.done():
                            await sampler
                        if relay.poll() is not None or worker.poll() is not None:
                            raise RuntimeError("Relay or worker exited; inspect process logs")
                        snap = snapshots[-1]
                        if snap["provider_created"] == len(accepted) and snap[
                            "pending_payment"
                        ] == len(accepted):
                            completed = time.perf_counter()
                            summary["T_from_restart_s"] = round(completed - restored_at, 3)
                            summary["T_from_ready_s"] = round(completed - ready_at, 3)
                            summary["recovered_snapshot"] = snap
                            break
                        await asyncio.sleep(0.1)
                    else:
                        raise TimeoutError("Payment sessions did not recover within 120 seconds")
                    async with r.engine.connect() as conn:
                        intents = (
                            (
                                await conn.execute(
                                    text("""
                            SELECT i.id::text AS intent_id, o.id::text AS order_id, o.total_fen,
                                   o.payment_status, o.status, o.paid_at
                            FROM payment_intents i JOIN orders o ON o.id=i.order_id
                        """)
                                )
                            )
                            .mappings()
                            .all()
                        )
                        inventory = dict(
                            (
                                await conn.execute(
                                    text("""
                            SELECT sum(reserved) AS reserved, sum(allocated) AS allocated
                            FROM stock_items
                        """)
                                )
                            )
                            .mappings()
                            .one()
                        )
                        task_errors = [
                            dict(x)
                            for x in (
                                await conn.execute(
                                    text("""
                            SELECT status, last_error, count(*) AS count
                            FROM async_operations WHERE kind='payment'
                            GROUP BY status, last_error
                        """)
                                )
                            ).mappings()
                        ]
                    provider_final = await asyncio.to_thread(payment_rows)
                    expected = {
                        x["intent_id"]: (x["total_fen"], settings.merchant_id, "pending")
                        for x in intents
                    }
                    actual = {
                        ident: (amount, merchant, state)
                        for ident, amount, merchant, state in provider_final
                    }
                    expected_orders = {x["body"]["id"] for x in accepted}
                    summary["verification"] = {
                        "provider_rows": len(provider_final),
                        "application_intents": len(intents),
                        "exact_ids_amounts_states_match": expected == actual,
                        "exact_order_ids_match": {x["order_id"] for x in intents}
                        == expected_orders,
                        "all_orders_unpaid": all(x["paid_at"] is None for x in intents),
                        "inventory": inventory,
                        "task_states": task_errors,
                    }
                    (output / "payment-sessions.json").write_text(
                        json.dumps(
                            {
                                "application": [dict(x) for x in intents],
                                "provider": provider_final,
                            },
                            indent=2,
                            default=str,
                        )
                    )
                    sample_stop.set()
                    await sampler
                    outage_samples = [
                        s
                        for s in snapshots
                        if outage_began - epoch <= s["elapsed_s"] <= restored_at - epoch
                    ]
                    outage_broker = [
                        b
                        for b in broker_stats
                        if outage_began - epoch <= b["elapsed_s"] <= restored_at - epoch
                        and b["ready"] is not None
                    ]
                    summary["outage_peaks"] = {
                        "outbox_unsent": max(s["outbox_unsent"] for s in outage_samples),
                        "unknown_tasks": max(s["unknown_tasks"] for s in outage_samples),
                        "rabbit_ready_sampled": max(b["ready"] for b in outage_broker),
                        "rabbit_unacked_sampled": max(b["unacked"] for b in outage_broker),
                    }
                    summary["passed"] = (
                        len(accepted) == 100
                        and len(intents) == 100
                        and len(provider_final) == 100
                        and expected == actual
                        and {x["order_id"] for x in intents} == expected_orders
                        and all(x["paid_at"] is None for x in intents)
                        and inventory == {"reserved": 100, "allocated": 0}
                        and summary["recovered_snapshot"]["manual_review"] == 0
                    )
                    (output / "broker-stats.json").write_text(json.dumps(broker_stats, indent=2))
                    (output / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
                    print(json.dumps(summary, indent=2, default=str), flush=True)
                    if not summary["passed"]:
                        raise RuntimeError("Payment outage validation failed; see summary.json")
    finally:
        sample_stop.set()
        if sampler is not None and not sampler.done():
            sampler.cancel()
            await asyncio.gather(sampler, return_exceptions=True)
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try:
                    await asyncio.to_thread(process.wait, 15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    await asyncio.to_thread(process.wait)
        for log in logs:
            log.close()
        with (output / "services.log").open("wb") as log:
            await asyncio.to_thread(
                subprocess.run,
                [*compose, "logs", "--no-color"],
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        await asyncio.to_thread(
            subprocess.run, [*compose, "down", "--volumes", "--remove-orphans"], check=True
        )
        compose_file.unlink()


if __name__ == "__main__":
    asyncio.run(main())
