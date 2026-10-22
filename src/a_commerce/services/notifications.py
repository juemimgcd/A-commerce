"""站内通知和用户开关；消息是否已读不影响持久事件游标。"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from a_commerce.conf.resources import Resources
from a_commerce.errors import CommerceError, missing
from a_commerce.models.base import now
from a_commerce.models.entities import Event, Notification, NotificationPreference
from a_commerce.schemas.api import Schema
from a_commerce.services.event_recovery import check_cursor
from a_commerce.services.identity import Principal, authorize


class PreferenceIn(Schema):
    in_app: bool = True
    email: bool = False


class NotificationOut(Schema):
    id: UUID
    event_id: UUID
    order_id: UUID
    kind: str
    read_at: datetime | None
    created_at: datetime
    sequence: int


async def preference(r: Resources, p: Principal, data: PreferenceIn | None = None) -> PreferenceIn:
    if data is not None and data.email and not r.settings.mail_enabled:
        raise CommerceError("MAIL_DISABLED", "当前部署未启用邮件服务。", 503)
    async with r.sessions.begin() as session:
        await authorize(session, p, lock=data is not None)
        if data is not None:
            await session.execute(
                insert(NotificationPreference)
                .values(user_id=p.id, **data.model_dump())
                .on_conflict_do_update(index_elements=["user_id"], set_=data.model_dump())
            )
            return data
        row = await session.scalar(
            select(NotificationPreference).where(NotificationPreference.user_id == p.id)
        )
        return PreferenceIn.model_validate(row) if row else PreferenceIn()


async def list_notifications(
    r: Resources, p: Principal, after: int, limit: int
) -> list[NotificationOut]:
    async with r.sessions() as session:
        await authorize(session, p)
        await check_cursor(session, p.id, after)
        query = select(Notification, Event.sequence).join(Event, Event.id == Notification.event_id)
        rows = await session.execute(
            query.where(Notification.user_id == p.id, Event.sequence > after)
            .order_by(Event.sequence)
            .limit(limit)
        )
        return [
            NotificationOut.model_validate(
                {
                    **{
                        name: getattr(row, name)
                        for name in NotificationOut.model_fields
                        if name != "sequence"
                    },
                    "sequence": sequence,
                }
            )
            for row, sequence in rows
        ]


async def mark_read(r: Resources, p: Principal, notification_id: UUID) -> None:
    async with r.sessions.begin() as session:
        await authorize(session, p, lock=True)
        row = await session.scalar(
            select(Notification)
            .where(Notification.id == notification_id, Notification.user_id == p.id)
            .with_for_update()
        )
        if row is None:
            raise missing()
        row.read_at = row.read_at or now()
