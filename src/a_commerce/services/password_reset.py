"""重置令牌只保存摘要。原始令牌由独立密钥派生，异步邮件无需持久化明文凭证。"""

import asyncio
import hashlib
import hmac
from datetime import timedelta
from uuid import UUID, uuid4

from pydantic import EmailStr, Field, SecretStr
from sqlalchemy import select, update

from a_commerce.conf.resources import Resources
from a_commerce.conf.settings import Settings
from a_commerce.errors import CommerceError
from a_commerce.models.base import now
from a_commerce.models.entities import MailJob, PasswordReset, Session, User
from a_commerce.schemas.api import Schema
from a_commerce.services.identity import PASSWORDS
from a_commerce.services.operations import enqueue


class ResetRequest(Schema):
    email: EmailStr


class ResetConfirm(Schema):
    token: SecretStr = Field(min_length=70, max_length=128)
    new_password: SecretStr = Field(min_length=12, max_length=128)


def token_for(settings: Settings, reset_id: UUID) -> str:
    if settings.reset_secret is None or len(settings.reset_secret.get_secret_value()) < 32:
        raise CommerceError("MAIL_DISABLED", "未配置独立的密码重置密钥。", 503)
    signature = hmac.new(
        settings.reset_secret.get_secret_value().encode(), str(reset_id).encode(), hashlib.sha256
    ).hexdigest()
    return f"{reset_id}.{signature}"


async def request_reset(r: Resources, data: ResetRequest) -> None:
    if not r.settings.mail_enabled:
        raise CommerceError("MAIL_DISABLED", "当前部署未启用邮件服务。", 503)
    reset_id = uuid4()
    token = token_for(r.settings, reset_id)
    async with r.sessions.begin() as session:
        user = await session.scalar(
            # 与改密/重置确认锁同一账号，防止旧请求在令牌作废事务之后才提交。
            select(User)
            .where(User.email == str(data.email).lower(), User.active.is_(True))
            .with_for_update()
        )
        # 无论邮箱是否存在都由路由返回同样的受理结果，不公开账户存在性。
        if user is None:
            return
        reset = PasswordReset(
            id=reset_id,
            user_id=user.id,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=now() + timedelta(minutes=30),
        )
        session.add(reset)
        await session.flush()
        job = MailJob(user_id=user.id, reset_id=reset.id)
        session.add(job)
        await session.flush()
        await enqueue(session, "mail", job.id)


async def confirm_reset(r: Resources, data: ResetConfirm) -> None:
    token = data.token.get_secret_value()
    digest = hashlib.sha256(token.encode()).hexdigest()
    async with r.sessions() as session:
        reset = await session.scalar(
            select(PasswordReset).where(PasswordReset.token_hash == digest)
        )
        if reset is None or reset.used or reset.expires_at <= now():
            raise CommerceError("RESET_INVALID", "重置令牌无效、已使用或已过期。", 400)
        user_id, reset_id = reset.user_id, reset.id
    async with asyncio.timeout(5), r.password_slots:
        hashed = await asyncio.to_thread(PASSWORDS.hash, data.new_password.get_secret_value())
    async with r.sessions.begin() as session:
        # 与改密/禁用相同，先锁账号再锁令牌/会话，避免反向锁顺序。
        user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
        reset = await session.scalar(
            select(PasswordReset).where(PasswordReset.id == reset_id).with_for_update()
        )
        if (
            user is None
            or not user.active
            or reset is None
            or reset.used
            or reset.expires_at <= now()
        ):
            raise CommerceError("RESET_INVALID", "重置令牌无效、已使用或已过期。", 400)
        user.password_hash = hashed
        await session.execute(
            update(PasswordReset).where(PasswordReset.user_id == user_id).values(used=True)
        )
        await session.execute(
            update(Session).where(Session.user_id == user_id).values(revoked=True)
        )
