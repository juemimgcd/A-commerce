"""回调按原始请求体验签，先限制流式请求大小，再解析业务字段。"""

from typing import Annotated

from fastapi import APIRouter, Header, Request, Response

from a_commerce.dependencies import R
from a_commerce.errors import CommerceError
from a_commerce.services import shipping
from a_commerce.services.payments import callback

router = APIRouter(prefix="/api/v1/callbacks", tags=["提供方回调"])


@router.post("/shipping/simulator", status_code=204)
async def shipping_callback(
    request: Request,
    r: R,
    x_timestamp: Annotated[str, Header(max_length=20)],
    x_signature: Annotated[str, Header(max_length=64)],
) -> Response:
    chunks = bytearray()
    async for chunk in request.stream():
        chunks.extend(chunk)
        if len(chunks) > 8192:
            raise CommerceError("BODY_TOO_LARGE", "物流回调超过大小限制。", 413)
    await shipping.callback(r, bytes(chunks), x_timestamp, x_signature)
    return Response(status_code=204)


@router.post("/payments/simulator", status_code=204)
async def payment(
    request: Request,
    r: R,
    x_timestamp: Annotated[str, Header(max_length=20)],
    x_signature: Annotated[str, Header(max_length=64)],
) -> Response:
    chunks = bytearray()
    async for chunk in request.stream():
        chunks.extend(chunk)
        if len(chunks) > 8192:
            raise CommerceError("BODY_TOO_LARGE", "回调超过大小限制。", 413)
    await callback(r, bytes(chunks), x_timestamp, x_signature)
    return Response(status_code=204)
