"""有界标签的 SQL/连接池指标；不导出 SQL 文本、参数、账号或商品 ID。"""

import time
from collections.abc import Iterable
from dataclasses import dataclass

from prometheus_client import REGISTRY, Counter, Histogram
from prometheus_client.core import GaugeMetricFamily, Metric
from sqlalchemy import Connection, event
from sqlalchemy.engine import ExceptionContext, ExecutionContext
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import AsyncAdaptedQueuePool, ConnectionPoolEntry, QueuePool

POOL_ACQUIRE = Histogram("commerce_db_acquire_seconds", "获取连接耗时，包含排队与新连接建立")
POOL_TIMEOUTS = Counter("commerce_db_pool_timeouts_total", "连接池等待超时次数")
SQL_SECONDS = Histogram("commerce_db_query_seconds", "SQL 执行耗时，包含数据库锁等待")
SQL_ERRORS = Counter("commerce_db_errors_total", "数据库执行错误数")


class MeasuredPool(AsyncAdaptedQueuePool):
    # 当前固定 SQLAlchemy 版本的池获取入口；checkout 事件发生时排队已结束。
    # 此指标包含建立连接，不冒充纯排队时间。
    def _do_get(self) -> ConnectionPoolEntry:
        with POOL_ACQUIRE.time():
            try:
                return super()._do_get()
            except PoolTimeout:
                POOL_TIMEOUTS.inc()
                raise


def instrument(engine: AsyncEngine) -> None:
    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def before(
        conn: Connection,
        cursor: object,
        statement: str,
        parameters: object,
        context: ExecutionContext,
        executemany: bool,
    ) -> None:
        conn.info["commerce_query_started"] = time.perf_counter()

    @event.listens_for(engine.sync_engine, "after_cursor_execute")
    def after(
        conn: Connection,
        cursor: object,
        statement: str,
        parameters: object,
        context: ExecutionContext,
        executemany: bool,
    ) -> None:
        started = conn.info.pop("commerce_query_started", None)
        if isinstance(started, float):
            SQL_SECONDS.observe(time.perf_counter() - started)

    @event.listens_for(engine.sync_engine, "handle_error")
    def failed(context: ExceptionContext) -> None:
        SQL_ERRORS.inc()
        if context.connection is not None:
            started = context.connection.info.pop("commerce_query_started", None)
            if isinstance(started, float):
                SQL_SECONDS.observe(time.perf_counter() - started)


@dataclass(frozen=True)
class RuntimeMetrics:
    engine: AsyncEngine

    def collect(self) -> Iterable[Metric]:
        yield from REGISTRY.collect()
        yield from pool_metrics(self.engine)


def pool_metrics(engine: AsyncEngine) -> list[Metric]:
    pool = engine.sync_engine.pool
    if not isinstance(pool, QueuePool):
        return []
    used = GaugeMetricFamily("commerce_db_connections_used", "本进程已借出的数据库连接")
    used.add_metric([], pool.checkedout())
    capacity = GaugeMetricFamily("commerce_db_pool_size", "本进程固定连接池容量")
    capacity.add_metric([], pool.size())
    return [used, capacity]
