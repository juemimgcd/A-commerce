"""备份本项目 Compose 数据库与独立模拟支付库；业务进程应先停止，避免跨库时间差。"""

import argparse
import shutil
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=Path("var/backups"))
    args = parser.parse_args()
    destination = args.destination / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination.mkdir(parents=True, mode=0o700)
    database = destination / "commerce.dump"
    database.touch(mode=0o600)
    with database.open("wb") as output:
        subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                ".env",
                "-f",
                "deploy/compose.yaml",
                "exec",
                "-T",
                "db",
                "pg_dump",
                "-U",
                "commerce",
                "-d",
                "commerce",
                "-Fc",
            ],
            stdout=output,
            check=True,
        )
    provider = Path("var/provider.sqlite3")
    if provider.exists():
        target = destination / "provider.sqlite3"
        target.touch(mode=0o600)
        # SQLite backup API 同时覆盖 WAL 中已提交的数据，不能只复制主文件。
        with sqlite3.connect(f"file:{provider.resolve()}?mode=ro", uri=True) as source:
            with sqlite3.connect(target) as backup:
                source.backup(backup)
    if Path("var/files").exists():
        shutil.copytree("var/files", destination / "files")
    print(destination.resolve())


if __name__ == "__main__":
    main()
