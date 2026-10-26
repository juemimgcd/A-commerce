"""服务端会话：原始 token 仅在登录时返回，数据库只保存 SHA-256 摘要。"""

import asyncio
import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal, cast
from uuid import UUID

from pwdlib import PasswordHash
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from a_commerce.conf.resources import Resources
from a_commerce.errors import CommerceError, missing
from a_commerce.models.base import now
from a_commerce.models.entities import Address, Audit, PasswordReset, Session, Staff, User
from a_commerce.schemas.api import (
    AccountOut,
    AddressIn,
    AddressOut,
    Credentials,
    PasswordChange,
    SessionOut,
    TokenOut,
)

PASSWORDS = PasswordHash.recommended()
# 固定的无效账号哈希让不存在账号也执行 Argon2，避免明显的用户枚举时差。
DUMMY_HASH = PASSWORDS.hash(secrets.token_urlsafe(32))


@dataclass(frozen=True)
class Principal:
    id: UUID
    session_id: UUID
    kind: Literal["customer", "staff"]


async def authorize(
    session: AsyncSession,
    principal: Principal,
    permission: str | None = None,
    *,
    lock: bool = False,
) -> User | Staff:
    # 锁顺序始终是账号 → 会话 → 业务资源。共享锁允许同一买家的并发读写，
    # 禁用/撤销使用排他更新，提交后的下一次写操作必然观察到撤销结果。
    model: type[User] | type[Staff] = User if principal.kind == "customer" else Staff
    query = select(model).where(model.id == principal.id)
    if lock:
        query = query.with_for_update(read=True)
    account = cast(User | Staff | None, await session.scalar(query))
    token_query = select(Session).where(Session.id == principal.session_id)
    if lock:
        token_query = token_query.with_for_update(read=True)
    token = await session.scalar(token_query)
    owner = (
        None
        if token is None
        else (token.user_id if principal.kind == "customer" else token.staff_id)
    )
    if (
        account is None
        or not account.active
        or token is None
        or token.revoked
        or token.expires_at <= now()
        or owner != principal.id
    ):
        raise CommerceError("UNAUTHENTICATED", "会话已失效，请重新登录。", 401)
    if permission is not None and (
        not isinstance(account, Staff) or permission not in account.permissions
    ):
        raise missing()
    if lock and permission in {"identity", "aftersales", "audit"}:
        if token.created_at < now() - timedelta(minutes=15):
            raise CommerceError("RECENT_AUTH_REQUIRED", "该敏感操作需要在 15 分钟内重新登录。", 401)
    return account


async def resolve(r: Resources, token: str, kind: Literal["customer", "staff"]) -> Principal:
    if len(token) > 128:
        raise CommerceError("UNAUTHENTICATED", "无效会话。", 401)
    async with r.sessions() as session:
        record = await session.scalar(
            select(Session).where(Session.token_hash == hashlib.sha256(token.encode()).hexdigest())
        )
        owner = (
            None if record is None else (record.user_id if kind == "customer" else record.staff_id)
        )
        if record is None or owner is None:
            raise CommerceError("UNAUTHENTICATED", "无效会话。", 401)
        principal = Principal(owner, record.id, kind)
        await authorize(session, principal)
        return principal


async def register(r: Resources, data: Credentials) -> AccountOut:
    async with asyncio.timeout(5), r.password_slots:
        digest = await asyncio.to_thread(PASSWORDS.hash, data.password.get_secret_value())
    async with r.sessions.begin() as session:
        user = User(email=str(data.email).lower(), password_hash=digest)
        session.add(user)
        await session.flush()
        return AccountOut.model_validate(user)


async def login(r: Resources, data: Credentials, *, staff: bool = False) -> TokenOut:
    model: type[User] | type[Staff] = Staff if staff else User
    async with r.sessions() as session:
        account = cast(
            User | Staff | None,
            await session.scalar(select(model).where(model.email == str(data.email).lower())),
        )
        digest = account.password_hash if account is not None else DUMMY_HASH
        account_id = account.id if account else None
    # 密码校验期间不占数据库连接；再次加锁检查密码/账号，防止并发改密后发出旧会话。
    async with asyncio.timeout(5), r.password_slots:
        valid = await asyncio.to_thread(PASSWORDS.verify, data.password.get_secret_value(), digest)
    if not valid or account_id is None:
        async with r.sessions.begin() as session:
            session.add(
                Audit(
                    actor_id=account_id,
                    action="auth.login_failed",
                    resource_id=account_id or UUID(int=0),
                    detail="staff" if staff else "customer",
                )
            )
        raise CommerceError("INVALID_CREDENTIALS", "邮箱或密码不正确。", 401)
    raw = secrets.token_urlsafe(32)
    expires = now() + timedelta(hours=r.settings.session_hours)
    async with r.sessions.begin() as session:
        account = cast(
            User | Staff | None,
            await session.scalar(select(model).where(model.id == account_id).with_for_update()),
        )
        accepted = account is not None and account.active and account.password_hash == digest
        if accepted and account is not None:
            session.add(
                Session(
                    token_hash=hashlib.sha256(raw.encode()).hexdigest(),
                    user_id=None if staff else account.id,
                    staff_id=account.id if staff else None,
                    expires_at=expires,
                )
            )
        # 审计不包含密码、原始 token、邮箱或地址；失败响应在事务提交之后返回。
        session.add(
            Audit(
                actor_id=account_id,
                resource_id=account_id,
                action="auth.login_succeeded" if accepted else "auth.login_failed",
                detail="staff" if staff else "customer",
            )
        )
    if not accepted:
        raise CommerceError("INVALID_CREDENTIALS", "邮箱或密码不正确。", 401)
    return TokenOut(token=raw, expires_at=expires)


async def revoke(r: Resources, p: Principal, session_id: UUID) -> None:
    async with r.sessions.begin() as session:
        # 先排他锁定账号，再核验当前会话并锁定目标会话。
        # 同账号撤销串行执行，避免并发退出的共享锁升级及两个会话交叉撤销死锁。
        model: type[User] | type[Staff] = User if p.kind == "customer" else Staff
        await session.scalar(select(model.id).where(model.id == p.id).with_for_update())
        await authorize(session, p)
        owner = Session.user_id if p.kind == "customer" else Session.staff_id
        record = await session.scalar(
            select(Session).where(Session.id == session_id, owner == p.id).with_for_update()
        )
        if record is None:
            raise missing()
        record.revoked = True


async def sessions(r: Resources, p: Principal) -> list[SessionOut]:
    async with r.sessions() as session:
        await authorize(session, p)
        owner = Session.user_id if p.kind == "customer" else Session.staff_id
        rows = await session.scalars(
            select(Session).where(owner == p.id).order_by(Session.created_at.desc()).limit(100)
        )
        return [
            SessionOut.model_validate(row).model_copy(update={"current": row.id == p.session_id})
            for row in rows
        ]


async def change_password(r: Resources, p: Principal, data: PasswordChange) -> None:
    async with r.sessions() as session:
        account = await authorize(session, p)
        original = account.password_hash
    async with asyncio.timeout(5), r.password_slots:
        valid = await asyncio.to_thread(
            PASSWORDS.verify, data.current_password.get_secret_value(), original
        )
        if not valid:
            raise CommerceError("INVALID_CREDENTIALS", "原密码不正确。", 401)
        digest = await asyncio.to_thread(PASSWORDS.hash, data.new_password.get_secret_value())
    async with r.sessions.begin() as session:
        # 改密先排他锁定账号，随后撤销该账号的全部会话（包含当前设备）。
        model: type[User] | type[Staff] = User if p.kind == "customer" else Staff
        locked_account = cast(
            User | Staff | None,
            await session.scalar(select(model).where(model.id == p.id).with_for_update()),
        )
        await authorize(session, p)
        if locked_account is None or locked_account.password_hash != original:
            raise CommerceError("PASSWORD_CHANGED", "密码已发生变化，请重新登录。")
        locked_account.password_hash = digest
        if p.kind == "customer":
            # 改密同时作废旧重置凭证；不能只撤销登录会话而让旧邮件覆盖新密码。
            # 与重置确认保持账号 → 重置令牌 → 会话的锁顺序。
            await session.execute(
                update(PasswordReset).where(PasswordReset.user_id == p.id).values(used=True)
            )
        owner = Session.user_id if p.kind == "customer" else Session.staff_id
        await session.execute(update(Session).where(owner == p.id).values(revoked=True))


async def addresses(r: Resources, p: Principal) -> list[AddressOut]:
    async with r.sessions() as session:
        await authorize(session, p)
        rows = await session.scalars(
            select(Address).where(Address.user_id == p.id).order_by(Address.id).limit(100)
        )
        return [AddressOut.model_validate(row) for row in rows]


async def save_address(
    r: Resources, p: Principal, data: AddressIn, address_id: UUID | None = None
) -> AddressOut:
    async with r.sessions.begin() as session:
        await authorize(session, p, lock=True)
        if address_id is None:
            address = Address(user_id=p.id, **data.model_dump())
            session.add(address)
        else:
            existing = await session.scalar(
                select(Address)
                .where(Address.id == address_id, Address.user_id == p.id)
                .with_for_update()
            )
            if existing is None:
                raise missing()
            address = existing
            for key, value in data.model_dump().items():
                setattr(address, key, value)
            address.version += 1
        await session.flush()
        return AddressOut.model_validate(address)
