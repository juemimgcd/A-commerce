"""公开秒杀列表及拥有 catalog 权限的管理员配置入口。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from a_commerce.dependencies import Admin, Customer, R
from a_commerce.schemas.flash_sales import FlashSaleIn, FlashSaleOut, FlashSalePage, FlashSaleUpdate
from a_commerce.services import flash_sales

router = APIRouter(prefix="/api/v1", tags=["秒杀"])


@router.get("/customer/eligible-flash-sales", response_model=FlashSalePage)
async def eligible_sales(
    r: R, p: Customer, cursor: UUID | None = None, limit: Annotated[int, Query(ge=1, le=50)] = 20
) -> FlashSalePage:
    return await flash_sales.list_sales(r, cursor, limit, buyer=p)


@router.get("/customer/eligible-flash-sales/{sale_id}", response_model=FlashSaleOut)
async def eligible_sale(sale_id: UUID, r: R, p: Customer) -> FlashSaleOut:
    return await flash_sales.get_sale(r, sale_id, buyer=p)


@router.get("/customer/flash-sales", response_model=FlashSalePage)
async def public_sales(
    r: R,
    cursor: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> FlashSalePage:
    return await flash_sales.list_sales(r, cursor, limit)


@router.get("/customer/flash-sales/{sale_id}", response_model=FlashSaleOut)
async def public_sale(sale_id: UUID, r: R) -> FlashSaleOut:
    return await flash_sales.get_sale(r, sale_id)


@router.get("/admin/flash-sales", response_model=FlashSalePage)
async def admin_sales(
    r: R,
    p: Admin,
    cursor: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> FlashSalePage:
    return await flash_sales.list_sales(r, cursor, limit, p)


@router.post("/admin/flash-sales", response_model=FlashSaleOut, status_code=201)
async def create_sale(data: FlashSaleIn, r: R, p: Admin) -> FlashSaleOut:
    return await flash_sales.save_sale(r, p, data)


@router.put("/admin/flash-sales/{sale_id}", response_model=FlashSaleOut)
async def update_sale(sale_id: UUID, data: FlashSaleUpdate, r: R, p: Admin) -> FlashSaleOut:
    return await flash_sales.save_sale(r, p, data, sale_id)
