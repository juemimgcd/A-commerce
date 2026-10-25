"""真实 PostgreSQL/etcd/RabbitMQ 停机、自动切换和恢复；只允许独立验收项目。"""

import asyncio
import json
import os
import secrets
import time
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("COMMERCE_HA_TEST_COMPOSE"), reason="requires validate_deployment.py --ha"
)


async def test_postgres_and_rabbitmq_failover_recovery(tmp_path: Path) -> None:
    compose = json.loads(os.environ["COMMERCE_HA_TEST_COMPOSE"])
    assert compose[compose.index("--project-name") + 1].startswith("commerce-acceptance-")
    report: dict = {}

    async def run(*args: str) -> str:
        process = await asyncio.create_subprocess_exec(
            *compose, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        async with asyncio.timeout(200):
            stdout, stderr = await process.communicate()
        assert process.returncode == 0, stderr.decode()
        return stdout.decode().strip()

    async def patroni(node: str, path: str = "cluster"):
        return json.loads(
            await run(
                "exec",
                "-T",
                node,
                "python",
                "-c",
                f"import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8008/{path}').read().decode())",
            )
        )

    async def leader(observer: str = "pg-1") -> str | None:
        members = (await patroni(observer))["members"]
        return next((m["name"] for m in members if m["role"] in {"leader", "primary"}), None)

    async def healthy() -> None:
        await run("exec", "-T", "api", "python", "scripts/check_ha.py")

    await healthy()
    probe = await asyncio.create_subprocess_exec(
        *compose,
        "exec",
        "-T",
        "-e",
        "COMMERCE_HA_TEST=1",
        "api",
        "python",
        "scripts/ha_probe.py",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )
    assert probe.stdin and probe.stdout
    async with asyncio.timeout(15):
        assert json.loads(await probe.stdout.readline()) == {"ready": True}

    async def command(action: str, **kwargs):
        probe.stdin.write((json.dumps({"action": action, **kwargs}) + "\n").encode())
        await probe.stdin.drain()
        async with asyncio.timeout(15):
            return json.loads(await probe.stdout.readline())

    async def sql(query: str):
        result = await command("sql", sql=query)
        assert result["ok"], result
        return result["value"]

    dataset = json.loads(
        await asyncio.to_thread(Path(os.environ["COMMERCE_HA_TEST_DATASET"]).read_text)
    )
    buyer, product = dataset["users"][0], dataset["products"][0]
    sku = product["sku_id"]
    headers = {"Authorization": f"Bearer {buyer['token']}"}
    async with httpx.AsyncClient(base_url=os.environ["COMMERCE_HA_TEST_URL"], timeout=20) as http:

        async def api_ready() -> None:
            # 数据库选主完成后，网关还需连续健康检查才重新接流；恢复时间包含这一窗口。
            async with asyncio.timeout(60):
                while True:
                    response = await http.get("/api/v1/customer/profile", headers=headers)
                    if response.status_code == 200:
                        return
                    assert response.status_code == 503, response.text
                    await asyncio.sleep(0.5)

        async def quote() -> str:
            result = await http.post(
                "/api/v1/customer/quotes",
                headers=headers,
                json={"address_id": buyer["address_id"], "items": [{"sku_id": sku, "quantity": 1}]},
            )
            assert result.status_code == 201, result.text
            return result.json()["id"]

        async def place(quote_id: str, key: str):
            return await http.post(
                "/api/v1/customer/orders",
                json={"quote_id": quote_id},
                headers={**headers, "Idempotency-Key": key},
            )

        async def order():
            quote_id, key = await quote(), secrets.token_hex(16)
            result = await place(quote_id, key)
            assert result.status_code == 201, result.text
            return result.json(), quote_id, key

        async def payment_pending(order_id: str):
            async with asyncio.timeout(120):
                while True:
                    response = await http.get(
                        f"/api/v1/customer/orders/{order_id}/payment-session", headers=headers
                    )
                    if response.status_code == 200 and response.json()["status"] == "pending":
                        return response.json()
                    await asyncio.sleep(1)

        async def rabbit(path: str, node: str = "rabbitmq"):
            # 密码只从容器已有环境读取，不进入命令参数或 pytest 输出。
            return json.loads(
                await run(
                    "exec",
                    "-T",
                    "api",
                    "python",
                    "-c",
                    "import os,httpx; from urllib.parse import urlsplit; "
                    "u=urlsplit(os.environ['COMMERCE_AMQP_URL']); "
                    f"r=httpx.get('http://{node}:15672/api/{path}',auth=(u.username,u.password)); "
                    "r.raise_for_status(); print(r.text)",
                )
            )

        try:
            committed, quote_id, key = await order()
            await payment_pending(committed["id"])
            # 保存一条已确认提交的订单；旧主崩溃时还有另一笔未提交库存变动。
            original_stock = await sql(f"SELECT on_hand FROM stock_items WHERE sku_id='{sku}'")
            assert (
                await command(
                    "begin", sql=f"UPDATE stock_items SET on_hand=on_hand+7 WHERE sku_id='{sku}'"
                )
            )["ok"]
            old = await leader()
            assert old in {"pg-1", "pg-2", "pg-3"}
            observer = next(node for node in ("pg-1", "pg-2", "pg-3") if node != old)
            await run("stop", "-t", "0", "etcd-1")
            started = time.monotonic()
            await run("stop", "-t", "0", old)
            async with asyncio.timeout(90):
                while True:
                    new = await leader(observer)
                    if new and new != old:
                        recovered = await command(
                            "sql", sql="SELECT pg_is_in_recovery() AS replica"
                        )
                        if recovered["ok"] and not recovered["value"][0]["replica"]:
                            break
                    await asyncio.sleep(0.5)
            await command("rollback")
            assert (
                await sql(f"SELECT on_hand FROM stock_items WHERE sku_id='{sku}'") == original_stock
            )
            await api_ready()
            replay = await place(quote_id, key)
            assert replay.status_code in {200, 201}, replay.text
            assert replay.json()["id"] == committed["id"]
            report["postgres_failover"] = {
                "old": old,
                "new": new,
                "recovery_seconds": round(time.monotonic() - started, 3),
                "committed_order_preserved": True,
                "uncommitted_change_rolled_back": True,
                "same_idempotency_key_preserved": True,
                "one_etcd_down": True,
            }
            await run("up", "-d", "--no-deps", "--no-recreate", "--wait", "etcd-1", old)
            await healthy()
            async with asyncio.timeout(60):
                while True:
                    if (await patroni(old, "patroni"))["role"] == "replica":
                        break
                    await asyncio.sleep(0.5)
            await run("restart", old)
            await run("up", "-d", "--no-deps", "--wait", old)
            assert await leader(observer) == new
            report["postgres_old_primary_rejoined"] = True

            # 同步副本全部中断时，写事务不能立即返回成功；恢复后用同一键查回结果。
            quote_id = await quote()
            key = secrets.token_hex(16)
            replicas = [node for node in ("pg-1", "pg-2", "pg-3") if node != new]
            await run("stop", "-t", "0", *replicas)
            pending = asyncio.create_task(place(quote_id, key))
            await asyncio.sleep(2)
            assert not pending.done(), "strict sync acknowledged a write without any standby"
            waiting = await sql(
                "SELECT count(*) AS count FROM pg_stat_activity WHERE wait_event='SyncRep'"
            )
            assert waiting[0]["count"] > 0, "write did not reach synchronous replication wait"
            await run("start", *replicas)
            response = await pending
            assert response.status_code in {200, 201, 503}, response.text
            await healthy()
            await api_ready()
            response = await place(quote_id, key)
            assert response.status_code in {200, 201}, response.text
            report["strict_sync_blocks_without_replica"] = True

            # etcd 多数派丢失后旧主必须自动降级，恢复协调服务后才重新选主。
            await run("stop", "-t", "0", "etcd-2", "etcd-3")
            async with asyncio.timeout(60):
                while True:
                    result = await command("sql", sql="SELECT pg_is_in_recovery() AS replica")
                    if not result["ok"]:
                        break
                    await asyncio.sleep(1)
            assert (await http.get("/api/v1/customer/profile", headers=headers)).status_code == 503
            await run("up", "-d", "--no-deps", "--no-recreate", "--wait", "etcd-2", "etcd-3")
            await healthy()
            assert not (await sql("SELECT pg_is_in_recovery() AS replica"))[0]["replica"]
            await api_ready()
            report["etcd_quorum_loss_demotes_primary_and_recovers"] = True

            # 发布确认 + 未 ack 交付：停止真实队列 leader，已有连接重连后重投。
            assert (await command("publish", count=30))["value"] == 30
            unacked = await command("get", ack=False)
            assert unacked["ok"] and unacked["value"] is not None
            info = await rabbit("queues/commerce/ha.probe")
            assert len(info["members"]) == 3 and len(info["online"]) == 3
            old_broker = info["leader"].split("@", 1)[1]
            connections = await rabbit("connections")
            client_node = next(
                c["node"].split("@", 1)[1]
                for c in connections
                if c.get("client_properties", {}).get("connection_name") == "commerce-ha-probe"
            )
            assert client_node == old_broker
            started = time.monotonic()
            await run("stop", "-t", "0", old_broker)
            expected = {f"message-{index}" for index in range(30)}
            async with asyncio.timeout(90):
                while True:
                    result = await command("drain")
                    if result["ok"] and {m["body"] for m in result["value"]} == expected:
                        break
                    await asyncio.sleep(1)
            messages = result["value"]
            assert {m["body"] for m in messages} == expected
            assert any(m["body"] == unacked["value"]["body"] and m["redelivered"] for m in messages)
            report["rabbitmq_failover"] = {
                "old_leader": old_broker,
                "recovery_seconds": round(time.monotonic() - started, 3),
                "confirmed_messages_recovered": 30,
                "unacked_redelivered": True,
                "existing_connection_recovered": True,
            }
            await run("up", "-d", "--no-deps", "--no-recreate", "--wait", old_broker)
            await healthy()

            # 两节点下线失去 quorum，业务 outbox 保留；恢复后真实 relay/worker 补齐付款准备。
            await run("stop", "-t", "0", "rabbitmq-2", "rabbitmq-3")
            queued, _, _ = await order()
            await asyncio.sleep(7)
            pending_rows = await sql(
                "SELECT o.sent_at FROM outbox_events o "
                "JOIN async_operations op ON op.id=o.operation_id "
                "JOIN payment_intents p ON p.id=op.resource_id "
                f"WHERE p.order_id='{queued['id']}' AND op.kind='payment'"
            )
            assert pending_rows and all(row["sent_at"] is None for row in pending_rows)
            await run(
                "up", "-d", "--no-deps", "--no-recreate", "--wait", "rabbitmq-2", "rabbitmq-3"
            )
            await healthy()
            await payment_pending(queued["id"])
            # 模拟提供方实际付款，验证恢复后的业务链路与资金分录。
            intent = (await sql(f"SELECT id FROM payment_intents WHERE order_id='{queued['id']}'"))[
                0
            ]["id"]
            await run(
                "exec",
                "-T",
                "api",
                "python",
                "-c",
                "import os,httpx; "
                f"r=httpx.post('http://provider:8001/payments/{intent}/pay',"
                "headers={'Authorization':'Bearer '+os.environ['COMMERCE_PROVIDER_KEY']}); "
                "r.raise_for_status(); assert r.headers.get('X-Callback-Delivered') != 'false'",
            )
            entries = await sql(
                "SELECT count(*) AS count FROM financial_entries "
                f"WHERE order_id='{queued['id']}' AND kind='payment'"
            )
            assert entries == [{"count": 1}]
            response = await http.get(f"/api/v1/customer/orders/{queued['id']}", headers=headers)
            assert response.json()["status"] == "paid"
            report["rabbitmq_quorum_loss_outbox_recovery"] = True
            report["payment_after_recovery"] = "paid_once"

            # 切主后通过 libpq 多地址入口备份，再恢复到全新数据库。
            backup_args = [
                "--env-file",
                compose[compose.index("--env-file") + 1],
                "--project-name",
                compose[compose.index("--project-name") + 1],
                "--database-service",
                "db-tools",
            ]
            files = [compose[index + 1] for index, arg in enumerate(compose) if arg == "-f"]
            backup_args.extend(["--compose-file", files[0]])
            for overlay in files[1:]:
                backup_args.extend(["--compose-overlay", overlay])

            async def script(*args: str) -> str:
                process = await asyncio.create_subprocess_exec(
                    "uv",
                    "run",
                    "--no-sync",
                    "python",
                    *args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                async with asyncio.timeout(90):
                    stdout, stderr = await process.communicate()
                assert process.returncode == 0, stderr.decode()
                return stdout.decode().strip()

            backup = await script(
                "scripts/backup_local.py",
                *backup_args,
                "--database",
                "commerce_load_acceptance",
                "--destination",
                str(tmp_path),
                "--provider-path",
                str(tmp_path / "no-provider"),
                "--files-directory",
                str(tmp_path / "no-files"),
            )
            restored = "commerce_restore_ha_" + secrets.token_hex(6)
            await script(
                "scripts/restore_local.py",
                str(Path(backup) / "commerce.dump"),
                restored,
                *backup_args,
            )
            restored_paid = await run(
                "exec",
                "-T",
                "db-tools",
                "psql",
                "-d",
                restored,
                "-At",
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                "SELECT count(*) FROM financial_entries "
                f"WHERE order_id='{queued['id']}' AND kind='payment'",
            )
            assert restored_paid == "1"
            await run("exec", "-T", "db-tools", "dropdb", restored)
            report["postgres_backup_restore_after_failover"] = True
            report["result"] = "passed"
        finally:
            report.setdefault("result", "failed")
            await asyncio.to_thread(
                Path(os.environ["COMMERCE_HA_TEST_REPORT"]).write_text,
                json.dumps(report, indent=2) + "\n",
            )
            probe.stdin.write(b'{"action":"exit"}\n')
            await probe.stdin.drain()
            async with asyncio.timeout(15):
                await probe.wait()
