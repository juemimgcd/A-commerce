"""一次性真实服务回归；只清理本次创建的 Compose 项目，不连接业务环境。"""

import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    os.chdir(ROOT)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    project = f"commerce-test-{stamp.lower()}-{secrets.token_hex(3)}"
    output = ROOT / "var" / "validation" / project
    output.mkdir(parents=True, mode=0o700)
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("COMMERCE_", "SIMULATOR_", "TEST_"))
    }
    environment.update(PYTHONPATH=str(ROOT / "src"), TEST_PASSWORD=secrets.token_hex(24))
    # Docker 的自动端口映射可能在 stop/start 后变化。先选择空闲端口并明确映射，
    # 保证同一轮故障恢复期间地址稳定；不固定为业务环境使用的端口。
    reservations = []
    try:
        for variable in ("TEST_DB_PORT", "TEST_REDIS_PORT", "TEST_AMQP_PORT"):
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            reservations.append(sock)
            environment[variable] = str(sock.getsockname()[1])
    finally:
        for sock in reservations:
            sock.close()
    with tempfile.TemporaryDirectory(prefix="commerce-test-") as temporary:
        env_file = Path(temporary) / "empty.env"
        env_file.touch(mode=0o600)
        compose = [
            "docker",
            "compose",
            "--project-name",
            project,
            "--env-file",
            str(env_file),
            "-f",
            str(ROOT / "deploy/compose.test.yaml"),
        ]
        result = 1
        try:
            subprocess.run(
                [*compose, "up", "-d", "--wait", "--wait-timeout", "180"],
                env=environment,
                check=True,
            )
            ports: dict[str, int] = {}
            for service, port in (("db", "5432"), ("redis", "6379"), ("rabbitmq", "5672")):
                address = subprocess.check_output(
                    [*compose, "port", service, port], env=environment, text=True
                ).strip()
                ports[service] = int(address.rsplit(":", 1)[1])
            password = environment["TEST_PASSWORD"]
            environment.update(
                COMMERCE_DATABASE_URL=(
                    f"postgresql+asyncpg://commerce:{password}@127.0.0.1:{ports['db']}/commerce_test"
                ),
                COMMERCE_REDIS_URL=f"redis://127.0.0.1:{ports['redis']}/0",
                COMMERCE_RATE_REDIS_URL=f"redis://127.0.0.1:{ports['redis']}/1",
                COMMERCE_AMQP_URL=(
                    f"amqp://commerce:{password}@127.0.0.1:{ports['rabbitmq']}/commerce_test"
                ),
                COMMERCE_PROVIDER_KEY=secrets.token_hex(32),
                COMMERCE_CALLBACK_SECRET=secrets.token_hex(32),
                COMMERCE_TEST_ISOLATED="1",
                COMMERCE_TEST_PROJECT=project,
                COMMERCE_TEST_ENV_FILE=str(env_file),
            )
            for command in (["alembic", "upgrade", "head"], ["alembic", "check"]):
                subprocess.run(["uv", "run", "--no-sync", *command], env=environment, check=True)
            args = sys.argv[1:] or ["-q"]
            result = subprocess.run(
                [
                    "uv",
                    "run",
                    "--no-sync",
                    "pytest",
                    *args,
                    f"--junitxml={output / 'junit.xml'}",
                    "--cov=a_commerce",
                    f"--cov-report=json:{output / 'coverage.json'}",
                    "--cov-report=term",
                ],
                env=environment,
                check=False,
            ).returncode
            (output / "result.json").write_text(
                json.dumps({"project": project, "exit_code": result, "date_utc": stamp}, indent=2)
                + "\n"
            )
        finally:
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
    print(f"Validation artifacts: {output}")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
