"""本机一次性全容器验收：指标抓取、告警到达、实例停止和滚动恢复。"""

import argparse
import json
import os
import secrets
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--load-users", type=int, default=4)
    parser.add_argument("--load-duration", type=float, default=10)
    parser.add_argument(
        "--ha", action="store_true", help="同时验证 PostgreSQL 与 RabbitMQ 三节点集群"
    )
    args = parser.parse_args()
    if not 1 <= args.load_users <= 10000 or args.load_duration <= 0:
        parser.error("load-users 应在 1..10000，load-duration 必须为正数")
    os.chdir(ROOT)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    project = f"commerce-acceptance-{stamp.lower()}-{secrets.token_hex(3)}"
    output = ROOT / "var" / "validation" / project
    output.mkdir(parents=True, mode=0o700)
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(
            ("COMMERCE_", "SIMULATOR_", "COMPOSE_", "REDIS_", "POSTGRES_", "PATRONI_", "RABBITMQ_")
        )
    }
    values = {
        "POSTGRES_PASSWORD": secrets.token_hex(24),
        "RABBITMQ_PASSWORD": secrets.token_hex(24),
        "REDIS_CACHE_PASSWORD": secrets.token_hex(32),
        "REDIS_RATE_PASSWORD": secrets.token_hex(32),
        "REDIS_SENTINEL_PASSWORD": secrets.token_hex(32),
        "COMMERCE_ENVIRONMENT": "development",
        "COMMERCE_DATABASE_NAME": "commerce_load_acceptance",
        "COMMERCE_LOAD_ISOLATED": "1",
        "COMMERCE_PROVIDER_KEY": secrets.token_hex(32),
        "COMMERCE_CALLBACK_SECRET": secrets.token_hex(32),
        "COMMERCE_IMAGE": f"a-commerce:{project}",
        "COMMERCE_PATRONI_IMAGE": f"a-commerce-patroni:{project}",
        "POSTGRES_ADMIN_PASSWORD": secrets.token_hex(32),
        "POSTGRES_REPLICATION_PASSWORD": secrets.token_hex(32),
        "PATRONI_API_PASSWORD": secrets.token_hex(32),
        "RABBITMQ_ERLANG_COOKIE": secrets.token_hex(32),
        "COMMERCE_HA": "1" if args.ha else "0",
    }
    values["SIMULATOR_API_KEY"] = values["COMMERCE_PROVIDER_KEY"]
    values["SIMULATOR_CALLBACK_SECRET"] = values["COMMERCE_CALLBACK_SECRET"]
    for port in (
        "POSTGRES",
        "CACHE",
        "RATE",
        "AMQP",
        "BROKER_UI",
        "GATEWAY",
        "PROVIDER",
        "PROMETHEUS",
        "ALERTMANAGER",
    ):
        values[f"COMMERCE_{port}_PORT"] = "0"
    subnet = secrets.randbelow(128) + 64
    values.update(
        COMMERCE_EDGE_SUBNET=f"172.30.{subnet}.0/24",
        COMMERCE_EDGE_RANGE=f"172.30.{subnet}.128/25",
        COMMERCE_GATEWAY_IP=f"172.30.{subnet}.2",
        COMMERCE_BACKEND_PREFIX=f"172.28.{subnet}",
        COMMERCE_DCS_PREFIX=f"172.27.{subnet}",
    )
    report: dict[str, object] = {"project": project, "date_utc": stamp}
    with tempfile.TemporaryDirectory(prefix="commerce-acceptance-") as temporary:
        env_file = Path(temporary) / "isolated.env"
        env_file.touch(mode=0o600)
        env_file.write_text("\n".join(f"{key}={value}" for key, value in values.items()) + "\n")
        environment.update(values, COMMERCE_ENV_FILE=str(env_file), COMPOSE_PROJECT_NAME=project)
        compose = [
            "docker",
            "compose",
            "--project-name",
            project,
            "--env-file",
            str(env_file),
            "-f",
            "deploy/compose.yaml",
            "-f",
            "deploy/compose.redis-sentinel.yaml",
            "-f",
            "deploy/compose.monitoring.yaml",
        ]
        try:
            if args.ha:
                compose.extend(["-f", "deploy/compose.ha.yaml"])
            subprocess.run(
                [*compose, "build", "api", *(["pg-1"] if args.ha else [])],
                env=environment,
                check=True,
            )
            subprocess.run(
                [*compose, "up", "-d", "--wait", "--wait-timeout", "360"],
                env=environment,
                check=True,
            )
            subprocess.run(
                [*compose, "exec", "-T", "api", "python", "scripts/check_redis.py"],
                env=environment,
                check=True,
            )
            subprocess.run(
                [
                    *compose,
                    "run",
                    "--rm",
                    "--no-deps",
                    "--entrypoint",
                    "promtool",
                    "prometheus",
                    "check",
                    "config",
                    "/etc/prometheus/prometheus.yml",
                ],
                env=environment,
                check=True,
            )
            subprocess.run(
                [
                    *compose,
                    "run",
                    "--rm",
                    "--no-deps",
                    "-v",
                    f"{ROOT / 'tests/prometheus-rules.yml'}:/etc/prometheus/rules-test.yml:ro",
                    "--entrypoint",
                    "promtool",
                    "prometheus",
                    "test",
                    "rules",
                    "/etc/prometheus/rules-test.yml",
                ],
                env=environment,
                check=True,
            )
            subprocess.run(
                [
                    *compose,
                    "run",
                    "--rm",
                    "--no-deps",
                    "--entrypoint",
                    "amtool",
                    "alertmanager",
                    "check-config",
                    "/etc/alertmanager/alertmanager.yml",
                ],
                env=environment,
                check=True,
            )
            urls = {}
            for service, port in (
                ("gateway", "8080"),
                ("prometheus", "9090"),
                ("alertmanager", "9093"),
            ):
                address = subprocess.check_output(
                    [*compose, "port", service, port], env=environment, text=True
                ).strip()
                if not address:
                    raise RuntimeError(f"{service} has no published loopback port")
                urls[service] = f"http://{address}"
            with httpx.Client(timeout=10) as client:
                response = client.get(urls["gateway"] + "/health/ready")
                response.raise_for_status()
                if client.get(urls["gateway"] + "/metrics").status_code != 404:
                    raise RuntimeError("public metrics endpoint must be hidden")
                report["gateway_ready"] = response.json()
                deadline = time.monotonic() + 120
                while True:
                    targets = client.get(urls["prometheus"] + "/api/v1/targets").json()["data"][
                        "activeTargets"
                    ]
                    if len(targets) == 8 and all(target["health"] == "up" for target in targets):
                        break
                    if time.monotonic() >= deadline:
                        raise RuntimeError("not all eight monitoring targets became healthy")
                    time.sleep(2)
                report["scrape_targets"] = [
                    {"job": target["labels"]["job"], "health": target["health"]}
                    for target in targets
                ]
                subprocess.run(
                    [
                        *compose,
                        "run",
                        "--rm",
                        "--no-deps",
                        "-T",
                        "api",
                        "python",
                        "scripts/seed_load.py",
                        "--users",
                        str(args.load_users),
                        "--products",
                        "100",
                        "--output",
                        "/app/var/files/load-dataset.json",
                    ],
                    env=environment,
                    check=True,
                )
                dataset = Path(temporary) / "load-dataset.json"
                dataset.touch(mode=0o600)
                dataset.write_bytes(
                    subprocess.check_output(
                        [*compose, "exec", "-T", "api", "cat", "/app/var/files/load-dataset.json"],
                        env=environment,
                    )
                )
                subprocess.run(
                    [
                        "uv",
                        "run",
                        "--no-sync",
                        "pytest",
                        "tests/test_sentinel.py",
                        "-q",
                        "-W",
                        "error::ResourceWarning",
                        f"--junitxml={output / 'sentinel-junit.xml'}",
                    ],
                    env={
                        **environment,
                        "COMMERCE_SENTINEL_TEST_COMPOSE": json.dumps(compose),
                        "COMMERCE_SENTINEL_TEST_URL": urls["gateway"],
                        "COMMERCE_SENTINEL_TEST_DATASET": str(dataset),
                        "COMMERCE_SENTINEL_TEST_REPORT": str(output / "sentinel.json"),
                    },
                    check=True,
                )
                report["sentinel"] = json.loads((output / "sentinel.json").read_text())
                if args.ha:
                    subprocess.run(
                        [
                            "uv",
                            "run",
                            "--no-sync",
                            "pytest",
                            "tests/test_ha.py",
                            "-q",
                            "-x",
                            "-W",
                            "error::ResourceWarning",
                            f"--junitxml={output / 'ha-junit.xml'}",
                        ],
                        env={
                            **environment,
                            "COMMERCE_HA_TEST_COMPOSE": json.dumps(compose),
                            "COMMERCE_HA_TEST_URL": urls["gateway"],
                            "COMMERCE_HA_TEST_DATASET": str(dataset),
                            "COMMERCE_HA_TEST_REPORT": str(output / "ha.json"),
                        },
                        check=True,
                    )
                    report["ha"] = json.loads((output / "ha.json").read_text())
                subprocess.run(
                    [
                        "uv",
                        "run",
                        "--no-sync",
                        "python",
                        "scripts/load_backend.py",
                        "--base-url",
                        urls["gateway"],
                        "--dataset",
                        str(dataset),
                        "--output",
                        str(output / "load.json"),
                        "--users",
                        str(args.load_users),
                        "--duration",
                        str(args.load_duration),
                        "--think-min",
                        "0.1",
                        "--think-max",
                        "0.3",
                        "--order-fraction",
                        "1",
                    ],
                    env={**environment, "PYTHONPATH": str(ROOT / "src")},
                    check=True,
                )
                report["load"] = json.loads((output / "load.json").read_text())
                # HTTP 结束后核对数据库，不把请求成功率当作资金/库存验收。
                violations = subprocess.check_output(
                    [
                        *compose,
                        "exec",
                        "-T",
                        "db-tools" if args.ha else "db",
                        "psql",
                        "-U",
                        "commerce",
                        "-d",
                        values["COMMERCE_DATABASE_NAME"],
                        "-At",
                        "-v",
                        "ON_ERROR_STOP=1",
                        "-c",
                        "SELECT COUNT(*) FROM stock_items s WHERE "
                        "s.reserved <> COALESCE((SELECT SUM(i.quantity) FROM order_items i "
                        "JOIN orders o ON o.id=i.order_id WHERE i.sku_id=s.sku_id "
                        "AND o.status IN ('pending_payment','closing')),0) OR "
                        "s.allocated <> COALESCE((SELECT SUM(i.quantity) FROM order_items i "
                        "JOIN orders o ON o.id=i.order_id WHERE i.sku_id=s.sku_id "
                        "AND o.status='paid' AND o.fulfillment_status "
                        "IN ('unfulfilled','fulfilling')),0); "
                        "SELECT COUNT(*) FROM orders o WHERE o.paid_at IS NOT NULL AND "
                        "o.total_fen <> COALESCE((SELECT SUM(f.amount_fen) "
                        "FROM financial_entries f "
                        "WHERE f.order_id=o.id AND f.kind='payment'),0);",
                    ],
                    env=environment,
                    text=True,
                ).splitlines()
                if violations != ["0", "0"]:
                    raise RuntimeError("load left stock or payment ledger inconsistencies")
                report["load_consistency"] = {"stock_mismatches": 0, "payment_mismatches": 0}
                # 真实停止一个 API，观察 Prometheus 规则 -> Alertmanager 的本地接收链路。
                subprocess.run([*compose, "stop", "api-secondary"], env=environment, check=True)
                deadline = time.monotonic() + 150
                while True:
                    alerts = client.get(urls["alertmanager"] + "/api/v2/alerts").json()
                    if any(
                        alert["labels"].get("alertname") == "CommerceScrapeMissing"
                        for alert in alerts
                    ):
                        report["alertmanager_received"] = "CommerceScrapeMissing"
                        break
                    if time.monotonic() >= deadline:
                        raise RuntimeError("scrape failure alert did not reach Alertmanager")
                    time.sleep(2)
                for _ in range(10):
                    client.get(urls["gateway"] + "/api/v1/customer/products").raise_for_status()
                report["single_api_catalog_successes"] = 10
                subprocess.run(
                    [*compose, "up", "-d", "--no-deps", "--wait", "api-secondary"],
                    env=environment,
                    check=True,
                )
                subprocess.run(["bash", "deploy/roll-api.sh"], env=environment, check=True)
                client.get(urls["gateway"] + "/health/ready").raise_for_status()
                report["rolling_restart"] = "passed"
                deadline = time.monotonic() + 90
                while True:
                    alerts = client.get(urls["prometheus"] + "/api/v1/alerts").json()["data"][
                        "alerts"
                    ]
                    if not any(
                        alert["labels"].get("alertname") == "CommerceScrapeMissing"
                        for alert in alerts
                    ):
                        report["scrape_alert_resolved"] = True
                        break
                    if time.monotonic() >= deadline:
                        raise RuntimeError("scrape alert did not resolve after recovery")
                    time.sleep(2)
                # 保存实际 RabbitMQ 指标名，防止告警引用不存在的 series。
                samples = client.get(
                    urls["prometheus"] + "/api/v1/query",
                    params={"query": "rabbitmq_queue_messages_ready"},
                ).json()["data"]["result"]
                if not samples:
                    raise RuntimeError("RabbitMQ per-queue metrics are absent")
                report["rabbitmq_queue_series"] = len(samples)
                report["result"] = "passed"
        finally:
            (output / "result.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n"
            )
            with (output / "services.log").open("w") as log:
                subprocess.run(
                    [*compose, "logs", "--no-color"],
                    env=environment,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
            subprocess.run(
                [*compose, "down", "--volumes", "--remove-orphans"], env=environment, check=True
            )
    print(f"Deployment validation: {output}")


if __name__ == "__main__":
    main()
