"""默认跳过破坏性演练；只由 validate_deployment.py 在专属项目中启用。"""

import asyncio
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from a_commerce.conf.redis import RedisSentinelSettings


def test_add_redis_secrets_preserves_existing_configuration(tmp_path: Path) -> None:
    directory = tmp_path / "scripts"
    directory.mkdir()
    source = Path(__file__).resolve().parents[1] / "scripts/configure_local.py"
    script = directory / source.name
    script.write_bytes(source.read_bytes())
    env_file = tmp_path / ".env"
    original = "POSTGRES_PASSWORD=existing-database-secret\nREDIS_CACHE_PASSWORD=" + "a" * 64
    env_file.write_text(original)
    command = [sys.executable, str(script), "--redis-secrets"]
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    saved = env_file.read_text()
    assert saved.startswith(original + "\n")
    values = dict(line.split("=", 1) for line in saved.splitlines() if "=" in line)
    for key in ("REDIS_RATE_PASSWORD", "REDIS_SENTINEL_PASSWORD"):
        assert len(values[key]) == 64
        assert all(character in "0123456789abcdef" for character in values[key])
        assert values[key] not in result.stdout + result.stderr
    assert values["REDIS_RATE_PASSWORD"] != values["REDIS_SENTINEL_PASSWORD"]
    assert env_file.stat().st_mode & 0o777 == 0o600
    subprocess.run(command, capture_output=True, check=True)
    assert env_file.read_text() == saved


@pytest.mark.parametrize(
    "changes",
    [
        {"nodes": [("sentinel-1", 26379)]},
        {"nodes": [("same", 26379)] * 3},
        {"rate_master": "commerce-cache"},
        {"password": "short"},
        {"nodes": [("a", 0), ("b", 26379), ("c", 26379)]},
    ],
)
def test_reject_incomplete_sentinel_configuration(changes: dict) -> None:
    config = {
        "nodes": [(f"sentinel-{index}", 26379) for index in range(1, 4)],
        "password": "a" * 64,
        "cache_password": "b" * 64,
        "rate_password": "c" * 64,
    }
    with pytest.raises(ValidationError):
        RedisSentinelSettings(**(config | changes))


@pytest.mark.skipif(
    not os.environ.get("COMMERCE_SENTINEL_TEST_COMPOSE"),
    reason="requires isolated full Docker deployment",
)
async def test_real_sentinel_failover_and_recovery() -> None:
    compose = json.loads(os.environ["COMMERCE_SENTINEL_TEST_COMPOSE"])
    assert compose[compose.index("--project-name") + 1].startswith("commerce-acceptance-")
    report: dict = {"groups": {}}

    async def run(*args: str) -> str:
        process = await asyncio.create_subprocess_exec(
            *compose, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        async with asyncio.timeout(60):
            stdout, stderr = await process.communicate()
        assert process.returncode == 0, stderr.decode()
        return stdout.decode().strip()

    async def cli(service: str, *args: str, sentinel: bool = False):
        variable = "REDIS_SENTINEL_PASSWORD" if sentinel else "REDIS_PASSWORD"
        return json.loads(
            await run(
                "exec",
                "-T",
                service,
                "sh",
                "-c",
                f'export REDISCLI_AUTH="${variable}"; exec redis-cli --json -p '
                f'{26379 if sentinel else 6379} "$@"',
                "sh",
                *args,
            )
        )

    async def master(group: str) -> str:
        return (
            await cli("sentinel-2", "SENTINEL", "get-master-addr-by-name", group, sentinel=True)
        )[0]

    async def await_role(service: str, role: str, upstream: str | None = None) -> None:
        async with asyncio.timeout(60):
            while True:
                result = await cli(service, "ROLE")
                if result[0] == role and (upstream is None or result[1] == upstream):
                    return
                await asyncio.sleep(0.25)

    # 一直保持两个应用资源池存活；故障期间不重启探针或 API。
    process = await asyncio.create_subprocess_exec(
        *compose,
        "exec",
        "-T",
        "-e",
        "COMMERCE_SENTINEL_TEST=1",
        "api",
        "python",
        "scripts/sentinel_probe.py",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )
    assert process.stdin and process.stdout
    async with asyncio.timeout(10):
        assert json.loads(await process.stdout.readline()) == {"ready": True}

    async def probe(action: str, **args):
        process.stdin.write((json.dumps({"action": action, **args}) + "\n").encode())
        await process.stdin.drain()
        async with asyncio.timeout(10):
            return json.loads(await process.stdout.readline())

    async def await_probe(action: str, **args):
        async with asyncio.timeout(60):
            while True:
                result = await probe(action, **args)
                if result["ok"]:
                    return result["value"]
                await asyncio.sleep(0.1)

    dataset = json.loads(
        await asyncio.to_thread(Path(os.environ["COMMERCE_SENTINEL_TEST_DATASET"]).read_text)
    )
    # 与实际负载工具共享数据格式，HTTP 请求经过网关/真实 API。
    buyer = dataset["users"][0]
    product = dataset["products"][0]
    url = os.environ["COMMERCE_SENTINEL_TEST_URL"]
    async with httpx.AsyncClient(base_url=url, timeout=10) as http:

        async def quote():
            return await http.post(
                "/api/v1/customer/quotes",
                headers={"Authorization": f"Bearer {buyer['token']}"},
                json={
                    "address_id": buyer["address_id"],
                    "items": [{"sku_id": product["sku_id"], "quantity": 1}],
                },
            )

        try:
            api_ids = await run("ps", "-q", "api", "api-secondary", "api-callbacks")
            for host, port in (("redis", 6379), ("redis-rate", 6379), ("sentinel-1", 26379)):
                result = await probe("unauthenticated", host=host, port=port)
                assert result == {"ok": False, "error": "AuthenticationError"}
            assert (await quote()).status_code == 201
            # 失去一个 Sentinel 后，剩余两个仍有多数派；客户端跳过不可用发现节点。
            await run("stop", "-t", "0", "sentinel-1")
            for group, rate, nodes in (
                (
                    "commerce-cache",
                    False,
                    ["redis", "redis-cache-replica-1", "redis-cache-replica-2"],
                ),
                (
                    "commerce-rate",
                    True,
                    ["redis-rate", "redis-rate-replica-1", "redis-rate-replica-2"],
                ),
            ):
                key = "sentinel-marker:" + secrets.token_hex(12)
                old = await master(group)
                assert old in nodes
                # 标记复制到两副本；预热第二个池，确保验证的是已有连接恢复。
                assert (await probe("wait", rate=rate, key=key, value="before"))["value"] == [
                    True,
                    2,
                ]
                assert (await probe("get", rate=rate, key=key, second=True))["value"] == "before"
                started = time.monotonic()
                await run("stop", "-t", "0", old)
                if rate:
                    failed = await quote()
                    assert failed.status_code == 503, failed.text
                    assert failed.json()["error"]["code"] == "RATE_LIMIT_UNAVAILABLE"
                # 商品查询在切主窗口仍走缓存降级。
                assert (await http.get("/api/v1/customer/products")).status_code == 200
                async with asyncio.timeout(60):
                    while True:
                        new = await master(group)
                        if new != old:
                            break
                        await asyncio.sleep(0.2)
                assert new in nodes
                assert await await_probe("get", rate=rate, key=key) == "before"
                assert await await_probe("set", rate=rate, key=key, value="after") is True
                assert await await_probe("get", rate=rate, key=key, second=True) == "after"
                elapsed = time.monotonic() - started
                if rate:
                    assert (await quote()).status_code == 201
                # 旧主恢复，Sentinel 将其改为副本，配置重写必须跨再次重启生效。
                await run("start", "--wait", old)
                await await_role(old, "slave", new)
                await run("restart", old)
                await await_role(old, "slave", new)
                await run("restart", "sentinel-2", "sentinel-3")
                await run("up", "-d", "--no-deps", "--wait", "sentinel-2", "sentinel-3")
                assert await master(group) == new
                report["groups"][group] = {
                    "old": old,
                    "new": new,
                    "recovery_seconds": round(elapsed, 3),
                    "old_master_rejoined": True,
                    "persisted_after_restart": True,
                    "existing_clients_recovered": True,
                }
            # 限流组失去所有副本时，主节点仍在线，但禁止扣额度与新写入。
            rate_master = await master("commerce-rate")
            replicas = [
                node
                for node in ["redis-rate", "redis-rate-replica-1", "redis-rate-replica-2"]
                if node != rate_master
            ]
            await run("stop", "-t", "0", *replicas)
            async with asyncio.timeout(15):
                while True:
                    if not (await probe("limit", rate=True, key=secrets.token_hex(12)))["ok"]:
                        break
                    await asyncio.sleep(0.2)
            assert (await probe("limit", rate=True, key="no-replicas"))[
                "error"
            ] == "RATE_LIMIT_UNAVAILABLE"
            assert (await probe("limit", rate=True, key="callback", fail_open=True))["ok"]
            assert (await quote()).status_code == 503
            assert (await http.get("/api/v1/customer/products")).status_code == 200
            await run("start", "--wait", *replicas)
            await await_probe("limit", rate=True, key=secrets.token_hex(12))
            assert (await quote()).status_code == 201
            # 主节点切换后两个应用池仍共用同一限流预算。
            subject = secrets.token_hex(12)
            assert (await probe("limit", rate=True, key=subject))["ok"]
            assert (await probe("limit", rate=True, key=subject, second=True))["ok"]
            assert (await probe("limit", rate=True, key=subject))["error"] == "RATE_LIMITED"
            await run("start", "--wait", "sentinel-1")
            await run("exec", "-T", "api", "python", "scripts/check_redis.py")
            assert await run("ps", "-q", "api", "api-secondary", "api-callbacks") == api_ids
            report.update(
                result="passed",
                auth_required=True,
                single_sentinel_loss=True,
                no_replica_writes_rejected=True,
                api_containers_unchanged=True,
            )
        finally:
            process.stdin.write(b'{"action":"exit"}\n')
            await process.stdin.drain()
            async with asyncio.timeout(10):
                await process.wait()
            await asyncio.to_thread(
                Path(os.environ["COMMERCE_SENTINEL_TEST_REPORT"]).write_text,
                json.dumps(report, indent=2) + "\n",
            )
