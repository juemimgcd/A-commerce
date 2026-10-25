"""运营资金导出与敏感员工操作保持身份、所有权和重新认证边界。"""

from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import update

from a_commerce.models.base import now
from a_commerce.models.entities import Session
from tests.conftest import Backend

pytestmark = pytest.mark.integration


async def test_financial_csv_export_is_private_and_matches_confirmed_payment(
    backend: Backend,
) -> None:
    order = await backend.pay(await backend.order())
    day = now().date().isoformat()
    response = await backend.client.post(
        "/api/v1/admin/exports", headers=backend.admin.headers, json={"start": day, "end": day}
    )
    assert response.status_code == 202
    ident = UUID(response.json()["id"])
    pending = await backend.client.get(
        f"/api/v1/admin/exports/{ident}/download", headers=backend.admin.headers
    )
    assert pending.status_code == 409
    await backend.dispatch("export", ident)
    result = await backend.client.get(
        f"/api/v1/admin/exports/{ident}/download", headers=backend.admin.headers
    )
    assert result.status_code == 200
    assert f"{day},{order['total_fen']},0,{order['total_fen']}" in result.text
    assert "example.com" not in result.text
    assert backend.buyer.token not in result.text
    rejected = await backend.client.get(
        f"/api/v1/admin/exports/{ident}/download", headers=backend.buyer.headers
    )
    assert rejected.status_code == 401


async def test_sensitive_staff_changes_require_recent_login(backend: Backend) -> None:
    async with backend.r.sessions.begin() as session:
        await session.execute(
            update(Session)
            .where(Session.id == backend.admin.principal.session_id)
            .values(created_at=now() - timedelta(minutes=16))
        )
    response = await backend.client.post(
        "/api/v1/admin/staff",
        headers=backend.admin.headers,
        json={
            "email": "operator@example.com",
            "password": "staff-password-123",
            "permissions": ["catalog"],
        },
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "RECENT_AUTH_REQUIRED"
