"""运营查询与员工维护：分页读取历史，敏感权限变更撤销旧会话。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field
from sqlalchemy import select, union_all, update

from a_commerce.conf.resources import Resources
from a_commerce.crud.queries import return_outputs
from a_commerce.errors import CommerceError, missing
from a_commerce.models.entities import (
    Audit,
    CatalogChange,
    Event,
    EventArchive,
    Order,
    Refund,
    ReturnRequest,
    Session,
    Staff,
    StockMovement,
)
from a_commerce.schemas.api import ReturnOut, Schema
from a_commerce.services.identity import Principal, authorize


class StaffOut(Schema):
    id: UUID
    email: str
    active: bool
    permissions: list[str]


class StaffUpdate(Schema):
    active: bool
    permissions: list[Literal["catalog", "fulfillment", "aftersales", "audit", "identity"]] = Field(
        min_length=1, max_length=5
    )
    reason: str = Field(min_length=1, max_length=500)


class RefundOut(Schema):
    id: UUID
    order_id: UUID
    amount_fen: int
    status: str
    created_at: datetime
    confirmed_at: datetime | None


class MovementOut(Schema):
    id: UUID
    sku_id: UUID
    reference: str
    on_hand_delta: int
    reserved_delta: int
    allocated_delta: int
    reason: str
    created_at: datetime


class ChangeOut(Schema):
    id: UUID
    version: int
    snapshot: dict[str, object]
    created_at: datetime


class HistoryOut(Schema):
    sequence: int
    kind: str
    order_version: int
    occurred_at: datetime


async def staff_list(r: Resources, p: Principal, cursor: UUID | None, limit: int) -> list[StaffOut]:
    async with r.sessions() as session:
        await authorize(session, p, "identity")
        query = select(Staff)
        if cursor:
            query = query.where(Staff.id > cursor)
        return [
            StaffOut.model_validate(row)
            for row in await session.scalars(query.order_by(Staff.id).limit(limit))
        ]


async def update_staff(r: Resources, p: Principal, staff_id: UUID, data: StaffUpdate) -> StaffOut:
    async with r.sessions.begin() as session:
        # 单店员工数量很小：先按固定顺序锁员工集合，避免两个管理员同时删除最后一份身份权限。
        staff = list(await session.scalars(select(Staff).order_by(Staff.id).with_for_update()))
        await authorize(session, p, "identity", lock=True)
        target = next((row for row in staff if row.id == staff_id), None)
        if target is None:
            raise missing()
        if not (data.active and "identity" in data.permissions) and not any(
            row.id != target.id and row.active and "identity" in row.permissions for row in staff
        ):
            raise CommerceError("LAST_ADMIN", "必须保留至少一个可管理身份的有效管理员。")
        target.active, target.permissions = data.active, sorted(set(data.permissions))
        await session.execute(
            update(Session).where(Session.staff_id == target.id).values(revoked=True)
        )
        session.add(
            Audit(actor_id=p.id, action="staff.update", resource_id=target.id, detail=data.reason)
        )
        return StaffOut.model_validate(target)


async def returns(
    r: Resources,
    p: Principal,
    status: str | None,
    cursor: UUID | None,
    limit: int,
    *,
    customer: bool = False,
) -> list[ReturnOut]:
    async with r.sessions() as session:
        await authorize(session, p, None if customer else "aftersales")
        query = select(ReturnRequest)
        if customer:
            query = query.where(ReturnRequest.user_id == p.id)
        if status:
            query = query.where(ReturnRequest.status == status)
        if cursor:
            query = query.where(ReturnRequest.id > cursor)
        rows = list(await session.scalars(query.order_by(ReturnRequest.id).limit(limit)))
        return await return_outputs(session, rows, r.settings.shop.return_address)


async def refunds(
    r: Resources, p: Principal, status: str | None, cursor: UUID | None, limit: int
) -> list[RefundOut]:
    async with r.sessions() as session:
        await authorize(session, p, "aftersales")
        query = select(Refund)
        if status:
            query = query.where(Refund.status == status)
        if cursor:
            query = query.where(Refund.id > cursor)
        return [
            RefundOut.model_validate(row)
            for row in await session.scalars(query.order_by(Refund.id).limit(limit))
        ]


async def movements(
    r: Resources, p: Principal, sku_id: UUID, cursor: UUID | None, limit: int
) -> list[MovementOut]:
    async with r.sessions() as session:
        await authorize(session, p, "catalog")
        query = select(StockMovement).where(StockMovement.sku_id == sku_id)
        if cursor:
            query = query.where(StockMovement.id > cursor)
        return [
            MovementOut.model_validate(row)
            for row in await session.scalars(query.order_by(StockMovement.id).limit(limit))
        ]


async def changes(
    r: Resources, p: Principal, product_id: UUID, after: int, limit: int
) -> list[ChangeOut]:
    async with r.sessions() as session:
        await authorize(session, p, "catalog")
        return [
            ChangeOut.model_validate(row)
            for row in await session.scalars(
                select(CatalogChange)
                .where(CatalogChange.product_id == product_id, CatalogChange.version > after)
                .order_by(CatalogChange.version)
                .limit(limit)
            )
        ]


async def history(
    r: Resources,
    p: Principal,
    order_id: UUID,
    after: int,
    limit: int,
    *,
    customer: bool = False,
) -> list[HistoryOut]:
    async with r.sessions() as session:
        await authorize(session, p, None if customer else "audit")
        if customer:
            order = await session.get(Order, order_id)
            if order is None or order.user_id != p.id:
                raise missing()
        # 单条 UNION 查询使用同一语句快照，归档移动不会让同一事件重复或消失。
        query = union_all(
            select(
                Event.sequence,
                Event.kind,
                Event.order_version,
                Event.created_at.label("occurred_at"),
            ).where(Event.order_id == order_id, Event.sequence > after),
            select(
                EventArchive.sequence,
                EventArchive.kind,
                EventArchive.order_version,
                EventArchive.occurred_at,
            ).where(EventArchive.order_id == order_id, EventArchive.sequence > after),
        ).subquery()
        rows = await session.execute(select(query).order_by(query.c.sequence).limit(limit))
        return [HistoryOut.model_validate(dict(row._mapping)) for row in rows]
