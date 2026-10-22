"""周期任务的时钟保存在数据库；多个 relay 用行锁竞争，不依赖进程内定时器。"""

from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from a_commerce.conf.resources import Resources
from a_commerce.models.base import now
from a_commerce.models.entities import Operation, ScheduledTask
from a_commerce.services.operations import enqueue
from a_commerce.services.reconciliation import start


async def schedule(r: Resources) -> None:
    intervals = {"housekeeping": r.settings.maintenance_interval_seconds}
    if r.settings.reconciliation_enabled:
        intervals["reconcile"] = r.settings.reconciliation_interval_seconds
    async with r.sessions.begin() as session:
        for name in sorted(intervals):
            await session.execute(
                insert(ScheduledTask)
                .values(name=name, next_run_at=now())
                .on_conflict_do_nothing(index_elements=["name"])
            )
        rows = await session.scalars(
            select(ScheduledTask)
            .where(ScheduledTask.name.in_(intervals), ScheduledTask.next_run_at <= now())
            .order_by(ScheduledTask.name)
            .with_for_update(skip_locked=True)
        )
        for task in rows:
            previous = (
                await session.get(Operation, task.operation_id) if task.operation_id else None
            )
            # 人工处理中的周期任务也不另起一轮；先复核重放，防止同一故障无限积累任务。
            if previous and previous.status != "done":
                continue
            if task.name == "reconcile":
                run = await start(session)
                operation = await session.scalar(
                    select(Operation).where(
                        Operation.kind == "reconcile", Operation.resource_id == run.id
                    )
                )
                if operation is None:
                    raise ValueError("对账任务未创建")
            else:
                operation = await enqueue(session, "housekeeping", uuid4())
            task.operation_id = operation.id
            task.next_run_at = now() + timedelta(seconds=intervals[task.name])
