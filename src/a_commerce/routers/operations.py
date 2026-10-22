"""对账、快照与运营查询接口；写操作在 service 内再次验证当前权限。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from pydantic import Field

from a_commerce.dependencies import Admin, Customer, R, sensitive_limit
from a_commerce.schemas.api import PasswordChange, ReturnOut, Schema, SessionOut
from a_commerce.services import event_recovery, identity, management, reconciliation

router = APIRouter(prefix="/api/v1", tags=["运营与恢复"])
Limit = Annotated[int, Query(ge=1, le=100)]


class NoteIn(Schema):
    note: str = Field(min_length=1, max_length=500)


@router.post("/admin/reconciliations", response_model=reconciliation.RunOut, status_code=202)
async def create_reconciliation(r: R, p: Admin) -> reconciliation.RunOut:
    return await reconciliation.create(r, p)


@router.get("/admin/reconciliations", response_model=list[reconciliation.RunOut])
async def reconciliations(
    r: R, p: Admin, cursor: UUID | None = None, limit: Limit = 50
) -> list[reconciliation.RunOut]:
    return await reconciliation.runs(r, p, cursor, limit)


@router.get("/admin/reconciliation-cases", response_model=list[reconciliation.CaseOut])
async def cases(
    r: R,
    p: Admin,
    status: Annotated[str, Query(max_length=32)] = "open",
    cursor: UUID | None = None,
    limit: Limit = 50,
) -> list[reconciliation.CaseOut]:
    return await reconciliation.cases(r, p, status, cursor, limit)


@router.get("/admin/reconciliations/{run_id}", response_model=reconciliation.RunOut)
async def get_reconciliation(run_id: UUID, r: R, p: Admin) -> reconciliation.RunOut:
    return await reconciliation.get_run(r, p, run_id)


@router.post("/admin/reconciliation-cases/{case_id}/recheck", response_model=reconciliation.CaseOut)
async def recheck(case_id: UUID, data: NoteIn, r: R, p: Admin) -> reconciliation.CaseOut:
    return await reconciliation.recheck(r, p, case_id, data.note)


@router.post(
    "/customer/event-snapshots", response_model=event_recovery.SnapshotOut, status_code=201
)
async def create_snapshot(r: R, p: Customer) -> event_recovery.SnapshotOut:
    return await event_recovery.create_snapshot(r, p)


@router.get("/customer/event-snapshots/{snapshot_id}", response_model=event_recovery.SnapshotPage)
async def snapshot_page(
    snapshot_id: UUID, r: R, p: Customer, cursor: UUID | None = None, limit: Limit = 50
) -> event_recovery.SnapshotPage:
    return await event_recovery.snapshot_page(r, p, snapshot_id, cursor, limit)


@router.get("/admin/staff", response_model=list[management.StaffOut])
async def staff(
    r: R, p: Admin, cursor: UUID | None = None, limit: Limit = 50
) -> list[management.StaffOut]:
    return await management.staff_list(r, p, cursor, limit)


@router.put("/admin/staff/{staff_id}", response_model=management.StaffOut)
async def update_staff(
    staff_id: UUID, data: management.StaffUpdate, r: R, p: Admin
) -> management.StaffOut:
    return await management.update_staff(r, p, staff_id, data)


@router.get("/admin/sessions", response_model=list[SessionOut])
async def sessions(r: R, p: Admin) -> list[SessionOut]:
    return await identity.sessions(r, p)


@router.delete("/admin/sessions/{session_id}", status_code=204)
async def revoke(session_id: UUID, r: R, p: Admin) -> Response:
    await identity.revoke(r, p, session_id)
    return Response(status_code=204)


@router.post("/admin/auth/password", status_code=204, dependencies=[Depends(sensitive_limit)])
async def password(data: PasswordChange, r: R, p: Admin) -> Response:
    await identity.change_password(r, p, data)
    return Response(status_code=204)


@router.get("/admin/returns", response_model=list[ReturnOut])
async def returns(
    r: R,
    p: Admin,
    status: Annotated[str | None, Query(max_length=32)] = None,
    cursor: UUID | None = None,
    limit: Limit = 50,
) -> list[ReturnOut]:
    return await management.returns(r, p, status, cursor, limit)


@router.get("/admin/refunds", response_model=list[management.RefundOut])
async def refunds(
    r: R,
    p: Admin,
    status: Annotated[str | None, Query(max_length=32)] = None,
    cursor: UUID | None = None,
    limit: Limit = 50,
) -> list[management.RefundOut]:
    return await management.refunds(r, p, status, cursor, limit)


@router.get("/customer/returns", response_model=list[ReturnOut])
async def customer_returns(
    r: R,
    p: Customer,
    status: Annotated[str | None, Query(max_length=32)] = None,
    cursor: UUID | None = None,
    limit: Limit = 50,
) -> list[ReturnOut]:
    return await management.returns(r, p, status, cursor, limit, customer=True)


@router.get("/customer/orders/{order_id}/history", response_model=list[management.HistoryOut])
async def customer_history(
    order_id: UUID, r: R, p: Customer, after: Annotated[int, Query(ge=0)] = 0, limit: Limit = 50
) -> list[management.HistoryOut]:
    return await management.history(r, p, order_id, after, limit, customer=True)


@router.get("/admin/inventory/{sku_id}/movements", response_model=list[management.MovementOut])
async def movements(
    sku_id: UUID, r: R, p: Admin, cursor: UUID | None = None, limit: Limit = 50
) -> list[management.MovementOut]:
    return await management.movements(r, p, sku_id, cursor, limit)


@router.get("/admin/products/{product_id}/changes", response_model=list[management.ChangeOut])
async def changes(
    product_id: UUID, r: R, p: Admin, after: Annotated[int, Query(ge=0)] = 0, limit: Limit = 50
) -> list[management.ChangeOut]:
    return await management.changes(r, p, product_id, after, limit)


@router.get("/admin/orders/{order_id}/history", response_model=list[management.HistoryOut])
async def history(
    order_id: UUID, r: R, p: Admin, after: Annotated[int, Query(ge=0)] = 0, limit: Limit = 50
) -> list[management.HistoryOut]:
    return await management.history(r, p, order_id, after, limit)
