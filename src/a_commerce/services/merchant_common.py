"""经营写入共用授权、版本及幂等协议。"""

import hashlib
import json
from collections.abc import Awaitable, Callable
from functools import wraps
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from a_commerce.errors import CommerceError
from a_commerce.models.base import now
from a_commerce.models.entities import IdempotencyRecord, Staff, User
from a_commerce.models.support import SupportRun
from a_commerce.services.identity import Principal
from a_commerce.services.shop_access import scope


def snapshot_write[**P, T](operation: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
    """仅重放已整体回滚的数据库快照写入，不包裹任何外部副作用。"""

    @wraps(operation)
    async def retry(*args: P.args, **kwargs: P.kwargs) -> T:
        for attempt in range(3):
            try:
                return await operation(*args, **kwargs)
            except DBAPIError as exc:
                state = getattr(exc.orig, "sqlstate", None)
                constraint = getattr(getattr(exc.orig, "__cause__", None), "constraint_name", None)
                # 等待账号锁不会刷新 REPEATABLE READ 快照；并发同键写入
                # 可能先遇到唯一约束。新事务才能看到已提交结果并校验指纹。
                recoverable = state in {"40001", "40P01"} or (
                    state == "23505" and constraint == "uq_idempotency_records_actor_id"
                )
                if not recoverable or attempt == 2:
                    raise
        raise RuntimeError("快照写入未返回结果")

    return retry


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


async def writer(
    session: AsyncSession,
    p: Principal,
    shop_id: UUID,
    run_id: UUID | None = None,
    *,
    catalog: bool = False,
) -> None:
    model = User if p.kind == "customer" else Staff
    await session.scalar(select(model.id).where(model.id == p.id).with_for_update())
    await scope(session, p, shop_id, "catalog", write=True, catalog=catalog)
    if run_id is not None:
        run = await session.scalar(
            select(SupportRun).where(SupportRun.id == run_id).with_for_update()
        )
        if (
            run is None
            or run.user_id != p.id
            or run.shop_id != shop_id
            or run.status != "running"
            or run.expires_at <= now()
        ):
            raise CommerceError("SUPPORT_EXPIRED", "经营运行已结束，本次未保存。", 401)


async def replay(
    session: AsyncSession,
    p: Principal,
    shop_id: UUID,
    action: str,
    key: str,
    value: object,
) -> UUID | None:
    fingerprint = digest([shop_id, value])
    row = await session.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.actor_id == p.id,
            IdempotencyRecord.action == action,
            IdempotencyRecord.key == key,
        )
    )
    if row is not None and row.fingerprint != fingerprint:
        raise CommerceError("IDEMPOTENCY_CONFLICT", "同一业务键不能用于不同内容。", 409)
    return row.result_id if row else None


def remember(
    session: AsyncSession,
    p: Principal,
    shop_id: UUID,
    action: str,
    key: str,
    value: object,
    result_id: UUID,
) -> None:
    session.add(
        IdempotencyRecord(
            actor_id=p.id,
            action=action,
            key=key,
            fingerprint=digest([shop_id, value]),
            result_id=result_id,
        )
    )


def version(actual: int, expected: int) -> None:
    if actual != expected:
        raise CommerceError("VERSION_CONFLICT", "记录已变化，请刷新后重新确认。", 409)
