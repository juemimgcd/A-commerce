"""共用店铺授权；锁顺序为账号、会话、店铺、业务资源。"""

from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a_commerce.errors import CommerceError, missing
from a_commerce.models.base import now
from a_commerce.models.entities import Audit, Session, Shop
from a_commerce.services.identity import Principal, authorize


async def scope(
    session: AsyncSession,
    p: Principal,
    shop_id: UUID | None,
    permission: str,
    *,
    write: bool = False,
    catalog: bool = False,
    sensitive: bool = False,
) -> Shop | None:
    await authorize(
        session, p, permission if shop_id is None or p.kind == "staff" else None, lock=write
    )
    if shop_id is None:
        return None
    query = select(Shop).where(Shop.id == shop_id)
    if write:
        query = query.with_for_update(read=True)
    shop = await session.scalar(query)
    if shop is None or (p.kind != "staff" and shop.owner_user_id != p.id):
        raise missing()
    if write and shop.status == "closed":
        raise CommerceError("SHOP_CLOSED", "店铺已关闭，只能查看历史记录。", 409)
    if catalog and shop.status != "active":
        raise CommerceError("SHOP_SUSPENDED", "当前店铺不能新增商品、改价或调整库存。", 409)
    if sensitive and p.kind == "customer":
        token = await session.get(Session, p.session_id)
        if token is None or token.created_at < now() - timedelta(minutes=15):
            raise CommerceError("RECENT_AUTH_REQUIRED", "该操作需要在 15 分钟内重新登录。", 401)
    return shop


def audit(p: Principal, shop_id: UUID, action: str, resource_id: UUID, detail: str) -> Audit:
    return Audit(
        actor_id=p.id,
        actor_kind=p.kind,
        shop_id=shop_id,
        action=action,
        resource_id=resource_id,
        detail=detail,
    )
