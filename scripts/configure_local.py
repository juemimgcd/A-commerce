"""生成仅限本机开发的随机凭证；文件已存在则拒绝覆盖。"""

import os
import secrets
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    password = secrets.token_hex(24)
    broker_password = secrets.token_hex(24)
    provider_key = secrets.token_hex(32)
    callback_secret = secrets.token_hex(32)
    values = {
        "POSTGRES_PASSWORD": password,
        "RABBITMQ_PASSWORD": broker_password,
        "COMMERCE_ENVIRONMENT": "development",
        "COMMERCE_DATABASE_URL": f"postgresql+asyncpg://commerce:{password}@127.0.0.1:55432/commerce",
        "COMMERCE_REDIS_URL": "redis://127.0.0.1:56379/0",
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
