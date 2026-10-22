"""跨进程任务指标来自数据库快照，避免 API 进程中的计数器误报 worker 存活。"""

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from prometheus_client import generate_latest
from prometheus_client.core import GaugeMetricFamily, Metric
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from a_commerce.conf.resources import Resources
from a_commerce.models.base import now
from a_commerce.models.entities import Operation, Outbox, ProcessHeartbeat, ReconciliationCase

BACKGROUND_METRIC_NAMES = frozenset(
    {
        "commerce_operations",
        "commerce_outbox_oldest_seconds",
        "commerce_operations_overdue",
        "commerce_reconciliation_open",
        "commerce_background_alive",
    }
)


@dataclass(frozen=True)
class MetricSnapshot:
    """只为本次响应提供样本，不注册全局 Gauge，也不创建多进程 mmap 文件。"""

    families: list[Metric]

    def collect(self) -> list[Metric]:
        return self.families


async def heartbeat(r: Resources, instance: UUID, role: str, *, stopped: bool = False) -> None:
    async with r.sessions.begin() as session:
        await session.execute(
            insert(ProcessHeartbeat)
            .values(id=instance, role=role, last_seen=now(), stopped=stopped)
            .on_conflict_do_update(
                index_elements=["id"], set_={"last_seen": now(), "stopped": stopped}
            )
        )


async def collect(r: Resources) -> bytes:
    # Gauge 的独立 registry 并不能隔离 PROMETHEUS_MULTIPROC_DIR：其值仍会落入 mmap。
    # 直接构造本次采集的样本，数据库已消失的状态不会留下旧标签，也不会重复汇总。
    tasks = GaugeMetricFamily("commerce_operations", "持久任务数量", labels=["kind", "status"])
    oldest = GaugeMetricFamily("commerce_outbox_oldest_seconds", "最老未发送消息的等待秒数")
    overdue = GaugeMetricFamily("commerce_operations_overdue", "已经到期但未完成的任务数")
    cases = GaugeMetricFamily("commerce_reconciliation_open", "尚未解决的资金差异数")
    live = GaugeMetricFamily(
        "commerce_background_alive", "45 秒内有心跳的后台进程数", labels=["role"]
    )
    async with r.sessions() as session:
        rows = await session.execute(
            select(Operation.kind, Operation.status, func.count()).group_by(
                Operation.kind, Operation.status
            )
        )
        for kind, status, count in rows:
            tasks.add_metric([kind, status], count)
        first = await session.scalar(
            select(func.min(Outbox.created_at)).where(Outbox.sent_at.is_(None))
        )
        oldest.add_metric([], max(0, (now() - first).total_seconds()) if first else 0)
        overdue.add_metric(
            [],
            await session.scalar(
                select(func.count())
                .select_from(Operation)
                .where(
                    Operation.status.in_(["pending", "running", "unknown", "retry_wait"]),
                    Operation.next_attempt_at < now(),
                )
            )
            or 0,
        )
        cases.add_metric(
            [],
            await session.scalar(
                select(func.count())
                .select_from(ReconciliationCase)
                .where(ReconciliationCase.status == "open")
            )
            or 0,
        )
        for role in ("relay", "critical", "general", "maintenance"):
            live.add_metric(
                [role],
                await session.scalar(
                    select(func.count())
                    .select_from(ProcessHeartbeat)
                    .where(
                        ProcessHeartbeat.role == role,
                        ProcessHeartbeat.stopped.is_(False),
                        ProcessHeartbeat.last_seen > now() - timedelta(seconds=45),
                    )
                )
                or 0,
            )
    return generate_latest(MetricSnapshot([tasks, oldest, overdue, cases, live]))
