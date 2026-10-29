"""调用实际备份/恢复脚本，在新数据库核对交易、资金、库存和 SQLite WAL 事实。"""

import asyncio
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import Backend

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[1]


async def test_backup_and_restore_round_trip(backend: Backend, tmp_path: Path) -> None:
    project = os.environ["COMMERCE_TEST_PROJECT"]
    assert project.startswith("commerce-test-")
    migration_head = ScriptDirectory.from_config(
        Config(str(ROOT / "alembic.ini"))
    ).get_current_head()
    order = await backend.pay(await backend.order(quantity=2))
    files = backend.r.settings.files_directory
    files.mkdir(parents=True, exist_ok=True)
    (files / "evidence.txt").write_text("backup evidence")
    options = [
        "--env-file",
        os.environ["COMMERCE_TEST_ENV_FILE"],
        "--compose-file",
        str(ROOT / "deploy/compose.test.yaml"),
        "--project-name",
        project,
    ]
    backup = await asyncio.to_thread(
        subprocess.check_output,
        [
            sys.executable,
            str(ROOT / "scripts/backup_local.py"),
            *options,
            "--database",
            "commerce_test",
            "--destination",
            str(tmp_path / "backups"),
            "--provider-path",
            os.environ["SIMULATOR_DATABASE_PATH"],
            "--files-directory",
            str(files),
        ],
        text=True,
        cwd=ROOT,
    )
    destination = Path(backup.strip())
    assert (destination / "files/evidence.txt").read_text() == "backup evidence"
    with closing(sqlite3.connect(destination / "provider.sqlite3")) as connection:
        assert (
            connection.execute("SELECT SUM(amount) FROM money_ledger").fetchone()[0]
            == order["total_fen"]
        )
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    restore_name = f"commerce_restore_{uuid4().hex}"
    try:
        await asyncio.to_thread(
            subprocess.run,
            [
                sys.executable,
                str(ROOT / "scripts/restore_local.py"),
                str(destination / "commerce.dump"),
                restore_name,
                *options,
            ],
            check=True,
            cwd=ROOT,
            capture_output=True,
        )
        target = make_url(backend.r.settings.database_url.get_secret_value()).set(
            database=restore_name
        )
        engine = create_async_engine(target)
        try:
            async with engine.connect() as connection:
                row = (
                    await connection.execute(
                        text("SELECT status, total_fen FROM orders WHERE id = :id"),
                        {"id": order["id"]},
                    )
                ).one()
                assert tuple(row) == ("paid", order["total_fen"])
                assert (
                    await connection.scalar(text("SELECT SUM(amount_fen) FROM financial_entries"))
                    == order["total_fen"]
                )
                assert await connection.scalar(text("SELECT SUM(allocated) FROM stock_items")) == 2
                assert (
                    await connection.scalar(text("SELECT version_num FROM alembic_version"))
                    == migration_head
                )
        finally:
            await engine.dispose()
        # 已有恢复库必须拒绝，不能静默覆盖。
        repeated = await asyncio.to_thread(
            subprocess.run,
            [
                sys.executable,
                str(ROOT / "scripts/restore_local.py"),
                str(destination / "commerce.dump"),
                restore_name,
                *options,
            ],
            cwd=ROOT,
            capture_output=True,
        )
        assert repeated.returncode != 0
    finally:
        await asyncio.to_thread(
            subprocess.run,
            [
                "docker",
                "compose",
                "--env-file",
                os.environ["COMMERCE_TEST_ENV_FILE"],
                "-f",
                str(ROOT / "deploy/compose.test.yaml"),
                "--project-name",
                project,
                "exec",
                "-T",
                "db",
                "dropdb",
                "-U",
                "commerce",
                restore_name,
            ],
            check=True,
            cwd=ROOT,
            capture_output=True,
        )
