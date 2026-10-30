"""用户会话经营店铺；平台开店仍使用独立管理员会话。"""

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request

from a_commerce.dependencies import Admin, Customer, Key, R
from a_commerce.errors import CommerceError
from a_commerce.schemas.api import (
    InspectionIn,
    OrderFilter,
    OrderOut,
    OrderPage,
    ProductIn,
    ProductOut,
    ReturnOut,
    ShipmentIn,
    SKUIn,
    SKUOut,
    StockAdjustment,
    StockOut,
    VersionIn,
)
from a_commerce.schemas.merchant import (
    CostIn,
    CostOut,
    MerchantProductIn,
    MerchantProductPage,
    MerchantSKUIn,
    ShopEdit,
    ShopIn,
    ShopOut,
    ShopStatus,
)
from a_commerce.schemas.support import ConversationIn, ConversationOut, MessageIn, SupportRunOut
from a_commerce.services import (
    aftersales,
    catalog,
    fulfillment,
    media,
    merchant,
    merchant_reporting,
    orders,
    shops,
    support,
)
from a_commerce.services.media import MediaOut

router = APIRouter(prefix="/api/v1", tags=["店铺与商家经营"])


@router.get("/admin/shops", response_model=list[ShopOut])
async def admin_shops(r: R, p: Admin) -> list[ShopOut]:
    return await shops.list_shops(r, p, admin=True)


@router.post("/admin/shops", response_model=ShopOut, status_code=201)
async def create_shop(data: ShopIn, r: R, p: Admin) -> ShopOut:
    return await shops.create(r, p, data)


@router.put("/admin/shops/{shop_id}", response_model=ShopOut)
async def admin_edit_shop(shop_id: UUID, data: ShopEdit, r: R, p: Admin) -> ShopOut:
    return await shops.edit(r, p, shop_id, data)


@router.put("/admin/shops/{shop_id}/status", response_model=ShopOut)
async def shop_status(shop_id: UUID, data: ShopStatus, r: R, p: Admin) -> ShopOut:
    return await shops.set_status(r, p, shop_id, data)


@router.get("/merchant/shops", response_model=list[ShopOut])
async def my_shops(r: R, p: Customer) -> list[ShopOut]:
    return await shops.list_shops(r, p)


@router.get("/admin/shops/{shop_id}/products", response_model=MerchantProductPage)
async def platform_shop_products(
    shop_id: UUID,
    r: R,
    p: Admin,
    cursor: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> MerchantProductPage:
    return await merchant.products(r, p, shop_id, cursor, limit)


@router.put("/admin/shops/{shop_id}/skus/{sku_id}/cost", response_model=CostOut)
async def platform_cost(shop_id: UUID, sku_id: UUID, data: CostIn, r: R, p: Admin) -> CostOut:
    return await merchant.cost(r, p, shop_id, sku_id, data)


@router.get("/admin/shops/{shop_id}/reports", response_model=merchant_reporting.MerchantReport)
async def platform_shop_reports(
    shop_id: UUID, start: date, end: date, r: R, p: Admin
) -> merchant_reporting.MerchantReport:
    return await merchant_reporting.report(r, p, shop_id, start, end)


@router.put("/merchant/shops/{shop_id}", response_model=ShopOut)
async def edit_shop(shop_id: UUID, data: ShopEdit, r: R, p: Customer) -> ShopOut:
    return await shops.edit(r, p, shop_id, data)


@router.get("/merchant/shops/{shop_id}/products", response_model=MerchantProductPage)
async def products(
    shop_id: UUID,
    r: R,
    p: Customer,
    cursor: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    q: Annotated[str, Query(max_length=80)] = "",
) -> MerchantProductPage:
    return await merchant.products(r, p, shop_id, cursor, limit, q)


@router.post("/merchant/shops/{shop_id}/products", response_model=ProductOut, status_code=201)
async def create_product(shop_id: UUID, data: ProductIn, r: R, p: Customer) -> ProductOut:
    return await catalog.save_product(r, p, data, shop_id=shop_id)


@router.put("/merchant/shops/{shop_id}/products/{product_id}", response_model=ProductOut)
async def update_product(
    shop_id: UUID, product_id: UUID, data: MerchantProductIn, r: R, p: Customer
) -> ProductOut:
    return await catalog.save_product(
        r,
        p,
        ProductIn.model_validate(data.model_dump(exclude={"expected_version"})),
        product_id,
        shop_id=shop_id,
        expected_version=data.expected_version,
    )


@router.post(
    "/merchant/shops/{shop_id}/products/{product_id}/skus", response_model=SKUOut, status_code=201
)
async def create_sku(shop_id: UUID, product_id: UUID, data: SKUIn, r: R, p: Customer) -> SKUOut:
    return await catalog.save_sku(r, p, product_id, data, shop_id=shop_id)


@router.put("/merchant/shops/{shop_id}/products/{product_id}/skus/{sku_id}", response_model=SKUOut)
async def update_sku(
    shop_id: UUID, product_id: UUID, sku_id: UUID, data: MerchantSKUIn, r: R, p: Customer
) -> SKUOut:
    return await catalog.save_sku(
        r,
        p,
        product_id,
        SKUIn.model_validate(data.model_dump(exclude={"expected_version"})),
        sku_id,
        shop_id=shop_id,
        expected_version=data.expected_version,
    )


@router.put("/merchant/shops/{shop_id}/skus/{sku_id}/cost", response_model=CostOut)
async def cost(shop_id: UUID, sku_id: UUID, data: CostIn, r: R, p: Customer) -> CostOut:
    return await merchant.cost(r, p, shop_id, sku_id, data)


@router.post("/merchant/shops/{shop_id}/inventory/{sku_id}/adjust", response_model=StockOut)
async def adjust(
    shop_id: UUID, sku_id: UUID, data: StockAdjustment, r: R, p: Customer, key: Key
) -> StockOut:
    return await catalog.adjust_stock(r, p, sku_id, data, key, shop_id=shop_id)


@router.get("/merchant/shops/{shop_id}/products/{product_id}/media", response_model=list[MediaOut])
async def product_media(shop_id: UUID, product_id: UUID, r: R, p: Customer) -> list[MediaOut]:
    return await merchant.media_list(r, p, shop_id, product_id)


@router.post(
    "/merchant/shops/{shop_id}/products/{product_id}/media",
    response_model=MediaOut,
    status_code=201,
)
async def upload(shop_id: UUID, product_id: UUID, request: Request, r: R, p: Customer) -> MediaOut:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 8 * 1024 * 1024:
            raise CommerceError("MEDIA_TOO_LARGE", "图片不能超过 8 MiB。", 413)
    return await media.upload(r, p, product_id, bytes(body), shop_id=shop_id)


@router.get("/merchant/shops/{shop_id}/orders", response_model=OrderPage)
async def list_orders(
    shop_id: UUID,
    r: R,
    p: Customer,
    filters: Annotated[OrderFilter, Depends()],
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> OrderPage:
    return await orders.list_orders(r, p, cursor, limit, filters=filters, shop_id=shop_id)


@router.get("/merchant/shops/{shop_id}/orders/{order_id}", response_model=OrderOut)
async def order_detail(shop_id: UUID, order_id: UUID, r: R, p: Customer) -> OrderOut:
    return await orders.get_order(r, p, order_id, shop_id=shop_id)


@router.post("/merchant/shops/{shop_id}/orders/{order_id}/pick", response_model=OrderOut)
async def pick(shop_id: UUID, order_id: UUID, data: VersionIn, r: R, p: Customer) -> OrderOut:
    return await fulfillment.transition(r, p, order_id, "pick", data, shop_id=shop_id)


@router.post("/merchant/shops/{shop_id}/orders/{order_id}/ship", response_model=OrderOut)
async def ship(shop_id: UUID, order_id: UUID, data: ShipmentIn, r: R, p: Customer) -> OrderOut:
    return await fulfillment.transition(r, p, order_id, "ship", data, shop_id=shop_id)


@router.post("/merchant/shops/{shop_id}/orders/{order_id}/deliver", response_model=OrderOut)
async def deliver(shop_id: UUID, order_id: UUID, data: VersionIn, r: R, p: Customer) -> OrderOut:
    return await fulfillment.transition(r, p, order_id, "deliver", data, shop_id=shop_id)


@router.get("/merchant/shops/{shop_id}/returns", response_model=list[ReturnOut])
async def returns(
    shop_id: UUID,
    r: R,
    p: Customer,
    cursor: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[ReturnOut]:
    return await merchant.returns(r, p, shop_id, cursor, limit)


@router.get("/merchant/shops/{shop_id}/returns/{return_id}", response_model=ReturnOut)
async def return_detail(shop_id: UUID, return_id: UUID, r: R, p: Customer) -> ReturnOut:
    return await aftersales.get_return(r, p, return_id, shop_id=shop_id)


@router.post("/merchant/shops/{shop_id}/returns/{return_id}/inspection", response_model=ReturnOut)
async def inspect_return(
    shop_id: UUID, return_id: UUID, data: InspectionIn, r: R, p: Customer
) -> ReturnOut:
    return await aftersales.inspect(r, p, return_id, data, shop_id=shop_id)


@router.post("/merchant/shops/{shop_id}/returns/{return_id}/{action}", response_model=ReturnOut)
async def return_progress(
    shop_id: UUID,
    return_id: UUID,
    action: Literal["approve", "reject", "receive"],
    r: R,
    p: Customer,
    reason: Annotated[str, Query(min_length=1, max_length=500)] = "商家处理售后",
) -> ReturnOut:
    return await aftersales.progress(r, p, return_id, action, reason=reason, shop_id=shop_id)


@router.get("/merchant/shops/{shop_id}/reports", response_model=merchant_reporting.MerchantReport)
async def reports(
    shop_id: UUID, start: date, end: date, r: R, p: Customer, product_id: UUID | None = None
) -> merchant_reporting.MerchantReport:
    return await merchant_reporting.report(r, p, shop_id, start, end, product_id)


@router.get("/merchant/shops/{shop_id}/conversations", response_model=list[ConversationOut])
async def conversations(shop_id: UUID, r: R, p: Customer) -> list[ConversationOut]:
    return await support.conversations(r, p, shop_id=shop_id)


@router.post(
    "/merchant/shops/{shop_id}/conversations", response_model=ConversationOut, status_code=201
)
async def create_conversation(
    shop_id: UUID, data: ConversationIn, r: R, p: Customer
) -> ConversationOut:
    return await support.create_conversation(r, p, data, shop_id=shop_id)


@router.get(
    "/merchant/shops/{shop_id}/conversations/{conversation_id}/messages",
    response_model=list[SupportRunOut],
)
async def messages(shop_id: UUID, conversation_id: UUID, r: R, p: Customer) -> list[SupportRunOut]:
    return await support.history(r, p, conversation_id, shop_id=shop_id)


@router.post(
    "/merchant/shops/{shop_id}/conversations/{conversation_id}/messages",
    response_model=SupportRunOut,
    status_code=202,
)
async def send(
    shop_id: UUID,
    conversation_id: UUID,
    data: MessageIn,
    background: BackgroundTasks,
    r: R,
    p: Customer,
) -> SupportRunOut:
    run, token = await support.start_run(r, p, conversation_id, data, shop_id=shop_id)
    if token:
        background.add_task(support.execute_run, r, p, run.id, token)
    return run


@router.post("/merchant/shops/{shop_id}/runs/{run_id}/cancel", status_code=204)
async def cancel(shop_id: UUID, run_id: UUID, r: R, p: Customer) -> None:
    await support.cancel_run(r, p, run_id, shop_id=shop_id)
