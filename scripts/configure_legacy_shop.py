"""首次绑定历史自营店铺的店主；不修改密码，不启用交易，不支持转让。"""

import argparse
import asyncio

from pydantic import EmailStr, TypeAdapter
from sqlalchemy import select

from a_commerce.conf.resources import resources
from a_commerce.conf.settings import Settings
from a_commerce.models.base import now
from a_commerce.models.entities import Audit, Shop, User
from a_commerce.models.shops import LEGACY_OWNER_ID, LEGACY_SHOP_ID


async def configure(email: str) -> None:
    normalized = str(TypeAdapter(EmailStr).validate_python(email)).lower()
    async with resources(Settings()) as r, r.sessions.begin() as session:
        owner = await session.scalar(select(User).where(User.email == normalized))
        if owner is None or not owner.active:
            raise ValueError("请先使用普通注册入口创建并核验有效店主账号")
        shop = await session.scalar(select(Shop).where(Shop.id == LEGACY_SHOP_ID).with_for_update())
        if shop is None:
            raise ValueError("历史店铺不存在，请先执行迁移")
        if shop.owner_user_id == owner.id:
            print("店主已经绑定，未修改店铺。")
            return
        if shop.owner_user_id != LEGACY_OWNER_ID or shop.status != "suspended":
            raise ValueError("历史店铺已经配置或启用，不允许转让")
        shop.owner_user_id = owner.id
        shop.version += 1
        shop.updated_at = now()
        session.add(
            Audit(
                actor_kind="operator",
                shop_id=shop.id,
                action="shop.bootstrap",
                resource_id=shop.id,
                detail="首次绑定有效自营账号，保持暂停状态",
            )
        )
    print("历史店主已绑定。请由平台核对资料、资金和库存后启用店铺。")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("owner_email")
    asyncio.run(configure(parser.parse_args().owner_email))


if __name__ == "__main__":
    main()
