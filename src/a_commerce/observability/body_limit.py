"""在 JSON 解析前限制请求体；流式传输同样累计大小，不只信任 Content-Length。"""

from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        maximum = (
            8 * 1024 * 1024
            if path.startswith("/api/v1/admin/products/") and path.endswith("/media")
            else 128 * 1024
        )
        consumed = 0

        async def limited_receive() -> Message:
            nonlocal consumed
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > maximum:
                    # FastAPI 会把其他解析阶段异常包装成 400；HTTPException 保留准确的 413。
                    raise HTTPException(413, "请求体超过当前接口的大小上限。")
            return message

        await self.app(scope, limited_receive, send)
