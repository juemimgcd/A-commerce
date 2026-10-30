"""平台开店、店铺状态和店主资料维护。"""

from datetime import timedelta
from uuid import UUID

from sqlalchemy import Integer, cast, select

from a_commerce.conf.resources import Resources
from a_commerce.errors import CommerceError, missing
from a_commerce.models.base import now
from a_commerce.models.entities import (
    Operation,
    Order,
    PaymentIntent,
    Product,
    Refund,
    ReturnRequest,
    Shop,
    User,
)
from a_commerce.schemas.merchant import ShopEdit, ShopIn, ShopOut, ShopStatus
from a_commerce.services.catalog import record_change
from a_commerce.services.identity import Principal, authorize
from a_commerce.services.shop_access import audit


async def list_shops(r: Resources, p: Principal, *, admin: bool = False) -> list[ShopOut]:
    async with r.sessions() as session:
        await authorize(session, p, "identity" if admin else None)
        query = select(Shop)
        if not admin:
            query = query.where(Shop.owner_user_id == p.id)
        return [
            ShopOut.model_validate(s)
            for s in await session.scalars(query.order_by(Shop.id).limit(100))
        ]


async def create(r: Resources, p: Principal, data: ShopIn) -> ShopOut:
    async with r.sessions.begin() as session:
        await authorize(session, p, "identity", lock=True)
        owner = await session.scalar(
            select(User).where(User.email == str(data.owner_email).lower())
        )
        if owner is None or not owner.active:
            raise missing()
        shop = Shop(
            owner_user_id=owner.id,
            name=data.name,
            status="suspended",
            return_address=data.return_address.model_dump(),
            updated_at=now(),
        )
        session.add(shop)
        await session.flush()
        session.add(audit(p, shop.id, "shop.create", shop.id, "新增店铺，待平台启用"))
        return ShopOut.model_validate(shop)


async def edit(r: Resources, p: Principal, shop_id: UUID, data: ShopEdit) -> ShopOut:
    async with r.sessions.begin() as session:
        await authorize(session, p, "identity" if p.kind == "staff" else None, lock=True)
        shop = await session.scalar(select(Shop).where(Shop.id == shop_id).with_for_update())
        if shop is None or (p.kind != "staff" and shop.owner_user_id != p.id):
            raise missing()
        if shop.status == "closed":
            raise CommerceError("SHOP_CLOSED", "店铺已关闭，只能查看历史记录。", 409)
        if shop.version != data.expected_version:
            raise CommerceError("VERSION_CONFLICT", "店铺已变化，请刷新。", 409)
        shop.name, shop.return_address = data.name, data.return_address.model_dump()
        shop.version += 1
        shop.updated_at = now()
        session.add(audit(p, shop.id, "shop.edit", shop.id, "维护店铺资料"))
        return ShopOut.model_validate(shop)


async def set_status(r: Resources, p: Principal, shop_id: UUID, data: ShopStatus) -> ShopOut:
    async with r.sessions.begin() as session:
        await authorize(session, p, "identity", lock=True)
        shop = await session.scalar(select(Shop).where(Shop.id == shop_id).with_for_update())
        if shop is None:
            raise missing()
        if shop.version != data.expected_version or (
            shop.status == "closed" and data.status != "closed"
        ):
            raise CommerceError("SHOP_STATE_CONFLICT", "店铺状态或版本不允许此操作。", 409)
        if data.status == "active":
            owner = await session.get(User, shop.owner_user_id)
            if owner is None or not owner.active or not shop.return_address:
                raise CommerceError("SHOP_NOT_READY", "请先启用店主账号并配置退货地址。", 409)
        if data.status == "closed":
            current = now()
            pending_order = await session.scalar(
                select(Order.id)
                .where(
                    Order.shop_id == shop_id,
                    (Order.status.in_(["pending_payment", "closing"]))
                    | (
                        (Order.status == "paid")
                        & (
                            (Order.fulfillment_status != "delivered")
                            | Order.delivered_at.is_(None)
                            | (
                                Order.delivered_at
                                + cast(Order.policy["return_days"].astext, Integer)
                                * timedelta(days=1)
                                >= current
                            )
                        )
                    ),
                )
                .limit(1)
            )
            pending_return = await session.scalar(
                select(ReturnRequest.id)
                .join(Order)
                .where(
                    Order.shop_id == shop_id, ReturnRequest.status.not_in(["rejected", "inspected"])
                )
                .limit(1)
            )
            pending_refund = await session.scalar(
                select(Refund.id)
                .join(Order)
                .where(Order.shop_id == shop_id, Refund.status != "succeeded")
                .limit(1)
            )
            order_ids = select(Order.id).where(Order.shop_id == shop_id)
            resources = select(PaymentIntent.id).where(PaymentIntent.order_id.in_(order_ids))
            refund_ids = select(Refund.id).where(Refund.order_id.in_(order_ids))
            pending_task = await session.scalar(
                select(Operation.id)
                .where(
                    Operation.status != "done",
                    (
                        (Operation.kind.in_(["payment", "close"]))
                        & Operation.resource_id.in_(resources)
                    )
                    | ((Operation.kind == "refund") & Operation.resource_id.in_(refund_ids)),
                )
                .limit(1)
            )
            if pending_order or pending_return or pending_refund or pending_task:
                raise CommerceError(
                    "SHOP_HAS_PENDING_WORK",
                    "店铺仍有未完成订单、有效售后期或异步任务，不能关闭。",
                    409,
                )
        shop.status, shop.updated_at = data.status, now()
        shop.version += 1
        # 更新公开快照并走原有缓存/搜索事件链；下单始终读主库店铺状态。
        for product in await session.scalars(
            select(Product).where(Product.shop_id == shop_id).order_by(Product.id).with_for_update()
        ):
            product.version += 1
            await record_change(session, product)
        session.add(audit(p, shop_id, "shop.status", shop_id, data.reason))
        return ShopOut.model_validate(shop)
