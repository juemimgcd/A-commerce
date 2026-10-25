"""账号撤销、真实 SMTP 投递、媒体清理与输入边界。"""

import asyncio
import io
import re
from datetime import timedelta
from email import policy
from email.parser import BytesParser
from uuid import uuid4

import pytest
from PIL import Image
from pydantic import SecretStr
from sqlalchemy import select, update

from a_commerce.models.base import now
from a_commerce.models.entities import MailJob, PasswordReset, Session, StoredObject, User
from a_commerce.schemas.api import PasswordChange
from a_commerce.services import identity, media, password_reset
from a_commerce.services.password_reset import ResetRequest
from tests.conftest import Backend

pytestmark = pytest.mark.integration


async def test_password_reset_delivered_over_smtp_revokes_all_sessions(backend: Backend) -> None:
    received: asyncio.Queue[bytes] = asyncio.Queue()

    async def smtp(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            writer.write(b"220 test-smtp\r\n")
            await writer.drain()
            while line := await reader.readline():
                command = line.split(maxsplit=1)[0].upper()
                if command == b"EHLO":
                    writer.write(b"250-test-smtp\r\n250 SIZE 1048576\r\n")
                elif command in {b"MAIL", b"RCPT", b"RSET", b"NOOP"}:
                    writer.write(b"250 OK\r\n")
                elif command == b"DATA":
                    writer.write(b"354 End with dot\r\n")
                    await writer.drain()
                    body = bytearray()
                    while (line := await reader.readline()) != b".\r\n":
                        if not line:
                            return
                        body.extend(line[1:] if line.startswith(b"..") else line)
                    await received.put(bytes(body))
                    writer.write(b"250 queued\r\n")
                elif command == b"QUIT":
                    writer.write(b"221 bye\r\n")
                    await writer.drain()
                    return
                else:
                    writer.write(b"502 unsupported\r\n")
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(smtp, "127.0.0.1", 0)
    async with server:
        settings = backend.r.settings
        settings.mail_enabled = True
        settings.smtp_host = "127.0.0.1"
        settings.smtp_port = server.sockets[0].getsockname()[1]
        settings.smtp_start_tls = False
        settings.reset_secret = SecretStr("test-reset-secret-" * 3)
        settings.mail_from = "test@example.com"
        async with backend.r.sessions() as session:
            user = await session.get(User, backend.buyer.principal.id)
            email = user.email
        await password_reset.request_reset(backend.r, ResetRequest(email=email))
        async with backend.r.sessions() as session:
            reset = (await session.scalars(select(PasswordReset))).one()
            reset_id = reset.id
            job = (await session.scalars(select(MailJob))).one()
            job_id = job.id
        await backend.dispatch("mail", job_id)
        message = BytesParser(policy=policy.default).parsebytes(
            await asyncio.wait_for(received.get(), 5)
        )
        assert message["To"] == email
        token = re.search(r"[a-f0-9-]{36}\.[a-f0-9]{64}", message.get_content()).group()
        assert token.startswith(str(reset_id))
        async with backend.r.sessions() as session:
            assert (await session.get(PasswordReset, reset_id)).token_hash != token
        response = await backend.client.post(
            "/api/v1/customer/auth/reset-confirm",
            json={"token": token, "new_password": "new-secure-password-123"},
        )
        assert response.status_code == 204, response.text
        replay = await backend.client.post(
            "/api/v1/customer/auth/reset-confirm",
            json={"token": token, "new_password": "other-password-123"},
        )
        assert replay.status_code == 400
        assert (
            await backend.client.get("/api/v1/customer/profile", headers=backend.buyer.headers)
        ).status_code == 401
        login = await backend.client.post(
            "/api/v1/customer/auth/login",
            json={"email": email, "password": "new-secure-password-123"},
        )
        assert login.status_code == 200, login.text


async def test_password_change_invalidates_previously_issued_reset(backend: Backend) -> None:
    r = backend.r
    r.settings.mail_enabled = True
    r.settings.reset_secret = SecretStr("test-reset-secret-" * 3)
    async with r.sessions.begin() as session:
        user = await session.get(User, backend.buyer.principal.id)
        user.password_hash = await asyncio.to_thread(identity.PASSWORDS.hash, "old-password-123")
        email = user.email
    await password_reset.request_reset(r, ResetRequest(email=email))
    async with r.sessions() as session:
        reset_id = (await session.scalars(select(PasswordReset.id))).one()
    token = password_reset.token_for(r.settings, reset_id)
    await identity.change_password(
        r,
        backend.buyer.principal,
        PasswordChange(current_password="old-password-123", new_password="new-password-456"),
    )
    response = await backend.client.post(
        "/api/v1/customer/auth/reset-confirm",
        json={"token": token, "new_password": "stolen-password-123"},
    )
    assert response.status_code == 400
    async with r.sessions() as session:
        assert all(
            await session.scalars(
                select(Session.revoked).where(Session.user_id == backend.buyer.principal.id)
            )
        )


async def test_disabled_account_cannot_write_using_old_session(backend: Backend) -> None:
    async with backend.r.sessions.begin() as session:
        await session.execute(
            update(User).where(User.id == backend.buyer.principal.id).values(active=False)
        )
    response = await backend.client.put(
        f"/api/v1/customer/cart/items/{backend.sku_ids[0]}",
        headers=backend.buyer.headers,
        json={"quantity": 1},
    )
    assert response.status_code == 401


async def test_media_decode_download_and_orphan_cleanup(backend: Backend) -> None:
    output = io.BytesIO()
    Image.new("RGB", (16, 16), color="red").save(output, format="PNG")
    response = await backend.client.post(
        f"/api/v1/admin/products/{backend.product_id}/media",
        headers={**backend.admin.headers, "Content-Type": "image/png"},
        content=output.getvalue(),
    )
    assert response.status_code == 201, response.text
    ident = response.json()["id"]
    response = await backend.client.get(f"/api/v1/customer/media/{ident}")
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "image/jpeg"
    assert Image.open(io.BytesIO(response.content)).size == (16, 16)
    orphan_key = f"media/{uuid4()}.jpg"
    orphan = backend.r.settings.files_directory / orphan_key
    orphan.write_bytes(b"orphan")
    async with backend.r.sessions.begin() as session:
        session.add(
            StoredObject(
                storage_key=orphan_key, backend="local", created_at=now() - timedelta(days=2)
            )
        )
        # 即便关联对象的台账异常为 pending，清理也不能删掉正在使用的媒体。
        await session.execute(
            update(StoredObject)
            .where(StoredObject.storage_key != orphan_key)
            .values(status="pending", created_at=now() - timedelta(days=2))
        )
    await media.clean_orphans(backend.r)
    assert not orphan.exists()
    assert (await backend.client.get(f"/api/v1/customer/media/{ident}")).status_code == 200
    async with backend.r.sessions() as session:
        assert (
            await session.scalar(
                select(StoredObject.status).where(StoredObject.storage_key == orphan_key)
            )
            == "deleted"
        )


@pytest.mark.parametrize("body", [b"not-an-image", b"<svg><script/></svg>"])
async def test_invalid_media_is_rejected(backend: Backend, body: bytes) -> None:
    response = await backend.client.post(
        f"/api/v1/admin/products/{backend.product_id}/media",
        headers=backend.admin.headers,
        content=body,
    )
    assert response.status_code == 422


async def test_input_limits_and_validation_do_not_echo_password(backend: Backend) -> None:
    secret = "private-password-" * 20
    response = await backend.client.post(
        "/api/v1/customer/auth/login", json={"email": "invalid", "password": secret}
    )
    assert response.status_code == 422
    assert secret not in response.text
    response = await backend.client.post(
        "/api/v1/customer/auth/login", content=b"x" * (128 * 1024 + 1)
    )
    assert response.status_code == 413
