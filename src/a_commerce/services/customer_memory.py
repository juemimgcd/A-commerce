"""显式偏好、待确认总结和功能使用事实。模型不能批准自己的推测或打开共享。"""

from datetime import timedelta
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from a_commerce.conf.resources import Resources
from a_commerce.errors import CommerceError, missing
from a_commerce.models.base import now
from a_commerce.models.entities import Audit, User
from a_commerce.models.support import CustomerMemory, SupportPreference, SupportRun, SupportUsage
from a_commerce.schemas.support import (
    AudiencePage,
    AudiencePreference,
    AudienceUser,
    InsightCount,
    InsightsOut,
    MemoryDecision,
    MemoryIn,
    MemoryOut,
    MemoryProfile,
    ProposeMemoryTool,
    SupportSettingsIn,
    UsageOut,
)
from a_commerce.services.identity import Principal, authorize


async def lock_preferences(session: AsyncSession, p: Principal) -> SupportPreference:
    # 用户锁让开关、清除、候选提取串行；外部模型调用期间不持锁。
    await session.scalar(select(User.id).where(User.id == p.id).with_for_update())
    await authorize(session, p)
    preference = await session.scalar(
        select(SupportPreference).where(SupportPreference.user_id == p.id)
    )
    if preference is None:
        preference = SupportPreference(user_id=p.id)
        session.add(preference)
        await session.flush()
    return preference


async def get_profile(r: Resources, p: Principal) -> MemoryProfile:
    async with r.sessions() as session:
        await authorize(session, p)
        preference = await session.scalar(
            select(SupportPreference).where(SupportPreference.user_id == p.id)
        )
        rows = await session.scalars(
            select(CustomerMemory)
            .where(CustomerMemory.user_id == p.id, CustomerMemory.status != "dismissed")
            .order_by(CustomerMemory.updated_at.desc(), CustomerMemory.id)
            .limit(100)
        )
        usage = await session.scalars(
            select(SupportUsage)
            .where(SupportUsage.user_id == p.id)
            .order_by(SupportUsage.uses.desc(), SupportUsage.feature)
        )
        return MemoryProfile(
            settings=SupportSettingsIn.model_validate(preference)
            if preference
            else SupportSettingsIn(),
            memories=[MemoryOut.model_validate(row) for row in rows],
            usage=[UsageOut.model_validate(row) for row in usage],
        )


async def save_settings(r: Resources, p: Principal, data: SupportSettingsIn) -> SupportSettingsIn:
    async with r.sessions.begin() as session:
        preference = await lock_preferences(session, p)
        if preference.memory_enabled != data.memory_enabled:
            preference.generation += 1
        for name, value in data.model_dump().items():
            setattr(preference, name, value)
        session.add(
            Audit(
                actor_id=p.id,
                action="support.consent",
                resource_id=p.id,
                detail=data.model_dump_json(),
            )
        )
    return data


async def save_memory(
    r: Resources, p: Principal, data: MemoryIn, memory_id: UUID | None = None
) -> MemoryOut:
    async with r.sessions.begin() as session:
        preference = await lock_preferences(session, p)
        if not preference.memory_enabled:
            raise CommerceError("MEMORY_DISABLED", "请先开启长期记忆。", 409)
        memory = await session.scalar(
            select(CustomerMemory).where(
                CustomerMemory.user_id == p.id,
                CustomerMemory.id == memory_id
                if memory_id
                else (CustomerMemory.kind == data.kind) & (CustomerMemory.value == data.value),
            )
        )
        if memory_id and memory is None:
            raise missing()
        if memory is None:
            await check_memory_capacity(session, p.id)
            memory = CustomerMemory(user_id=p.id)
            session.add(memory)
        memory.kind = data.kind
        memory.value = data.value.strip()
        memory.evidence = "用户在偏好管理中填写或纠正"
        memory.source = "user"
        memory.source_run_id = None
        memory.status = "confirmed"
        memory.updated_at = now()
        # 纠正后，正在执行的总结不能用旧上下文覆盖新偏好。
        preference.generation += 1
        await session.flush()
        return MemoryOut.model_validate(memory)


async def check_memory_capacity(session: AsyncSession, user_id: UUID) -> None:
    count = await session.scalar(
        select(func.count()).select_from(CustomerMemory).where(CustomerMemory.user_id == user_id)
    )
    if count is not None and count >= 100:
        raise CommerceError("MEMORY_LIMIT", "已达到 100 条记忆上限，请整理或清除后再添加。", 409)


async def decide_memory(
    r: Resources, p: Principal, memory_id: UUID, data: MemoryDecision
) -> MemoryOut:
    async with r.sessions.begin() as session:
        preference = await lock_preferences(session, p)
        if data.status == "confirmed" and not preference.memory_enabled:
            raise CommerceError("MEMORY_DISABLED", "请先开启长期记忆。", 409)
        memory = await session.scalar(
            select(CustomerMemory).where(
                CustomerMemory.id == memory_id, CustomerMemory.user_id == p.id
            )
        )
        if memory is None:
            raise missing()
        memory.status = data.status
        memory.updated_at = now()
        if data.status == "dismissed":
            # 保留去重键防止反复生成同一建议，但移除证据与来源对话关联。
            memory.evidence = ""
            memory.source_run_id = None
        preference.generation += 1
        return MemoryOut.model_validate(memory)


async def clear_memory(r: Resources, p: Principal) -> None:
    async with r.sessions.begin() as session:
        preference = await lock_preferences(session, p)
        preference.generation += 1
        await session.execute(delete(CustomerMemory).where(CustomerMemory.user_id == p.id))
        await session.execute(delete(SupportUsage).where(SupportUsage.user_id == p.id))
        session.add(
            Audit(actor_id=p.id, action="support.memory_clear", resource_id=p.id, detail="清除记忆")
        )


async def propose(
    r: Resources, p: Principal, run_id: UUID, data: ProposeMemoryTool
) -> dict[str, object]:
    async with r.sessions.begin() as session:
        preference = await lock_preferences(session, p)
        run = await session.get(SupportRun, run_id)
        if (
            run is None
            or run.user_id != p.id
            or run.status != "running"
            or run.expires_at <= now()
            or not preference.memory_enabled
            or run.memory_generation != preference.generation
        ):
            raise CommerceError("MEMORY_DISABLED", "记忆未启用或设置已变化，本次不保存。", 409)
        if data.evidence not in run.user_text:
            raise CommerceError("EVIDENCE_REQUIRED", "总结依据必须来自本轮用户原文。", 422)
        existing = await session.scalar(
            select(CustomerMemory).where(
                CustomerMemory.user_id == p.id,
                CustomerMemory.kind == data.kind,
                CustomerMemory.value == data.value.strip(),
            )
        )
        if existing is not None:
            return {"status": "unchanged", "message": "不覆盖已有偏好或重新提出已忽略的建议。"}
        await check_memory_capacity(session, p.id)
        memory = CustomerMemory(
            user_id=p.id,
            kind=data.kind,
            value=data.value.strip(),
            evidence=data.evidence,
            source="conversation",
            source_run_id=run.id,
            status="candidate",
            updated_at=now(),
        )
        session.add(memory)
        await session.flush()
        return {"status": "candidate", "memory_id": str(memory.id), "needs_user_confirmation": True}


async def record_usage(r: Resources, p: Principal, run_id: UUID, feature: str) -> None:
    async with r.sessions.begin() as session:
        preference = await lock_preferences(session, p)
        run = await session.get(SupportRun, run_id)
        if (
            run is None
            or run.status != "running"
            or run.expires_at <= now()
            or not preference.memory_enabled
            or preference.generation != run.memory_generation
        ):
            return
        await session.execute(
            insert(SupportUsage)
            .values(user_id=p.id, feature=feature, uses=1, last_used_at=now())
            .on_conflict_do_update(
                index_elements=["user_id", "feature"],
                set_={"uses": SupportUsage.uses + 1, "last_used_at": now()},
            )
        )


async def insights(r: Resources, p: Principal) -> InsightsOut:
    async with r.sessions() as session:
        await authorize(session, p, "audit")
        shared = (
            select(SupportPreference.user_id)
            .join(User, User.id == SupportPreference.user_id)
            .where(
                User.active.is_(True),
                SupportPreference.memory_enabled.is_(True),
                SupportPreference.share_with_merchant.is_(True),
            )
        )
        shared_users = await session.scalar(select(func.count()).select_from(shared.subquery()))
        marketing = await session.scalar(
            select(func.count())
            .select_from(SupportPreference)
            .where(
                SupportPreference.user_id.in_(shared), SupportPreference.marketing_enabled.is_(True)
            )
        )
        interests = await session.execute(
            select(CustomerMemory.kind, CustomerMemory.value, func.count(CustomerMemory.user_id))
            .where(CustomerMemory.user_id.in_(shared), CustomerMemory.status == "confirmed")
            .group_by(CustomerMemory.kind, CustomerMemory.value)
            .order_by(
                func.count(CustomerMemory.user_id).desc(), CustomerMemory.kind, CustomerMemory.value
            )
            .limit(30)
        )
        features = await session.execute(
            select(SupportUsage.feature, func.count(SupportUsage.user_id))
            .where(SupportUsage.user_id.in_(shared))
            .group_by(SupportUsage.feature)
            .order_by(func.count(SupportUsage.user_id).desc(), SupportUsage.feature)
        )
        feedback: dict[str | None, int] = {
            key: count
            for key, count in (
                await session.execute(
                    select(SupportRun.feedback, func.count(SupportRun.id))
                    .where(
                        SupportRun.user_id.in_(shared),
                        SupportRun.created_at >= now() - timedelta(days=30),
                        SupportRun.feedback.is_not(None),
                    )
                    .group_by(SupportRun.feedback)
                )
            ).all()
        }
        return InsightsOut(
            shared_users=shared_users or 0,
            marketing_users=marketing or 0,
            interests=[InsightCount(kind=k, value=v, users=n) for k, v, n in interests],
            features=[InsightCount(kind="feature", value=v, users=n) for v, n in features],
            helpful=feedback.get("helpful", 0),
            unhelpful=feedback.get("unhelpful", 0),
        )


async def audience(
    r: Resources, p: Principal, cursor: UUID | None, limit: int, marketing_only: bool
) -> AudiencePage:
    async with r.sessions.begin() as session:
        await authorize(session, p, "audit")
        query = (
            select(User, SupportPreference)
            .join(SupportPreference, SupportPreference.user_id == User.id)
            .where(
                User.active.is_(True),
                SupportPreference.memory_enabled.is_(True),
                SupportPreference.share_with_merchant.is_(True),
            )
        )
        if cursor:
            query = query.where(User.id > cursor)
        if marketing_only:
            query = query.where(SupportPreference.marketing_enabled.is_(True))
        rows = list((await session.execute(query.order_by(User.id).limit(limit + 1))).all())
        ids = [user.id for user, _ in rows[:limit]]
        memories = list(
            await session.scalars(
                select(CustomerMemory)
                .where(CustomerMemory.user_id.in_(ids), CustomerMemory.status == "confirmed")
                .order_by(CustomerMemory.updated_at.desc())
            )
        )
        usage = list(
            await session.scalars(
                select(SupportUsage)
                .where(SupportUsage.user_id.in_(ids))
                .order_by(SupportUsage.uses.desc())
            )
        )
        session.add(
            Audit(
                actor_id=p.id,
                action="support.audience_read",
                resource_id=p.id,
                detail=f"count={len(ids)};marketing_only={marketing_only}",
            )
        )
        return AudiencePage(
            items=[
                AudienceUser(
                    user_id=user.id,
                    email=user.email,
                    marketing_enabled=pref.marketing_enabled,
                    preferences=[
                        AudiencePreference.model_validate(m)
                        for m in memories
                        if m.user_id == user.id
                    ],
                    usage=[UsageOut.model_validate(u) for u in usage if u.user_id == user.id],
                )
                for user, pref in rows[:limit]
            ],
            next_cursor=ids[-1] if len(rows) > limit else None,
        )
