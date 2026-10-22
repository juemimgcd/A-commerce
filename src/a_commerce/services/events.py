"""在业务事务内写用户事件；序号在独立用户行锁下分配，防止晚提交事件被游标漏掉。"""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from a_commerce.conf.resources import Resources
from a_commerce.models.entities import (
    Event,
    EventSequence,
    MailJob,
    Notification,
    NotificationPreference,
    Order,
)
from a_commerce.schemas.api import EventOut
from a_commerce.services.event_recovery import check_cursor
from a_commerce.services.identity import Principal, authorize
from a_commerce.services.operations import enqueue


async def record_event(session: AsyncSession, order: Order, kind: str) -> None:
    await session.execute(
        insert(EventSequence)
        .values(user_id=order.user_id, value=0)
        .on_conflict_do_nothing(index_elements=["user_id"])
    )
    sequence = (
        await session.scalars(
            select(EventSequence).where(EventSequence.user_id == order.user_id).with_for_update()
        )
    ).one()
    sequence.value += 1
    event = Event(
        user_id=order.user_id,
        order_id=order.id,
        sequence=sequence.value,
        kind=kind,
        order_version=order.version,
    )
    session.add(event)
    await session.flush()
    preference = await session.scalar(
        select(NotificationPreference).where(NotificationPreference.user_id == order.user_id)
    )
    if preference is None or preference.in_app:
        session.add(
            Notification(user_id=order.user_id, event_id=event.id, order_id=order.id, kind=kind)
        )
    if preference is not None and preference.email:
        job = MailJob(user_id=order.user_id, event_id=event.id)
        session.add(job)
        await session.flush()
        await enqueue(session, "mail", job.id)


async def list_events(r: Resources, p: Principal, after: int, limit: int) -> list[EventOut]:
    async with r.sessions() as session:
        await authorize(session, p)
        await check_cursor(session, p.id, after)
        rows = await session.scalars(
            select(Event)
            .where(Event.user_id == p.id, Event.sequence > after)
            .order_by(Event.sequence)
            .limit(limit)
        )
        return [EventOut.model_validate(row) for row in rows]
