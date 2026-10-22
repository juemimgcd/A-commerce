"""应用工厂：生命周期创建资源，统一错误与观测，不在 API 启动时执行迁移。"""

import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
    multiprocess,
)
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.exc import TimeoutError as PoolTimeout
from starlette.exceptions import HTTPException
from starlette.middleware.base import RequestResponseEndpoint

from a_commerce import __version__
from a_commerce.conf.resources import resources
from a_commerce.conf.settings import Settings
from a_commerce.errors import CommerceError
from a_commerce.observability.background import BACKGROUND_METRIC_NAMES, MetricSnapshot, collect
from a_commerce.observability.body_limit import BodyLimitMiddleware
from a_commerce.routers import admin, callbacks, customer, operations

REQUESTS = Counter("commerce_http_requests_total", "HTTP 请求数", ["method", "route", "status"])
LATENCY = Histogram("commerce_http_seconds", "HTTP 响应耗时", ["method", "route"])
log = structlog.get_logger()


def create_app() -> FastAPI:
    settings = Settings()
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with resources(settings) as shared:
            app.state.resources = shared
            yield

    app = FastAPI(title="A-commerce 单店电商后端", version=__version__, lifespan=lifespan)
    app.add_middleware(BodyLimitMiddleware)
    app.include_router(customer.router)
    app.include_router(admin.router)
    app.include_router(callbacks.router)
    app.include_router(operations.router)

    def failure(
        request: Request, status: int, code: str, message: str, retryable: bool = False
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status,
            content={
                "error": {"code": code, "message": message, "retryable": retryable, "details": {}},
                "request_id": getattr(request.state, "request_id", ""),
            },
        )

    @app.exception_handler(CommerceError)
    async def commerce_error(request: Request, exc: CommerceError) -> JSONResponse:
        return failure(request, exc.status, exc.code, exc.message, exc.retryable)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return failure(
            request,
            exc.status_code,
            "BODY_TOO_LARGE" if exc.status_code == 413 else "HTTP_ERROR",
            str(exc.detail),
        )

    @app.exception_handler(RequestValidationError)
    @app.exception_handler(ValidationError)
    async def validation_error(request: Request, exc: Exception) -> JSONResponse:
        # 不回显 input，以免密码/完整地址进入响应或日志。
        return failure(request, 422, "VALIDATION_ERROR", "请求字段格式或取值不符合接口契约。")

    @app.exception_handler(IntegrityError)
    async def integrity_error(request: Request, exc: IntegrityError) -> JSONResponse:
        return failure(request, 409, "CONSTRAINT_CONFLICT", "资源已存在或操作违反一致性约束。")

    @app.exception_handler(DBAPIError)
    @app.exception_handler(PoolTimeout)
    async def database_error(request: Request, exc: Exception) -> JSONResponse:
        log.warning("database_unavailable", error_type=type(exc).__name__)
        return failure(request, 503, "DATABASE_BUSY", "数据库繁忙，请查询已有结果后重试。", True)

    @app.exception_handler(httpx.HTTPError)
    async def provider_error(request: Request, exc: Exception) -> JSONResponse:
        return failure(request, 503, "PROVIDER_UNAVAILABLE", "提供方暂不可用，请查询结果。", True)

    @app.middleware("http")
    async def observe(request: Request, call_next: RequestResponseEndpoint) -> Response:
        request.state.request_id = str(uuid4())
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:
            log.error(
                "request_failed", request_id=request.state.request_id, error_type=type(exc).__name__
            )
            response = failure(request, 500, "INTERNAL_ERROR", "服务内部错误，请保留请求编号。")
        route = getattr(request.scope.get("route"), "path", "unmatched")
        elapsed = time.perf_counter() - started
        REQUESTS.labels(request.method, route, str(response.status_code)).inc()
        LATENCY.labels(request.method, route).observe(elapsed)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["Cache-Control"] = "no-store"
        log.info(
            "http_request",
            request_id=request.state.request_id,
            route=route,
            status=response.status_code,
            duration=round(elapsed, 4),
        )
        return response

    @app.get("/health/live", tags=["运维"])
    async def live() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/health/ready", tags=["运维"])
    async def ready(request: Request) -> dict[str, str]:
        async with request.app.state.resources.sessions() as session:
            revision = await session.scalar(text("SELECT version_num FROM alembic_version"))
        if revision != "0005_case_fence":
            raise CommerceError("MIGRATION_REQUIRED", "数据库迁移版本不匹配。", 503)
        return {"status": "ready", "revision": revision}

    @app.get("/metrics", include_in_schema=False)
    async def metrics(request: Request) -> Response:
        # 单进程默认 registry；多进程部署必须提前设置 PROMETHEUS_MULTIPROC_DIR。
        if os.getenv("PROMETHEUS_MULTIPROC_DIR"):
            registry = CollectorRegistry()
            multiprocess.MultiProcessCollector(registry)  # type: ignore[no-untyped-call]
            # 共享目录可能保留旧版数据库 Gauge。只过滤本模块负责的五个指标族，
            # 不删除其他存活进程的文件；这些指标统一由下方实时数据库采集导出一次。
            body = generate_latest(
                MetricSnapshot(
                    [
                        metric
                        for metric in registry.collect()
                        if metric.name not in BACKGROUND_METRIC_NAMES
                    ]
                )
            )
        else:
            body = generate_latest()
        body += await collect(request.app.state.resources)
        return Response(body, media_type=CONTENT_TYPE_LATEST)

    return app
