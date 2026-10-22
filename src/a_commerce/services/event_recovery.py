"""持久化订单快照与连续事件前缀归档；游标过期不能静默丢掉历史变化。"""

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, func, literal, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from a_commerce.conf.resources import Resources
from a_commerce.errors import CommerceError, missing
from a_commerce.models.base import now
from a_commerce.models.entities import (
    Event,
    EventArchive,
    EventSequence,
    EventSnapshot,
    EventSnapshotItem,
    MailJob,
    Notification,
    Order,
)
from a_commerce.schemas.api import Schema
from a_commerce.services.identity import Principal, authorize


class SnapshotOut(Schema):
    id: UUID
    through_sequence: int
    expires_at: datetime


class SnapshotPage(Schema):
    items: list[dict[str, object]]
    next_cursor: UUID | None
    through_sequence: int


async def check_cursor(session: AsyncSession, user_id: UUID, after: int) -> None:
    # 与归档互斥到查询结束，防止检查通过后对应事件被并发删除。
    sequence = await session.scalar(
        select(EventSequence).where(EventSequence.user_id == user_id).with_for_update(read=True)
    )
    if sequence and after < sequence.retained_after:
        raise CommerceError(
            "CURSOR_EXPIRED", "事件游标已过期，请创建订单快照并从快照水位继续拉取。", 410
        )


async def create_snapshot(r: Resources, p: Principal) -> SnapshotOut:
    async with r.sessions.begin() as session:
        await authorize(session, p, lock=True)
        await session.execute(
            insert(EventSequence)
            .values(user_id=p.id, value=0, retained_after=0)
            .on_conflict_do_nothing(index_elements=["user_id"])
        )
        sequence = (
            await session.scalars(
                select(EventSequence).where(EventSequence.user_id == p.id).with_for_update()
            )
        ).one()
        count = await session.scalar(
            select(func.count())
            .select_from(EventSnapshot)
            .where(EventSnapshot.user_id == p.id, EventSnapshot.expires_at > now())
        )
        if count is not None and count >= 3:
            raise CommerceError(
                "SNAPSHOT_LIMIT", "最多保留三个有效快照，请复用快照或等待过期。", 429
            )
        snapshot = EventSnapshot(
            user_id=p.id,
            through_sequence=sequence.value,
            expires_at=now() + timedelta(seconds=r.settings.snapshot_seconds),
        )
        session.add(snapshot)
        await session.flush()
        # 写事件的事务最后锁同一序号行；此处持锁且只读取已提交订单，水位与快照一致。
        # 一条 INSERT SELECT 避免把所有订单装进 Python 内存，分页读取已固定的结果。
        fields = (
            "id",
            "status",
            "payment_status",
            "fulfillment_status",
            "version",
            "total_fen",
            "refunded_fen",
            "refund_reserved_fen",
            "created_at",
        )
        values: list[object] = []
        for field in fields:
            values.extend([field, getattr(Order, field)])
        await session.execute(
            insert(EventSnapshotItem).from_select(
                ["id", "created_at", "snapshot_id", "order_id", "snapshot"],
                select(
                    func.gen_random_uuid(),
                    func.now(),
                    literal(snapshot.id),
                    Order.id,
                    func.jsonb_build_object(*values),
                ).where(Order.user_id == p.id),
                include_defaults=False,
            )
        )
        return SnapshotOut.model_validate(snapshot)


async def snapshot_page(
    r: Resources, p: Principal, snapshot_id: UUID, cursor: UUID | None, limit: int
) -> SnapshotPage:
    async with r.sessions() as session:
        await authorize(session, p)
        snapshot = await session.scalar(
            select(EventSnapshot)
            .where(EventSnapshot.id == snapshot_id, EventSnapshot.user_id == p.id)
            .with_for_update(read=True)
        )
        if snapshot is None:
            raise missing()
        if snapshot.expires_at <= now():
            raise CommerceError("SNAPSHOT_EXPIRED", "订单快照已过期，请重新创建。", 410)
        query = select(EventSnapshotItem).where(EventSnapshotItem.snapshot_id == snapshot.id)
        if cursor:
            query = query.where(EventSnapshotItem.order_id > cursor)
        rows = list(
            await session.scalars(query.order_by(EventSnapshotItem.order_id).limit(limit + 1))
        )
        return SnapshotPage(
            items=[row.snapshot for row in rows[:limit]],
            next_cursor=rows[limit - 1].order_id if len(rows) > limit else None,
            through_sequence=snapshot.through_sequence,
        )


async def archive_batch(r: Resources) -> bool:
    """每次最多归档十个用户各一百条；邮件未送达时停在该前缀，保留其原始事件。"""
    cutoff = now() - timedelta(days=r.settings.event_retention_days)
    more = False
    async with r.sessions.begin() as session:
        expired = list(
            await session.scalars(
                select(EventSnapshot.id)
                .where(EventSnapshot.expires_at <= now())
                .limit(100)
                .with_for_update(skip_locked=True)
            )
        )
        if expired:
            await session.execute(
                delete(EventSnapshotItem).where(EventSnapshotItem.snapshot_id.in_(expired))
            )
            await session.execute(delete(EventSnapshot).where(EventSnapshot.id.in_(expired)))
        more = len(expired) == 100
        sequences = list(
            await session.scalars(
                select(EventSequence)
                .where(
                    select(Event.id)
                    .where(Event.user_id == EventSequence.user_id, Event.created_at < cutoff)
                    .exists()
                )
                .order_by(
                    EventSequence.archive_checked_at.asc().nulls_first(), EventSequence.user_id
                )
                .limit(10)
                .with_for_update(skip_locked=True)
            )
        )
        for sequence in sequences:
            user_id = sequence.user_id
            # 被未送达邮件阻塞的用户移到队尾，不能长期饿死其他用户的归档。
            sequence.archive_checked_at = now()
            rows = list(
                await session.scalars(
                    select(Event)
                    .where(Event.user_id == user_id)
                    .order_by(Event.sequence)
                    .limit(100)
                )
            )
            pending = set(
                await session.scalars(
                    select(MailJob.event_id).where(
                        MailJob.event_id.in_([row.id for row in rows]), MailJob.status != "sent"
                    )
                )
            )
            archived: list[UUID] = []
            for event in rows:
                if event.created_at >= cutoff or event.id in pending:
                    break
                session.add(
                    # order_id 外键会获取订单 KEY SHARE 锁；业务订单锁必须保持
                    # NO KEY UPDATE，不能在持有事件序号锁的路径中引入反向等待。
                    EventArchive(
                        user_id=user_id,
                        order_id=event.order_id,
                        sequence=event.sequence,
                        kind=event.kind,
                        order_version=event.order_version,
                        occurred_at=event.created_at,
                    )
                )
                archived.append(event.id)
                sequence.retained_after = event.sequence
            if archived:
                await session.execute(
                    delete(Notification).where(Notification.event_id.in_(archived))
                )
                await session.execute(delete(MailJob).where(MailJob.event_id.in_(archived)))
                await session.execute(delete(Event).where(Event.id.in_(archived)))
                more = True
        # 无可归档前缀时结束本轮，避免被未送达邮件卡住后忙循环。
    return more
