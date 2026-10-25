"""负载工具的小规模完整路径验证，不作为容量或长稳成绩。"""

import asyncio
import os
import secrets
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import update
from sqlalchemy.engine import make_url

from a_commerce.models.entities import Stock
from scripts.load_backend import Dataset, LoadConfig, LoadProduct, LoadUser, run_load
from scripts.seed_load import seed
from tests.conftest import Backend

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("mode", ["users", "arrival"])
async def test_load_tool_with_real_relay_worker_and_simulated_payment(
    backend: Backend, tmp_path: Path, mode: str
) -> None:
    async with backend.r.sessions.begin() as session:
        await session.execute(update(Stock).values(on_hand=10000))
    data = Dataset(
        users=[
            LoadUser(token=buyer.token, address_id=buyer.address_id)
            for buyer in (backend.buyer, backend.other)
        ],
        products=[LoadProduct(product_id=backend.product_id, sku_id=backend.sku_ids[0])],
    )
    logs = []
    processes = []
    try:
        for module in ("a_commerce.workers.main", "a_commerce.dispatch.main"):
            log = (tmp_path / f"{module}.log").open("wb")
            logs.append(log)
            processes.append(
                await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    module,
                    cwd=tmp_path,
                    env=dict(os.environ),
                    stdout=log,
                    stderr=log,
                )
            )
        report = await run_load(
            LoadConfig(
                base_url=str(backend.client.base_url),
                mode=mode,
                users=2,
                duration=2,
                arrival_rate=0.5,
                think_min=0.01,
                think_max=0.01,
                browse_steps=1,
                order_fraction=1,
                pay_fraction=1,
            ),
            data,
        )
        assert report["business"]["orders_created"] >= 1
        assert report["business"]["payments_confirmed"] == report["business"]["orders_created"]
        assert report["business"].get("payment_unresolved", 0) == 0
        assert report["status_counts"].get("500", 0) == 0
        assert report["dropped_journeys"] == 0
        assert report["maximum_active_users"] <= 2
        assert report["rps"] > 0
    finally:
        for process in processes:
            if process.returncode is None:
                process.terminate()
        for process in processes:
            async with asyncio.timeout(40):
                await process.wait()
        for log in logs:
            log.close()


async def test_seed_requires_an_empty_explicit_load_database(
    backend: Backend, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COMMERCE_LOAD_ISOLATED", "1")
    with pytest.raises(ValueError, match="commerce_load_"):
        await seed(2, 3, tmp_path / "forbidden.json")
    assert not (tmp_path / "forbidden.json").exists()
    database = f"commerce_load_{secrets.token_hex(8)}"
    root = ROOT
    command = [
        "docker",
        "compose",
        "--env-file",
        os.environ["COMMERCE_TEST_ENV_FILE"],
        "-f",
        str(root / "deploy/compose.test.yaml"),
        "--project-name",
        os.environ["COMMERCE_TEST_PROJECT"],
        "exec",
        "-T",
        "db",
    ]
    await asyncio.to_thread(
        subprocess.run,
        [*command, "createdb", "-U", "commerce", "-T", "template0", database],
        check=True,
    )
    try:
        target = make_url(backend.r.settings.database_url.get_secret_value()).set(database=database)
        monkeypatch.setenv("COMMERCE_DATABASE_URL", target.render_as_string(hide_password=False))
        await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=root,
            check=True,
        )
        destination = tmp_path / "load-dataset.json"
        await seed(2, 3, destination)
        dataset = Dataset.model_validate_json(destination.read_text())
        assert len(dataset.users) == 2
        assert dataset.users[0].token != dataset.users[1].token
        assert destination.stat().st_mode & 0o777 == 0o600
        with pytest.raises(ValueError, match="必须为空"):
            await seed(2, 3, tmp_path / "second.json")
    finally:
        await asyncio.to_thread(
            subprocess.run, [*command, "dropdb", "-U", "commerce", database], check=True
        )
