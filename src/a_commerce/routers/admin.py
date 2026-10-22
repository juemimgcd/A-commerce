"""管理员路由；catalog、fulfillment、aftersales、audit、identity 分别检查权限。"""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import Field

from a_commerce.dependencies import Admin, Key, R, sensitive_limit
from a_commerce.errors import CommerceError
from a_commerce.schemas.api import (
    AccountOut,
    CategoryIn,
    CategoryOut,
    Credentials,
    InspectionIn,
    OperationOut,
    OrderFilter,
    OrderOut,
    OrderPage,
    ProductIn,
    ProductOut,
    ProductPage,
    ReturnOut,
    Schema,
    ShipmentIn,
    SKUIn,
    SKUOut,
    StockAdjustment,
    StockOut,
    TokenOut,
    VersionIn,
)
from a_commerce.services import (
    administration,
    aftersales,
    catalog,
    catalog_import,
    exports,
    fulfillment,
    identity,
    media,
    orders,
    pricing,
    reporting,
)
from a_commerce.services.exports import ExportIn, ExportOut
from a_commerce.services.media import MediaOut
from a_commerce.services.pricing import ShippingRuleIn, ShippingRuleOut
from a_commerce.services.reporting import DailyOut

router = APIRouter(prefix="/api/v1/admin", tags=["管理员"])


@router.get("/reports/ranking", response_model=list[reporting.RankingOut])
async def product_ranking(
    start: date, end: date, r: R, p: Admin, limit: Annotated[int, Query(ge=1, le=100)] = 20
) -> list[reporting.RankingOut]:
    return await reporting.ranking(r, p, start, end, limit)


@router.get("/products", response_model=ProductPage)
async def admin_products(
    r: R,
    p: Admin,
    q: Annotated[str, Query(max_length=80)] = "",
    cursor: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> ProductPage:
    return await catalog.admin_products(r, p, q, cursor, limit)


@router.post("/catalog-imports", response_model=list[catalog_import.ImportResult])
async def catalog_imports(
    data: catalog_import.ImportIn, r: R, p: Admin
) -> list[catalog_import.ImportResult]:
    return await catalog_import.import_products(r, p, data)


@router.post("/staff", response_model=AccountOut, status_code=201)
async def create_staff(data: administration.StaffIn, r: R, p: Admin) -> AccountOut:
    return await administration.create_staff(r, p, data)


@router.get("/audits", response_model=list[administration.AuditOut])
async def audits(
    r: R, p: Admin, cursor: UUID | None = None, limit: Annotated[int, Query(ge=1, le=100)] = 50
) -> list[administration.AuditOut]:
    return await administration.audits(r, p, cursor, limit)


@router.get("/message-failures", response_model=list[administration.FailureOut])
async def message_failures(
    r: R, p: Admin, limit: Annotated[int, Query(ge=1, le=100)] = 50
) -> list[administration.FailureOut]:
    return await administration.failures(r, p, limit)


@router.get("/inventory/low-stock", response_model=list[StockOut])
async def low_stock(
    r: R,
    p: Admin,
    threshold: Annotated[int, Query(ge=0, le=10000)] = 5,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[StockOut]:
    return await administration.low_stock(r, p, threshold, limit)


@router.put("/shipping-rules", response_model=ShippingRuleOut)
async def shipping_rule(data: ShippingRuleIn, r: R, p: Admin) -> ShippingRuleOut:
    return await pricing.save_rule(r, p, data)


@router.post("/exports", response_model=ExportOut, status_code=202)
async def create_export(data: ExportIn, r: R, p: Admin) -> ExportOut:
    return await exports.create_export(r, p, data)


@router.get("/exports/{export_id}", response_model=ExportOut)
async def get_export(export_id: UUID, r: R, p: Admin) -> ExportOut:
    return await exports.get_export(r, p, export_id)


@router.get("/exports/{export_id}/download")
async def download_export(export_id: UUID, r: R, p: Admin) -> FileResponse:
    path = await exports.download(r, p, export_id)
    return FileResponse(path, media_type="text/csv", filename=f"commerce-{export_id}.csv")


@router.post("/products/{product_id}/media", response_model=MediaOut, status_code=201)
async def upload_media(product_id: UUID, request: Request, r: R, p: Admin) -> MediaOut:
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > 8 * 1024 * 1024:
            raise CommerceError("MEDIA_TOO_LARGE", "图片不能超过 8 MiB。", 413)
    return await media.upload(r, p, product_id, bytes(content))


@router.get("/reports/daily", response_model=list[DailyOut])
async def daily_report(start: date, end: date, r: R, p: Admin) -> list[DailyOut]:
    return await reporting.daily(r, p, start, end)


class ReasonIn(Schema):
    reason: str = Field(min_length=1, max_length=500)


@router.post("/returns/{return_id}/approve", response_model=ReturnOut)
async def approve_return(return_id: UUID, r: R, p: Admin) -> ReturnOut:
    return await aftersales.progress(r, p, return_id, "approve")


@router.post("/returns/{return_id}/reject", response_model=ReturnOut)
async def reject_return(return_id: UUID, data: ReasonIn, r: R, p: Admin) -> ReturnOut:
    return await aftersales.progress(r, p, return_id, "reject", reason=data.reason)


@router.post("/returns/{return_id}/receive", response_model=ReturnOut)
async def receive_return(return_id: UUID, r: R, p: Admin) -> ReturnOut:
    return await aftersales.progress(r, p, return_id, "receive")


@router.post("/auth/login", response_model=TokenOut, dependencies=[Depends(sensitive_limit)])
async def login(data: Credentials, r: R) -> TokenOut:
    return await identity.login(r, data, staff=True)


@router.post("/auth/logout", status_code=204)
async def logout(r: R, p: Admin) -> Response:
    await identity.revoke(r, p, p.session_id)
    return Response(status_code=204)


@router.post("/categories", response_model=CategoryOut, status_code=201)
async def create_category(data: CategoryIn, r: R, p: Admin) -> CategoryOut:
    return await catalog.create_category(r, p, data)


@router.post("/products", response_model=ProductOut, status_code=201)
async def create_product(data: ProductIn, r: R, p: Admin) -> ProductOut:
    return await catalog.save_product(r, p, data)


@router.put("/products/{product_id}", response_model=ProductOut)
async def update_product(product_id: UUID, data: ProductIn, r: R, p: Admin) -> ProductOut:
    return await catalog.save_product(r, p, data, product_id)


@router.post("/products/{product_id}/skus", response_model=SKUOut, status_code=201)
async def create_sku(product_id: UUID, data: SKUIn, r: R, p: Admin) -> SKUOut:
    return await catalog.save_sku(r, p, product_id, data)


@router.put("/products/{product_id}/skus/{sku_id}", response_model=SKUOut)
async def update_sku(product_id: UUID, sku_id: UUID, data: SKUIn, r: R, p: Admin) -> SKUOut:
    return await catalog.save_sku(r, p, product_id, data, sku_id)


@router.post("/inventory/{sku_id}/adjust", response_model=StockOut)
async def adjust(sku_id: UUID, data: StockAdjustment, r: R, p: Admin, key: Key) -> StockOut:
    return await catalog.adjust_stock(r, p, sku_id, data, key)


@router.get("/orders", response_model=OrderPage)
async def list_orders(
    r: R,
    p: Admin,
    filters: Annotated[OrderFilter, Depends()],
    user_id: UUID | None = None,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> OrderPage:
    return await orders.list_orders(
        r, p, cursor, limit, admin=True, filters=filters, user_id=user_id
    )


@router.get("/orders/{order_id}", response_model=OrderOut)
async def get_order(order_id: UUID, r: R, p: Admin) -> OrderOut:
    return await orders.get_order(r, p, order_id, admin=True)


@router.post("/orders/{order_id}/pick", response_model=OrderOut)
async def pick(order_id: UUID, data: VersionIn, r: R, p: Admin) -> OrderOut:
    return await fulfillment.transition(r, p, order_id, "pick", data)


@router.post("/orders/{order_id}/ship", response_model=OrderOut)
async def ship(order_id: UUID, data: ShipmentIn, r: R, p: Admin) -> OrderOut:
    return await fulfillment.transition(r, p, order_id, "ship", data)


@router.post("/orders/{order_id}/deliver", response_model=OrderOut)
async def deliver(order_id: UUID, data: VersionIn, r: R, p: Admin) -> OrderOut:
    return await fulfillment.transition(r, p, order_id, "deliver", data)


@router.get("/returns/{return_id}", response_model=ReturnOut)
async def get_return(return_id: UUID, r: R, p: Admin) -> ReturnOut:
    return await aftersales.get_return(r, p, return_id, admin=True)


@router.post("/returns/{return_id}/inspect", response_model=ReturnOut)
async def inspect(return_id: UUID, data: InspectionIn, r: R, p: Admin) -> ReturnOut:
    return await aftersales.inspect(r, p, return_id, data)


@router.get("/operations", response_model=list[OperationOut])
async def operations(
    r: R,
    p: Admin,
    status: Annotated[str, Query(max_length=32)] = "manual_review",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[OperationOut]:
    return await administration.operations(r, p, status, limit)


@router.post("/operations/{operation_id}/replay", response_model=OperationOut)
async def replay(operation_id: UUID, data: ReasonIn, r: R, p: Admin) -> OperationOut:
    return await administration.replay(r, p, operation_id, data.reason)


@router.post("/users/{user_id}/disable", status_code=204)
async def disable_user(user_id: UUID, data: ReasonIn, r: R, p: Admin) -> Response:
    await administration.disable_user(r, p, user_id, data.reason)
    return Response(status_code=204)
