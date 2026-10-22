"""管理用例：异常任务核对后重放，保留执行版本和审计证据。"""

import asyncio
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import Field
from sqlalchemy import select, update

from a_commerce.conf.resources import Resources
from a_commerce.errors import CommerceError, missing
from a_commerce.models.base import now
from a_commerce.models.entities import (
    Audit,
    MessageFailure,
    Operation,
    Outbox,
    Session,
    Staff,
    Stock,
    User,
)
from a_commerce.schemas.api import AccountOut, Credentials, OperationOut, Schema, StockOut
from a_commerce.services.identity import PASSWORDS, Principal, authorize
from a_commerce.services.operations import PENDING_REDISPATCH_SECONDS


class StaffIn(Credentials):
    permissions: list[Literal["catalog", "fulfillment", "aftersales", "audit", "identity"]] = Field(
        min_length=1, max_length=5
    )


class AuditOut(Schema):
    id: UUID
    actor_id: UUID | None
    action: str
    resource_id: UUID
    detail: str
    created_at: datetime


class FailureOut(Schema):
    id: UUID
    message_id: str | None
    reason: str
    created_at: datetime


async def create_staff(r: Resources, p: Principal, data: StaffIn) -> AccountOut:
    async with r.sessions() as session:
        await authorize(session, p, "identity")
    async with asyncio.timeout(5), r.password_slots:
        digest = await asyncio.to_thread(PASSWORDS.hash, data.password.get_secret_value())
    async with r.sessions.begin() as session:
        await authorize(session, p, "identity", lock=True)
        staff = Staff(
            email=str(data.email).lower(),
            password_hash=digest,
            permissions=sorted(set(data.permissions)),
        )
        session.add(staff)
        await session.flush()
        session.add(
            Audit(
                actor_id=p.id,
                action="staff.create",
                resource_id=staff.id,
                detail=",".join(staff.permissions),
            )
        )
        return AccountOut.model_validate(staff)


async def audits(r: Resources, p: Principal, cursor: UUID | None, limit: int) -> list[AuditOut]:
    async with r.sessions() as session:
        await authorize(session, p, "audit")
        query = select(Audit)
        if cursor:
            query = query.where(Audit.id > cursor)
        return [
            AuditOut.model_validate(row)
            for row in await session.scalars(query.order_by(Audit.id).limit(limit))
        ]


async def failures(r: Resources, p: Principal, limit: int) -> list[FailureOut]:
    async with r.sessions() as session:
        await authorize(session, p, "audit")
        return [
            FailureOut.model_validate(row)
            for row in await session.scalars(
                select(MessageFailure).order_by(MessageFailure.created_at.desc()).limit(limit)
            )
        ]


async def low_stock(r: Resources, p: Principal, threshold: int, limit: int) -> list[StockOut]:
    async with r.sessions() as session:
        await authorize(session, p, "catalog")
        return [
            StockOut.model_validate(row)
            for row in await session.scalars(
                select(Stock)
                .where(Stock.on_hand - Stock.reserved - Stock.allocated <= threshold)
                .order_by(Stock.sku_id)
                .limit(limit)
            )
        ]


async def operations(r: Resources, p: Principal, status: str, limit: int) -> list[OperationOut]:
    async with r.sessions() as session:
        await authorize(session, p, "audit")
        rows = await session.scalars(
            select(Operation)
            .where(Operation.status == status)
            .order_by(Operation.next_attempt_at, Operation.id)
            .limit(limit)
        )
        return [OperationOut.model_validate(row) for row in rows]


async def replay(r: Resources, p: Principal, operation_id: UUID, reason: str) -> OperationOut:
    async with r.sessions.begin() as session:
        await authorize(session, p, "audit", lock=True)
        operation = await session.scalar(
            select(Operation).where(Operation.id == operation_id).with_for_update()
        )
        if operation is None:
            raise missing()
        if operation.status != "manual_review":
            raise CommerceError("OPERATION_STATE_CONFLICT", "只能重放已经进入人工处理的操作。")
        # 不换业务 ID。支付/退款 handler 仍先向提供方查询，重放不能跳过资金核对。
        operation.status = "pending"
        operation.fence += 1
        operation.dispatch_version += 1
        operation.attempts = 0
        operation.lease_until = None
        queued_at = now()
        # 人工重放立即发 outbox，扫描补发仍遵守同一个 90 秒等待窗口。
        operation.next_attempt_at = queued_at + timedelta(seconds=PENDING_REDISPATCH_SECONDS)
        session.add(
            Outbox(
                operation_id=operation.id,
                kind=operation.kind,
                dispatch_version=operation.dispatch_version,
                available_at=queued_at,
            )
        )
        session.add(
            Audit(actor_id=p.id, action="operation.replay", resource_id=operation.id, detail=reason)
        )
        return OperationOut.model_validate(operation)


async def disable_user(r: Resources, p: Principal, user_id: UUID, reason: str) -> None:
    async with r.sessions.begin() as session:
        await authorize(session, p, "identity", lock=True)
        user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
        if user is None:
            raise missing()
        user.active = False
        await session.execute(
            update(Session).where(Session.user_id == user.id).values(revoked=True)
        )
        session.add(Audit(actor_id=p.id, action="user.disable", resource_id=user.id, detail=reason))
