"""直接包装 ASGI 响应消息，避免为每个请求创建任务和内存流。"""

import time
from uuid import uuid4

import structlog
from prometheus_client import Counter, Histogram
from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUESTS = Counter("commerce_http_requests_total", "HTTP 请求数", ["method", "route", "status"])
LATENCY = Histogram("commerce_http_seconds", "HTTP 响应耗时", ["method", "route"])
log = structlog.get_logger()


class HTTPObservabilityMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        started = time.perf_counter()
        response_started = False

        async def observed_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                route = getattr(scope.get("route"), "path", "unmatched")
                elapsed = time.perf_counter() - started
                status = message["status"]
                REQUESTS.labels(scope["method"], route, str(status)).inc()
                LATENCY.labels(scope["method"], route).observe(elapsed)
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = request_id
                headers["Cache-Control"] = "no-store"
                log.info(
                    "http_request",
                    request_id=request_id,
                    route=route,
                    status=status,
                    duration=round(elapsed, 4),
                )
            await send(message)

        try:
            await self.app(scope, receive, observed_send)
        except Exception as exc:
            log.error("request_failed", request_id=request_id, error_type=type(exc).__name__)
            # 流式响应已经开始时不能再发送一份 500；交给服务器关闭异常响应。
            if response_started:
                raise
            response = JSONResponse(
                status_code=500,
                content={
                    "error": {
                        "code": "INTERNAL_ERROR",
                        "message": "服务内部错误，请保留请求编号。",
                        "retryable": False,
                        "details": {},
                    },
                    "request_id": request_id,
                },
            )
            await response(scope, receive, observed_send)
