"""Web 界面使用的公开能力说明与受限展示查询。"""

import asyncio
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response
from fastapi.responses import FileResponse, RedirectResponse

from a_commerce.dependencies import Admin, Customer, R
from a_commerce.errors import missing
from a_commerce.integrations.storage import local_path, signed_url
from a_commerce.schemas.api import ProductOut, QuoteOut
from a_commerce.services import shipping, storefront
from a_commerce.services.management import RefundOut
from a_commerce.services.media import MediaOut

router = APIRouter(prefix="/api/v1", tags=["Web 展示"])
Limit = Annotated[int, Query(ge=1, le=100)]


@router.get("/customer/store", response_model=storefront.StoreOut)
async def store(r: R) -> storefront.StoreOut:
    return storefront.store(r)


@router.get("/customer/me", response_model=storefront.ProfileOut)
async def customer_me(r: R, p: Customer) -> storefront.ProfileOut:
    return await storefront.profile(r, p)


@router.get("/admin/profile", response_model=storefront.ProfileOut)
async def admin_me(r: R, p: Admin) -> storefront.ProfileOut:
    return await storefront.profile(r, p)


@router.get("/admin/products/{product_id}", response_model=ProductOut)
async def admin_product(product_id: UUID, r: R, p: Admin) -> ProductOut:
    return await storefront.product(r, p, product_id)


@router.get("/admin/inventory", response_model=storefront.InventoryPage)
async def inventory(
    r: R,
    p: Admin,
    cursor: UUID | None = None,
    limit: Limit = 30,
    q: Annotated[str, Query(max_length=80)] = "",
    low: bool = False,
) -> storefront.InventoryPage:
    return await storefront.inventory(r, p, cursor, limit, q, low)


@router.get("/admin/inventory/{sku_id}", response_model=storefront.InventoryOut)
async def stock(sku_id: UUID, r: R, p: Admin) -> storefront.InventoryOut:
    page = await storefront.inventory(r, p, None, 1, "", False, sku_id)
    if not page.items:
        raise missing()
    return page.items[0]


@router.get("/admin/shipping-rules", response_model=storefront.ShippingRulePage)
async def shipping_rules(
    r: R, p: Admin, cursor: UUID | None = None, limit: Limit = 50
) -> storefront.ShippingRulePage:
    return await storefront.shipping_rules(r, p, cursor, limit)


@router.get("/admin/products/{product_id}/media", response_model=list[MediaOut])
async def product_media(product_id: UUID, r: R, p: Admin) -> list[MediaOut]:
    return await storefront.product_media(r, p, product_id)


@router.get("/admin/media/{media_id}")
async def media(media_id: UUID, r: R, p: Admin) -> Response:
    record = await storefront.media(r, p, media_id)
    if record.backend == "s3":
        url = await asyncio.to_thread(signed_url, r.settings, record.storage_key)
        return RedirectResponse(url, status_code=307)
    return FileResponse(
        local_path(r.settings, record.storage_key),
        media_type=record.content_type,
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.get("/admin/orders/{order_id}/shipment", response_model=shipping.ShipmentDetail)
async def shipment(order_id: UUID, r: R, p: Admin) -> shipping.ShipmentDetail:
    return await shipping.get_shipment(r, p, order_id, admin=True)


@router.get("/customer/quotes/{quote_id}", response_model=QuoteOut)
async def quote(quote_id: UUID, r: R, p: Customer) -> QuoteOut:
    return await storefront.quote(r, p, quote_id)


@router.get("/customer/orders/{order_id}/refunds", response_model=list[RefundOut])
async def refunds(order_id: UUID, r: R, p: Customer) -> list[RefundOut]:
    return await storefront.refunds(r, p, order_id)
