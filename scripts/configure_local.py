"""生成仅限本机开发的随机凭证；文件已存在则拒绝覆盖。"""

import argparse
import os
import secrets
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--redis-secrets",
        action="store_true",
        help="向现有 .env 追加缺失的 Redis 凭证，不覆盖任何已有值",
    )
    parser.add_argument(
        "--ha-secrets",
        action="store_true",
        help="向现有 .env 追加 PostgreSQL HA 和 RabbitMQ 集群凭证",
    )
    args = parser.parse_args()
    if args.redis_secrets and args.ha_secrets:
        parser.error("一次只选择一组凭证")
    root = Path(__file__).resolve().parents[1]
    redis_values = {
        key: secrets.token_hex(32)
        for key in ("REDIS_CACHE_PASSWORD", "REDIS_RATE_PASSWORD", "REDIS_SENTINEL_PASSWORD")
    }
    ha_values = {
        key: secrets.token_hex(32)
        for key in (
            "POSTGRES_ADMIN_PASSWORD",
            "POSTGRES_REPLICATION_PASSWORD",
            "PATRONI_API_PASSWORD",
            "RABBITMQ_ERLANG_COOKIE",
        )
    }
    if args.redis_secrets or args.ha_secrets:
        path = root / ".env"
        existing = path.read_text()
        keys = {line.split("=", 1)[0].strip() for line in existing.splitlines() if "=" in line}
        selected = redis_values if args.redis_secrets else ha_values
        missing = {key: value for key, value in selected.items() if key not in keys}
        if missing:
            with path.open("a") as target:
                target.write(
                    "\n" + "\n".join(f"{key}={value}" for key, value in missing.items()) + "\n"
                )
            path.chmod(0o600)
        print(f"已追加 {len(missing)} 项基础设施凭证；已有值保持不变，未输出凭证。")
        return
    password = secrets.token_hex(24)
    broker_password = secrets.token_hex(24)
    provider_key = secrets.token_hex(32)
    callback_secret = secrets.token_hex(32)
    values = {
        **redis_values,
        **ha_values,
        "POSTGRES_PASSWORD": password,
        "RABBITMQ_PASSWORD": broker_password,
        "COMMERCE_ENVIRONMENT": "development",
        "COMMERCE_DATABASE_URL": f"postgresql+asyncpg://commerce:{password}@127.0.0.1:55432/commerce",
        "COMMERCE_REDIS_URL": "redis://127.0.0.1:56379/0",
        "COMMERCE_RATE_REDIS_URL": "redis://127.0.0.1:56380/0",
        "COMMERCE_AMQP_URL": f"amqp://commerce:{broker_password}@127.0.0.1:55672/commerce",
        "COMMERCE_PROVIDER_URL": "http://127.0.0.1:58001",
        "COMMERCE_PROVIDER_KEY": provider_key,
        "COMMERCE_CALLBACK_SECRET": callback_secret,
        "SIMULATOR_API_KEY": provider_key,
        "SIMULATOR_CALLBACK_SECRET": callback_secret,
        "SIMULATOR_CALLBACK_URL": "http://127.0.0.1:58000/api/v1/callbacks/payments/simulator",
        "SIMULATOR_DATABASE_PATH": "var/provider.sqlite3",
    }
    descriptor = os.open(root / ".env", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as target:
        target.write("\n".join(f"{key}={value}" for key, value in values.items()) + "\n")
    print("已生成 .env（权限 0600）；未输出凭证，未覆盖现有配置。")


if __name__ == "__main__":
    main()
