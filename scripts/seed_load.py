"""只向独立 commerce_load_* 库写压测数据；生成的会话清单按 0600 保存。"""

import argparse
import asyncio
import hashlib
import json
import os
import re
import secrets
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import insert, select
from sqlalchemy.engine import make_url

from a_commerce.conf.resources import resources
from a_commerce.conf.settings import Settings
from a_commerce.models.base import now
from a_commerce.models.entities import SKU, Address, Category, Product, Session, Stock, User
from a_commerce.services.identity import DUMMY_HASH


async def seed(users: int, products: int, destination: Path) -> None:
    settings = Settings()
    database = make_url(settings.database_url.get_secret_value()).database or ""
    if (
        settings.environment != "development"
        or os.environ.get("COMMERCE_LOAD_ISOLATED") != "1"
        or not re.fullmatch(r"commerce_load_[a-z0-9_]{1,40}", database)
    ):
        raise ValueError(
            "仅允许显式 COMMERCE_LOAD_ISOLATED=1 的独立 development commerce_load_* 库"
        )
    # 先独占创建文件，再写数据库，防止覆盖既有会话清单。
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        async with resources(settings) as r:
            async with r.sessions.begin() as session:
                if await session.scalar(select(User.id).limit(1)) is not None:
                    raise ValueError("压测库必须为空，不向既有用户库追加数据")
                category_id = uuid4()
                await session.execute(
                    insert(Category), [{"id": category_id, "name": "负载验证目录"}]
                )
                catalog: list[dict[str, str]] = []
                for start in range(0, products, 1000):
                    product_rows, sku_rows, stocks = [], [], []
                    for index in range(start, min(start + 1000, products)):
                        product_id, sku_id = uuid4(), uuid4()
                        catalog.append({"product_id": str(product_id), "sku_id": str(sku_id)})
                        product_rows.append(
                            {
                                "id": product_id,
                                "category_id": category_id,
                                "title": f"压测商品 {index}",
                                "active": True,
                            }
                        )
                        sku_rows.append(
                            {
                                "id": sku_id,
                                "product_id": product_id,
                                "code": f"LOAD-{index}",
                                "price_fen": 2500,
                                "attributes": {},
                            }
                        )
                        stocks.append({"sku_id": sku_id, "on_hand": 10000000})
                    await session.execute(insert(Product), product_rows)
                    await session.execute(insert(SKU), sku_rows)
                    await session.execute(insert(Stock), stocks)
                actors: list[dict[str, str]] = []
                for start in range(0, users, 1000):
                    accounts, sessions, addresses = [], [], []
                    for index in range(start, min(start + 1000, users)):
                        user_id, address_id, token = uuid4(), uuid4(), secrets.token_urlsafe(32)
                        accounts.append(
                            {
                                "id": user_id,
                                "email": f"load-{index}@example.com",
                                "password_hash": DUMMY_HASH,
                            }
                        )
                        sessions.append(
                            {
                                "user_id": user_id,
                                "token_hash": hashlib.sha256(token.encode()).hexdigest(),
                                "expires_at": now() + timedelta(hours=12),
                            }
                        )
                        addresses.append(
                            {
                                "id": address_id,
                                "user_id": user_id,
                                "recipient": "负载验证",
                                "phone": "12345678",
                                "region": "测试区域",
                                "detail": "测试地址",
                            }
                        )
                        actors.append({"token": token, "address_id": str(address_id)})
                    await session.execute(insert(User), accounts)
                    await session.execute(insert(Session), sessions)
                    await session.execute(insert(Address), addresses)
            # 事务成功后输出；中断时留下空文件而不是可用但对应未提交数据的凭证。
            json.dump(
                {"users": actors, "products": catalog},
                output,
            )
            output.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--users", type=int, default=100)
    parser.add_argument("--products", type=int, default=1000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.users <= 10000 or not 1 <= args.products <= 100000:
        parser.error("users 应在 1..10000，products 应在 1..100000")
    asyncio.run(seed(args.users, args.products, args.output))
    print(f"Private dataset: {args.output.resolve()}")


if __name__ == "__main__":
    main()
