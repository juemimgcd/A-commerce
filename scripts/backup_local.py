"""备份本项目 Compose 数据库与独立模拟支付库；业务进程应先停止，避免跨库时间差。"""

import argparse
import shutil
import sqlite3
import subprocess
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=Path("var/backups"))
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--compose-file", default="deploy/compose.yaml")
    parser.add_argument("--compose-overlay", action="append", default=[])
    parser.add_argument("--database-service", default="db")
    parser.add_argument("--project-name")
    parser.add_argument("--database", default="commerce")
    parser.add_argument("--provider-path", type=Path, default=Path("var/provider.sqlite3"))
    parser.add_argument("--files-directory", type=Path, default=Path("var/files"))
    args = parser.parse_args()
    command = ["docker", "compose", "--env-file", args.env_file, "-f", args.compose_file]
    for overlay in args.compose_overlay:
        command.extend(["-f", overlay])
    if args.project_name:
        command.extend(["--project-name", args.project_name])
    destination = args.destination / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination.mkdir(parents=True, mode=0o700)
    database = destination / "commerce.dump"
    database.touch(mode=0o600)
    with database.open("wb") as output:
        subprocess.run(
            [
                *command,
                "exec",
                "-T",
                args.database_service,
                "pg_dump",
                "-U",
                "commerce",
                "-d",
                args.database,
                "-Fc",
            ],
            stdout=output,
            check=True,
        )
    provider = args.provider_path
    if provider.exists():
        target = destination / "provider.sqlite3"
        target.touch(mode=0o600)
        # SQLite backup API 同时覆盖 WAL 中已提交的数据，不能只复制主文件。
        with closing(sqlite3.connect(f"file:{provider.resolve()}?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(target)) as backup:
                source.backup(backup)
    if args.files_directory.exists():
        shutil.copytree(args.files_directory, destination / "files")
    print(destination.resolve())


if __name__ == "__main__":
    main()
