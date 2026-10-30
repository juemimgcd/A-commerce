"""公开商品响应短期复用：仍逐次限流和检查目录版本，不缓存任何私有接口。"""

import hashlib
import re
import time
from collections import OrderedDict
from dataclasses import dataclass

from prometheus_client import Counter
from pydantic import BaseModel, ValidationError
from redis.exceptions import RedisError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from a_commerce.cache.catalog import CATALOG_DEADLINE, read_generation, read_snapshot
from a_commerce.conf.resources import Resources
from a_commerce.dependencies import entry_limit
from a_commerce.errors import CommerceError

RESPONSES = Counter("commerce_catalog_response_cache_total", "公开商品响应缓存", ["outcome"])
PRODUCT = re.compile(
    r"/api/v1/customer/products/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z"
)
MAXIMUM = 65536


class CachedResponse(BaseModel):
    expires_at: float
    body: str


@dataclass(frozen=True)
class CatalogRoute:
    path: str


class CatalogResponseCacheMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.local: OrderedDict[str, tuple[str, float, bytes]] = OrderedDict()
        self.local_bytes = 0

    def remember(self, base_key: str, key: str, expires: float, body: bytes) -> None:
        previous = self.local.pop(base_key, None)
        if previous is not None:
            self.local_bytes -= len(previous[2])
        self.local[base_key] = (key, expires, body)
        self.local_bytes += len(body)
        while len(self.local) > 1024 or self.local_bytes > 8 * 1024 * 1024:
            _, removed = self.local.popitem(last=False)
            self.local_bytes -= len(removed[2])

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        route = (
            "/api/v1/customer/products"
            if path == "/api/v1/customer/products"
            else "/api/v1/customer/products/{product_id}"
            if PRODUCT.fullmatch(path)
            else None
        )
        if (
            scope["type"] != "http"
            or scope["method"] != "GET"
            or route is None
            or len(scope["query_string"]) > 4096
        ):
            await self.app(scope, receive, send)
            return
        r: Resources = scope["app"].state.resources
        if r.settings.api_role == "callbacks" or r.settings.cache_response_seconds == 0:
            await self.app(scope, receive, send)
            return
        scope["route"] = CatalogRoute(route)
        request = Request(scope)
        try:
            await entry_limit(request, r)
        except CommerceError as exc:
            response = JSONResponse(
                status_code=exc.status,
                content={
                    "error": {
                        "code": exc.code,
                        "message": exc.message,
                        "retryable": exc.retryable,
                        "details": {},
                    },
                    "request_id": getattr(request.state, "request_id", ""),
                },
                headers={"Retry-After": str(exc.retry_after)} if exc.retry_after else None,
            )
            await response(scope, receive, send)
            return
        # 仅服务端作用域标志，不能由客户端 header 伪造；未命中时避免重复扣额度。
        request.state.catalog_entry_limited = True
        digest = hashlib.sha256(path.encode() + b"?" + scope["query_string"]).hexdigest()
        source = "primary"
        base_key = f"catalog:http:v1:{source}:{digest}"
        key = base_key
        available = True
        try:
            # 只有当前请求有未过期的本地响应时才单独检查版本。
            # 未命中直接原子读取版本与快照，避免重复获取同一版本。
            # 版本无法确认时绝不返回本地副本，且访问不会延长响应绝对期限。
            local = self.local.get(base_key)
            if local is not None and time.time() < local[1]:
                generation = await read_generation(r)
                # 等待 Redis 期间，其他请求可能替换或驱逐该条目；重新检查期限。
                local = self.local.get(base_key)
                if (
                    local is not None
                    and time.time() < local[1]
                    and local[0] == f"{base_key}:logical-v1:g{generation}"
                ):
                    self.local.move_to_end(base_key)
                    RESPONSES.labels("local_hit").inc()
                    await Response(local[2], media_type="application/json")(scope, receive, send)
                    return
            if local is not None:
                del self.local[base_key]
                self.local_bytes -= len(local[2])
            key, value = await read_snapshot(r, base_key)
            if value:
                entry = CachedResponse.model_validate_json(value)
                if time.time() < entry.expires_at:
                    RESPONSES.labels("hit").inc()
                    body = entry.body.encode()
                    self.remember(base_key, key, entry.expires_at, body)
                    await Response(body, media_type="application/json")(scope, receive, send)
                    return
        except ValidationError:
            RESPONSES.labels("invalid").inc()
        except RedisError:
            available = False
            RESPONSES.labels("unavailable").inc()
        RESPONSES.labels("miss").inc()
        token = CATALOG_DEADLINE.set(None)
        chunks: list[bytes] = []
        size = 0
        cacheable = False

        async def capture(message: Message) -> None:
            nonlocal size, cacheable
            if message["type"] == "http.response.start":
                headers = dict(message.get("headers", []))
                cacheable = (
                    available
                    and message["status"] == 200
                    and headers.get(b"content-type", b"").startswith(b"application/json")
                    and b"set-cookie" not in headers
                )
            elif message["type"] == "http.response.body" and cacheable:
                body = message.get("body", b"")
                size += len(body)
                if size <= MAXIMUM:
                    chunks.append(body)
                else:
                    cacheable = False
                    chunks.clear()
            await send(message)

        try:
            await self.app(scope, receive, capture)
            deadline = CATALOG_DEADLINE.get()
            if cacheable and deadline is not None:
                now = time.time()
                # 为底层查询缓存保留提前刷新窗口，较长的 HTTP 复用不能直接跨到数据过期。
                refresh_margin = min(3, r.settings.cache_query_seconds / 2)
                expires = min(deadline - refresh_margin, now + r.settings.cache_response_seconds)
                ttl_ms = int((expires - now) * 1000)
                if ttl_ms > 0:
                    entry = CachedResponse(expires_at=expires, body=b"".join(chunks).decode())
                    try:
                        await r.redis.set(key, entry.model_dump_json(), px=ttl_ms)
                        self.remember(base_key, key, expires, b"".join(chunks))
                    except RedisError:
                        RESPONSES.labels("publish_failed").inc()
        finally:
            CATALOG_DEADLINE.reset(token)
