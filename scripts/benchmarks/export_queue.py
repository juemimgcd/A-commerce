"""Run real HTTP export bursts against disposable services; preserve all failures and evidence."""

import asyncio
import csv
import hashlib
import io
import json
import math
import os
import secrets
import socket
import subprocess
import sys
import time
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import aio_pika
import httpx
from pydantic import SecretStr
from sqlalchemy import insert, text

from a_commerce.conf.resources import resources
from a_commerce.conf.settings import Settings
from a_commerce.conf.traffic import TrafficSettings
from a_commerce.messaging.topology import declare
from a_commerce.models.entities import DailyReport, ScheduledTask, Session, Staff

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
    project = f"commerce-export-{stamp}-{secrets.token_hex(2)}"
    output = ROOT / "var" / "validation" / project
    output.mkdir(parents=True, mode=0o700)
    print(f"Evidence: {output}", flush=True)
    # A fresh Compose project and explicit loopback ports prevent writes to business services.
    ports: dict[str, int] = {}
    sockets = []
    for name in ("db", "redis", "amqp", "management", "api"):
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
                    "POSTGRES_DB": "commerce_export_test",
                    "POSTGRES_USER": "commerce",
                    "POSTGRES_PASSWORD": password,
                },
                "ports": [f"127.0.0.1:{ports['db']}:5432"],
                "cpus": 2,
                "mem_limit": "1g",
                "healthcheck": {
                    "test": ["CMD", "pg_isready", "-U", "commerce", "-d", "commerce_export_test"],
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
                    "RABBITMQ_DEFAULT_VHOST": "commerce_export_test",
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
    base_env = {k: v for k, v in os.environ.items() if not k.startswith("COMMERCE_")}
    for key in list(os.environ):
        if key.startswith("COMMERCE_"):
            del os.environ[key]
    settings = Settings(
        _env_file=None,
        database_url=SecretStr(
            f"postgresql+asyncpg://commerce:{password}@127.0.0.1:{ports['db']}/commerce_export_test"
        ),
        redis_url=SecretStr(f"redis://127.0.0.1:{ports['redis']}/0"),
        rate_redis_url=SecretStr(f"redis://127.0.0.1:{ports['redis']}/1"),
        amqp_url=SecretStr(
            f"amqp://commerce:{password}@127.0.0.1:{ports['amqp']}/commerce_export_test"
        ),
        provider_key=SecretStr(secrets.token_hex(32)),
        callback_secret=SecretStr(secrets.token_hex(32)),
        files_directory=output / "files",
        worker_group="general",
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

    async def api_ready(client: httpx.AsyncClient, process: subprocess.Popen[bytes]) -> None:
        # Startup is asynchronous; early process exit or a deadline is a real setup failure.
        for _ in range(150):
            if process.poll() is not None:
                raise RuntimeError("API exited during startup; inspect API log")
            try:
                response = await client.get("/health/live")
                if response.status_code == 200:
                    return
            except httpx.ConnectError:
                pass
            await asyncio.sleep(0.2)
        raise TimeoutError("API did not become ready in 30 seconds")

    snapshots: list[dict[str, Any]] = []
    broker_stats: list[dict[str, Any]] = []
    sample_stop = asyncio.Event()
    sampler: asyncio.Task[None] | None = None
    summary: dict[str, Any] = {"project": project, "rounds": []}
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
        token = secrets.token_hex(24)
        staff_id = uuid4()
        today = datetime.now(UTC)
        first_day = date(2025, 1, 1)
        days = [first_day + timedelta(days=i) for i in range(366)]
        expected_rows = [["日期（UTC）", "实收（分）", "实退（分）", "净收款（分）"]] + [
            [str(day), str(100000 + i), str(1000 + i % 10), str(99000 + i - i % 10)]
            for i, day in enumerate(days)
        ]
        async with resources(settings) as r:
            async with r.engine.begin() as conn:
                await conn.execute(
                    insert(Staff),
                    [
                        {
                            "id": staff_id,
                            "email": "export-benchmark@example.invalid",
                            "password_hash": "!disabled",
                            "permissions": ["audit"],
                        }
                    ],
                )
                await conn.execute(
                    insert(Session),
                    [
                        {
                            "staff_id": staff_id,
                            "token_hash": hashlib.sha256(token.encode()).hexdigest(),
                            "expires_at": today + timedelta(hours=1),
                        }
                    ],
                )
                await conn.execute(
                    insert(DailyReport),
                    [
                        {
                            "day": day,
                            "sales_fen": 100000 + i,
                            "refunds_fen": 1000 + i % 10,
                            "paid_orders": 10,
                        }
                        for i, day in enumerate(days)
                    ],
                )
                # Keep unrelated scheduled maintenance out of the export-only workload.
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
                relay = start("relay", "a_commerce.dispatch.main", settings)
                worker = start("worker", "a_commerce.workers.main", settings)
                api_args = (
                    "a_commerce.main:create_app",
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(ports["api"]),
                    "--no-access-log",
                )
                api = start("api-default", "uvicorn", settings, *api_args)
                async with (
                    httpx.AsyncClient(
                        base_url=f"http://127.0.0.1:{ports['api']}",
                        headers={"Authorization": f"Bearer {token}"},
                        timeout=15,
                        limits=httpx.Limits(max_connections=100, max_keepalive_connections=100),
                    ) as client,
                    httpx.AsyncClient(
                        base_url=f"http://127.0.0.1:{ports['management']}",
                        auth=("commerce", password),
                        timeout=5,
                    ) as management,
                ):
                    await api_ready(client, api)
                    epoch = time.perf_counter()

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
                                          (SELECT count(*) FROM export_jobs) AS created,
                                          (SELECT count(*) FROM export_jobs
                                            WHERE status='done') AS done,
                                          (SELECT count(*) FROM outbox_events
                                            WHERE kind='export'
                                              AND sent_at IS NULL) AS outbox_unsent,
                                          (SELECT count(*) FROM async_operations
                                            WHERE kind='export' AND status='running') AS running,
                                          (SELECT count(*) FROM async_operations
                                            WHERE kind='export' AND status IN
                                              ('retry_wait','unknown','manual_review')) AS failed
                                    """)
                                            )
                                        )
                                        .mappings()
                                        .one()
                                    )
                                snap = dict(row, elapsed_s=sampled)
                                snapshots.append(snap)
                                stream.write(json.dumps(snap) + "\n")
                                stream.flush()
                                if sampled - last_broker >= 1:
                                    response = await management.get(
                                        "/api/queues/commerce_export_test/commerce.export.v1"
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
                    accepted: list[dict[str, Any]] = []

                    async def submit(index: int, scheduled: float) -> dict[str, Any]:
                        started = time.perf_counter()
                        result: dict[str, Any] = {
                            "index": index,
                            "started_s": started - epoch,
                            "schedule_lag_ms": (started - scheduled) * 1000,
                        }
                        try:
                            response = await client.post(
                                "/api/v1/admin/exports",
                                json={
                                    "start": str(days[0]),
                                    "end": str(days[-1]),
                                },
                            )
                            result.update(status=response.status_code, body=response.json())
                        except httpx.TransportError as exc:
                            result.update(status="transport_error", error=repr(exc))
                        result["latency_ms"] = (time.perf_counter() - started) * 1000
                        result["finished_s"] = time.perf_counter() - epoch
                        return result

                    async def drain(target: int) -> float:
                        # A finite deadline exposes stuck jobs instead of silently dropping them.
                        deadline = time.perf_counter() + 180
                        while time.perf_counter() < deadline:
                            if sampler.done():
                                await sampler
                            if relay.poll() is not None or worker.poll() is not None:
                                raise RuntimeError("Relay or worker exited; inspect process logs")
                            if snapshots and snapshots[-1]["done"] >= target:
                                if (
                                    snapshots[-1]["outbox_unsent"] == 0
                                    and snapshots[-1]["running"] == 0
                                ):
                                    return float(snapshots[-1]["elapsed_s"])
                            await asyncio.sleep(0.1)
                        raise TimeoutError(f"Export backlog did not drain; target={target}")

                    # Default-policy probe is kept separate from the task-throughput experiment.
                    probe = await asyncio.gather(
                        *[submit(i, time.perf_counter()) for i in range(20)]
                    )
                    (output / "default-policy.json").write_text(json.dumps(probe, indent=2))
                    accepted.extend(item for item in probe if item["status"] == 202)
                    summary["default_policy"] = dict(Counter(str(x["status"]) for x in probe))
                    await drain(len(accepted))
                    api.terminate()
                    await asyncio.to_thread(api.wait, 15)
                    traffic = settings.traffic.model_dump()
                    for policy in ("entry_ip", "user_write", "user_read"):
                        traffic[policy] = {"rate": 200, "burst": 200}
                    bench = settings.model_copy(update={"traffic": TrafficSettings(**traffic)})
                    summary["test_traffic"] = traffic
                    api = start("api-benchmark", "uvicorn", bench, *api_args)
                    await api_ready(client, api)
                    print("Services ready; worker continuously consuming", flush=True)
                    for count, rate in ((100, 10), (300, 50), (600, 100)):
                        began = time.perf_counter()
                        tasks = []
                        for index in range(count):
                            scheduled = began + index / rate
                            await asyncio.sleep(max(0, scheduled - time.perf_counter()))
                            tasks.append(asyncio.create_task(submit(index, scheduled)))
                        results = await asyncio.gather(*tasks)
                        ended = time.perf_counter()
                        round_accepted = [x for x in results if x["status"] == 202]
                        accepted.extend(round_accepted)
                        drained = await drain(len(accepted))
                        samples = [
                            s for s in snapshots if began - epoch <= s["elapsed_s"] <= drained
                        ]
                        summary["rounds"].append(
                            {
                                "planned": count,
                                "target_rps": rate,
                                "statuses": dict(Counter(str(x["status"]) for x in results)),
                                "http_ms": stats([x["latency_ms"] for x in results]),
                                "schedule_lag_ms": stats([x["schedule_lag_ms"] for x in results]),
                                "submission_duration_s": round(ended - began, 3),
                                "drain_after_last_response_s": round(
                                    max(0, drained - (ended - epoch)), 3
                                ),
                                "total_completion_s": round(drained - (began - epoch), 3),
                                "peak_outbox_unsent": max(s["outbox_unsent"] for s in samples),
                                # Management statistics are sampled separately at about 1 second.
                                # Startup samples can lack fields before broker collection.
                                "peak_queue_ready": max(
                                    b["ready"]
                                    for b in broker_stats
                                    if began - epoch <= b["elapsed_s"] <= drained
                                    and b["ready"] is not None
                                ),
                                "peak_running": max(s["running"] for s in samples),
                                "peak_failed": max(s["failed"] for s in samples),
                                "began_s": began - epoch,
                                "drained_s": drained,
                            }
                        )
                        (output / f"requests-{rate}rps.json").write_text(
                            json.dumps(results, indent=2)
                        )
                        (output / "summary.json").write_text(json.dumps(summary, indent=2))
                        print(json.dumps(summary["rounds"][-1]), flush=True)
                    downloads = []

                    async def verify(item: dict[str, Any]) -> dict[str, Any]:
                        job_id = item["body"]["id"]
                        response = await client.get(f"/api/v1/admin/exports/{job_id}/download")
                        rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
                        return {
                            "job_id": job_id,
                            "status": response.status_code,
                            "correct": response.status_code == 200 and rows == expected_rows,
                            "sha256": hashlib.sha256(response.content).hexdigest(),
                        }

                    for item in accepted:
                        downloads.append(asyncio.create_task(verify(item)))
                        await asyncio.sleep(0.02)
                    checked = await asyncio.gather(*downloads)
                    summary["downloads"] = {
                        "checked": len(checked),
                        "correct": sum(x["correct"] for x in checked),
                        "status_counts": dict(Counter(str(x["status"]) for x in checked)),
                        "distinct_content_hashes": len({x["sha256"] for x in checked}),
                        "expected_data_rows": 366,
                    }
                    (output / "downloads.json").write_text(json.dumps(checked, indent=2))
                    sample_stop.set()
                    await sampler
                    summary["final"] = snapshots[-1]
                    summary["max_unacked_sampled"] = max(
                        x["unacked"] for x in broker_stats if x["unacked"] is not None
                    )
                    summary["final_broker"] = broker_stats[-1]
                    summary["passed"] = (
                        len(accepted) == len([x for x in probe if x["status"] == 202]) + 1000
                        and all(x["correct"] for x in checked)
                        and summary["final"]["failed"] == 0
                        and broker_stats[-1]["ready"] == 0
                        and broker_stats[-1]["unacked"] == 0
                        and broker_stats[-1]["message_stats"].get("ack") == len(accepted)
                        and broker_stats[-1]["message_stats"].get("publish") == len(accepted)
                    )
                    (output / "broker-stats.json").write_text(json.dumps(broker_stats, indent=2))
                    (output / "summary.json").write_text(json.dumps(summary, indent=2))
                    print(json.dumps(summary, indent=2), flush=True)
                    if not summary["passed"]:
                        raise RuntimeError("Export benchmark checks failed; see summary.json")
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
