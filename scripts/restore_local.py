"""将 PostgreSQL 备份恢复到全新隔离库；拒绝已有库，不修改业务库或提供方文件。"""

import argparse
import re
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", type=Path)
    parser.add_argument("database", help="必须以 commerce_restore_ 开头")
    args = parser.parse_args()
    if not re.fullmatch(r"commerce_restore_[a-z0-9_]{1,40}", args.database):
        parser.error("目标必须是独立的 commerce_restore_ 开头数据库")
    if not args.backup.is_file():
        parser.error("备份文件不存在")
    command = [
        "docker",
        "compose",
        "--env-file",
        ".env",
        "-f",
        "deploy/compose.yaml",
        "exec",
        "-T",
        "db",
    ]
    # createdb 在名称已存在时失败；没有 --clean，也不在失败后自动删除恢复现场。
    subprocess.run(
        [*command, "createdb", "-U", "commerce", "-T", "template0", args.database], check=True
    )
    with args.backup.open("rb") as source:
        subprocess.run(
            [
                *command,
                "pg_restore",
                "-U",
                "commerce",
                "--exit-on-error",
                "--no-owner",
                "-d",
                args.database,
            ],
            stdin=source,
            check=True,
        )
    subprocess.run(
        [
            *command,
            "psql",
            "-U",
            "commerce",
            "-d",
            args.database,
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            "SELECT version_num FROM alembic_version",
        ],
        check=True,
    )
    print(f"已恢复到隔离数据库 {args.database}；原业务库未变动。")


if __name__ == "__main__":
    main()
