"""应用工厂：生命周期创建资源，统一错误与观测，不在 API 启动时执行迁移。"""

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import structlog
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    generate_latest,
    multiprocess,
)
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.exc import TimeoutError as PoolTimeout
from starlette.exceptions import HTTPException

from a_commerce import __version__
from a_commerce.cache.response import CatalogResponseCacheMiddleware
from a_commerce.conf.resources import resources
from a_commerce.conf.settings import Settings
from a_commerce.dependencies import entry_limit
from a_commerce.errors import CommerceError
from a_commerce.observability.background import BACKGROUND_METRIC_NAMES, MetricSnapshot, collect
from a_commerce.observability.body_limit import BodyLimitMiddleware
from a_commerce.observability.http import HTTPObservabilityMiddleware
from a_commerce.observability.runtime import RuntimeMetrics, pool_metrics
from a_commerce.routers import (
    admin,
    callbacks,
    customer,
    flash_sales,
    merchant,
    operations,
    storefront,
    support,
)

log = structlog.get_logger()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    structlog.configure(
        logger_factory=structlog.WriteLoggerFactory(),
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with resources(settings) as shared:
            app.state.resources = shared
            yield

    app = FastAPI(title="A-commerce 电商后端", version=__version__, lifespan=lifespan)
    app.add_middleware(BodyLimitMiddleware)
    app.add_middleware(CatalogResponseCacheMiddleware)
    app.add_middleware(HTTPObservabilityMiddleware)
    # 回调部署拥有独立进程与连接池，且不挂载买家/管理员路由；流量无法串入另一组。
    if settings.api_role in {"all", "public"}:
        for router in (
            customer.router,
            flash_sales.router,
            admin.router,
            merchant.router,
            operations.router,
            storefront.router,
            support.router,
        ):
            app.include_router(router, dependencies=[Depends(entry_limit)])
    if settings.api_role in {"all", "callbacks"}:
        app.include_router(callbacks.router)

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
        response = failure(request, exc.status, exc.code, exc.message, exc.retryable)
        if exc.retry_after is not None:
            response.headers["Retry-After"] = str(exc.retry_after)
        return response

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

    @app.get("/health/live", tags=["运维"])
    async def live() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/health/ready", tags=["运维"])
    async def ready(request: Request) -> dict[str, str]:
        # 滚动重启先标记摘流，让网关停止分配新请求，再发送 SIGTERM 等待在途请求。
        # 文件位于容器 /tmp，不进入共享文件卷；重建容器后不会继承旧实例的摘流状态。
        if await asyncio.to_thread(Path("/tmp/commerce-draining").exists):
            raise CommerceError("DRAINING", "当前实例正在退出。", 503, True)
        async with request.app.state.resources.sessions() as session:
            revision = await session.scalar(text("SELECT version_num FROM alembic_version"))
        if revision != "0009_merchants":
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
            body += generate_latest(
                MetricSnapshot(pool_metrics(request.app.state.resources.engine))
            )
        else:
            body = generate_latest(RuntimeMetrics(request.app.state.resources.engine))
        body += await collect(request.app.state.resources)
        return Response(body, media_type=CONTENT_TYPE_LATEST)

    return app
