"""数据库驱动的任务调度：消息是唤醒信号，操作记录才是恢复依据。"""

from datetime import timedelta
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from a_commerce.conf.resources import Resources
from a_commerce.models.base import now
from a_commerce.models.entities import Operation, Outbox

PENDING_REDISPATCH_SECONDS = 90


async def enqueue(session: AsyncSession, kind: str, resource_id: UUID) -> Operation:
    existing = await session.scalar(
        select(Operation).where(Operation.kind == kind, Operation.resource_id == resource_id)
    )
    if existing:
        return existing
    queued_at = now()
    # outbox 立即可投递；next_attempt_at 统一表示扫描器最早可补发的时间。
    operation = Operation(
        kind=kind,
        resource_id=resource_id,
        next_attempt_at=queued_at + timedelta(seconds=PENDING_REDISPATCH_SECONDS),
    )
    session.add(operation)
    await session.flush()
    session.add(
        Outbox(operation_id=operation.id, dispatch_version=1, kind=kind, available_at=queued_at)
    )
    return operation


async def scan(r: Resources) -> None:
    # SKIP LOCKED 只用于任务领取；普通库存竞争不得跳过被锁行。
    scanned_at = now()
    async with r.sessions.begin() as session:
        operations = list(
            (
                await session.scalars(
                    select(Operation)
                    .where(
                        Operation.status.in_(["pending", "running", "retry_wait", "unknown"]),
                        Operation.next_attempt_at <= scanned_at,
                        or_(Operation.lease_until.is_(None), Operation.lease_until <= scanned_at),
                    )
                    .order_by(Operation.next_attempt_at, Operation.id)
                    .limit(50)
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
        for operation in operations:
            # 到期条件已在 LIMIT 之前筛选，未到期 pending 不占这批 50 个名额。
            # 即使 outbox 已发送也要恢复；这里不再给到期时间额外加一次等待。
            if operation.attempts >= r.settings.operation_max_attempts:
                operation.status = "manual_review"
                continue
            operation.dispatch_version += 1
            operation.status = "pending"
            operation.next_attempt_at = scanned_at + timedelta(seconds=PENDING_REDISPATCH_SECONDS)
            operation.lease_until = None
            session.add(
                Outbox(
                    operation_id=operation.id,
                    kind=operation.kind,
                    dispatch_version=operation.dispatch_version,
                    available_at=scanned_at,
                )
            )
