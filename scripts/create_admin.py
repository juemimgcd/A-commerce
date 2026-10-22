"""管理员幂等初始化；再次执行不会改密码、权限或覆盖原有业务数据。"""

import argparse
import asyncio
import getpass
import os

from sqlalchemy import select

from a_commerce.conf.resources import resources
from a_commerce.conf.settings import Settings
from a_commerce.models.entities import Staff
from a_commerce.schemas.api import Credentials
from a_commerce.services.identity import PASSWORDS


async def create(email: str, password: str, permissions: list[str]) -> None:
    data = Credentials(email=email, password=password)
    digest = await asyncio.to_thread(PASSWORDS.hash, data.password.get_secret_value())
    async with resources(Settings()) as r, r.sessions.begin() as session:
        existing = await session.scalar(select(Staff).where(Staff.email == str(data.email).lower()))
        if existing:
            print("管理员已存在；未修改密码或权限。")
            return
        session.add(
            Staff(email=str(data.email).lower(), password_hash=digest, permissions=permissions)
        )
    print("管理员已创建。")


def main() -> None:
    parser = argparse.ArgumentParser(description="初始化管理员；密码不接收命令行参数")
    parser.add_argument("email")
    parser.add_argument(
        "--permissions",
        nargs="+",
        choices=["catalog", "fulfillment", "aftersales", "audit", "identity"],
        default=["catalog", "fulfillment", "aftersales", "audit", "identity"],
    )
    args = parser.parse_args()
    password = os.environ.get("COMMERCE_ADMIN_PASSWORD") or getpass.getpass(
        "管理员密码（至少12字符）："
    )
    asyncio.run(create(args.email, password, args.permissions))


if __name__ == "__main__":
    main()
