"""买家 HTTP 路由：输入/身份交给 schema 和依赖，业务规则交给 service。"""

import asyncio
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import FileResponse, RedirectResponse

from a_commerce.dependencies import Customer, Key, R, sensitive_limit
from a_commerce.errors import CommerceError
from a_commerce.integrations.storage import local_path, signed_url
from a_commerce.schemas.api import (
    AccountOut,
    AddressIn,
    AddressOut,
    CancelIn,
    CartIn,
    CartOut,
    CategoryOut,
    Credentials,
    EventOut,
    OrderFilter,
    OrderIn,
    OrderOut,
    OrderPage,
    PasswordChange,
    PaymentOut,
    ProductOut,
    ProductPage,
    QuoteIn,
    QuoteOut,
    ReturnIn,
    ReturnOut,
    SessionOut,
    TokenOut,
)
from a_commerce.services import (
    aftersales,
    cart,
    catalog,
    checkout,
    events,
    identity,
    media,
    notifications,
    orders,
    password_reset,
    payments,
    shipping,
)
from a_commerce.services.media import MediaOut
from a_commerce.services.notifications import NotificationOut, PreferenceIn
from a_commerce.services.password_reset import ResetConfirm, ResetRequest

router = APIRouter(prefix="/api/v1/customer", tags=["买家"])


@router.get("/orders/{order_id}/shipment", response_model=shipping.ShipmentDetail)
async def shipment(order_id: UUID, r: R, p: Customer) -> shipping.ShipmentDetail:
    return await shipping.get_shipment(r, p, order_id)


@router.get("/orders/{order_id}/return-options", response_model=list[aftersales.ReturnOption])
async def return_options(order_id: UUID, r: R, p: Customer) -> list[aftersales.ReturnOption]:
    return await aftersales.return_options(r, p, order_id)


@router.post("/returns/{return_id}/ship", response_model=ReturnOut)
async def return_parcel(
    return_id: UUID, data: aftersales.ReturnParcelIn, r: R, p: Customer
) -> ReturnOut:
    return await aftersales.progress(r, p, return_id, "ship", parcel=data)


@router.get("/order-requests/{key}", response_model=OrderOut)
async def order_request(key: str, r: R, p: Customer) -> OrderOut:
    return await orders.by_request(r, p, key)


@router.post("/auth/reset-request", status_code=202, dependencies=[Depends(sensitive_limit)])
async def reset_request(data: ResetRequest, r: R) -> dict[str, str]:
    await password_reset.request_reset(r, data)
    return {"status": "accepted", "message": "若邮箱对应有效账号，将收到重置邮件。"}


@router.post("/auth/reset-confirm", status_code=204, dependencies=[Depends(sensitive_limit)])
async def reset_confirm(data: ResetConfirm, r: R) -> Response:
    await password_reset.confirm_reset(r, data)
    return Response(status_code=204)


@router.get("/products/{product_id}/media", response_model=list[MediaOut])
async def product_media(product_id: UUID, r: R) -> list[MediaOut]:
    return await media.list_media(r, product_id)


@router.get("/media/{media_id}")
async def download_media(media_id: UUID, r: R) -> Response:
    record = await media.get_media(r, media_id)
    if record.backend == "s3":
        url = await asyncio.to_thread(signed_url, r.settings, record.storage_key)
        return RedirectResponse(url, status_code=307)
    return FileResponse(
        local_path(r.settings, record.storage_key),
        media_type=record.content_type,
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.get("/profile", response_model=AccountOut)
async def profile(r: R, p: Customer) -> AccountOut:
    async with r.sessions() as session:
        return AccountOut.model_validate(await identity.authorize(session, p))


@router.get("/favorites", response_model=ProductPage)
async def favorites(
    r: R, p: Customer, cursor: UUID | None = None, limit: Annotated[int, Query(ge=1, le=50)] = 20
) -> ProductPage:
    return await cart.favorites(r, p, cursor, limit)


@router.get("/notification-preferences", response_model=PreferenceIn)
async def notification_preferences(r: R, p: Customer) -> PreferenceIn:
    return await notifications.preference(r, p)


@router.put("/notification-preferences", response_model=PreferenceIn)
async def save_notification_preferences(data: PreferenceIn, r: R, p: Customer) -> PreferenceIn:
    return await notifications.preference(r, p, data)


@router.get("/notifications", response_model=list[NotificationOut])
async def list_notifications(
    r: R,
    p: Customer,
    after: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[NotificationOut]:
    return await notifications.list_notifications(r, p, after, limit)


@router.post("/notifications/{notification_id}/read", status_code=204)
async def mark_notification_read(notification_id: UUID, r: R, p: Customer) -> Response:
    await notifications.mark_read(r, p, notification_id)
    return Response(status_code=204)


@router.post(
    "/auth/register",
    response_model=AccountOut,
    status_code=201,
    dependencies=[Depends(sensitive_limit)],
)
async def register(data: Credentials, r: R) -> AccountOut:
    return await identity.register(r, data)


@router.post("/auth/login", response_model=TokenOut, dependencies=[Depends(sensitive_limit)])
async def login(data: Credentials, r: R) -> TokenOut:
    return await identity.login(r, data)


@router.post("/auth/logout", status_code=204)
async def logout(r: R, p: Customer) -> Response:
    await identity.revoke(r, p, p.session_id)
    return Response(status_code=204)


@router.get("/sessions", response_model=list[SessionOut])
async def sessions(r: R, p: Customer) -> list[SessionOut]:
    return await identity.sessions(r, p)


@router.delete("/sessions/{session_id}", status_code=204)
async def revoke(session_id: UUID, r: R, p: Customer) -> Response:
    await identity.revoke(r, p, session_id)
    return Response(status_code=204)


@router.post("/auth/password", status_code=204, dependencies=[Depends(sensitive_limit)])
async def password(data: PasswordChange, r: R, p: Customer) -> Response:
    await identity.change_password(r, p, data)
    return Response(status_code=204)


@router.get("/addresses", response_model=list[AddressOut])
async def addresses(r: R, p: Customer) -> list[AddressOut]:
    return await identity.addresses(r, p)


@router.post("/addresses", response_model=AddressOut, status_code=201)
async def create_address(data: AddressIn, r: R, p: Customer) -> AddressOut:
    return await identity.save_address(r, p, data)


@router.put("/addresses/{address_id}", response_model=AddressOut)
async def update_address(address_id: UUID, data: AddressIn, r: R, p: Customer) -> AddressOut:
    return await identity.save_address(r, p, data, address_id)


@router.get("/categories", response_model=list[CategoryOut])
async def categories(r: R) -> list[CategoryOut]:
    return await catalog.categories(r)


@router.get("/products", response_model=ProductPage)
async def products(
    r: R,
    q: Annotated[str, Query(max_length=80)] = "",
    category: UUID | None = None,
    cursor: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    min_price: Annotated[int, Query(ge=0)] = 0,
    max_price: Annotated[int, Query(ge=0, le=100000000)] = 100000000,
    brand: Annotated[str, Query(max_length=100)] = "",
    attribute: Annotated[list[str] | None, Query(max_length=20)] = None,
) -> ProductPage:
    # 可重复使用 attribute=接口:USB-C；所有规格必须由同一有效 SKU 同时满足。
    attributes: dict[str, str] = {}
    for item in attribute or []:
        name, separator, value = item.partition(":")
        if (
            not separator
            or not name
            or len(name) > 80
            or not value
            or len(value) > 200
            or name in attributes
        ):
            raise CommerceError(
                "INVALID_ATTRIBUTE", "属性应为不重复的名称:值，名称最长 80、值最长 200 字符。", 422
            )
        attributes[name] = value
    return await catalog.list_products(
        r, q, category, cursor, limit, min_price, max_price, brand, attributes
    )


@router.get("/products/{product_id}", response_model=ProductOut)
async def product(product_id: UUID, r: R) -> ProductOut:
    return await catalog.get_product(r, product_id)


@router.get("/cart", response_model=list[CartOut])
async def get_cart(r: R, p: Customer) -> list[CartOut]:
    return await cart.get_cart(r, p)


@router.put("/cart/items/{sku_id}", status_code=204)
async def set_cart(sku_id: UUID, data: CartIn, r: R, p: Customer) -> Response:
    await cart.set_item(r, p, sku_id, data.quantity)
    return Response(status_code=204)


@router.put("/favorites/{product_id}", status_code=204)
async def favorite(product_id: UUID, r: R, p: Customer) -> Response:
    await cart.favorite(r, p, product_id, True)
    return Response(status_code=204)


@router.delete("/favorites/{product_id}", status_code=204)
async def unfavorite(product_id: UUID, r: R, p: Customer) -> Response:
    await cart.favorite(r, p, product_id, False)
    return Response(status_code=204)


@router.post("/quotes", response_model=QuoteOut, status_code=201)
async def quote(data: QuoteIn, r: R, p: Customer) -> QuoteOut:
    return await checkout.create_quote(r, p, data)


@router.post("/orders", response_model=OrderOut, status_code=201)
async def create_order(data: OrderIn, r: R, p: Customer, key: Key) -> OrderOut:
    return await checkout.place_order(r, p, data, key)


@router.get("/orders", response_model=OrderPage)
async def list_orders(
    r: R,
    p: Customer,
    filters: Annotated[OrderFilter, Depends()],
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> OrderPage:
    return await orders.list_orders(r, p, cursor, limit, filters=filters)


@router.get("/orders/{order_id}", response_model=OrderOut)
async def order(order_id: UUID, r: R, p: Customer) -> OrderOut:
    return await orders.get_order(r, p, order_id)


@router.post("/orders/{order_id}/cancel", response_model=OrderOut)
async def cancel(order_id: UUID, data: CancelIn, r: R, p: Customer) -> OrderOut:
    return await orders.cancel(r, p, order_id, data)


@router.get("/orders/{order_id}/payment-session", response_model=PaymentOut)
async def payment_session(order_id: UUID, r: R, p: Customer) -> PaymentOut:
    return await orders.payment_session(r, p, order_id)


@router.post("/orders/{order_id}/simulate-payment", status_code=202)
async def simulate(order_id: UUID, r: R, p: Customer) -> dict[str, str]:
    await payments.simulate_payment(r, p, order_id)
    return {"status": "submitted", "message": "已提交模拟付款，请查询订单确认支付结果。"}


@router.post("/orders/{order_id}/returns", response_model=ReturnOut, status_code=201)
async def create_return(order_id: UUID, data: ReturnIn, r: R, p: Customer, key: Key) -> ReturnOut:
    return await aftersales.request_return(r, p, order_id, data, key)


@router.get("/returns/{return_id}", response_model=ReturnOut)
async def get_return(return_id: UUID, r: R, p: Customer) -> ReturnOut:
    return await aftersales.get_return(r, p, return_id)


@router.get("/events", response_model=list[EventOut])
async def list_events(
    r: R,
    p: Customer,
    after: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[EventOut]:
    return await events.list_events(r, p, after, limit)
