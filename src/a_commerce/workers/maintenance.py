"""维护任务与支付消费隔离；每次仅推进一页，数据库提交游标后再确认消息。"""

from datetime import timedelta
from uuid import UUID

import httpx
from sqlalchemy import select

from a_commerce.conf.resources import Resources
from a_commerce.integrations.payments.ledger import page
from a_commerce.models.base import now
from a_commerce.models.entities import Operation, ReconciliationRun
from a_commerce.services.event_recovery import archive_batch
from a_commerce.services.media import clean_orphans
from a_commerce.services.reconciliation import check_local, consume_page


async def execute(
    r: Resources, operation_id: UUID, fence: int, kind: str, resource_id: UUID
) -> None:
    try:
        result = None
        more = False
        if kind == "reconcile":
            async with r.sessions() as session:
                run = await session.get(ReconciliationRun, resource_id)
                if run is None:
                    raise ValueError("对账轮次不存在")
                status, after, through = run.status, run.after_sequence, run.through_sequence
            if status == "provider":
                result = await page(r, after, through)
        else:
            more = await archive_batch(r)
            more = await clean_orphans(r) or more
        async with r.sessions.begin() as session:
            operation = await session.scalar(
                select(Operation).where(Operation.id == operation_id).with_for_update()
            )
            if operation is None or operation.fence != fence or operation.status != "running":
                return
            if kind == "reconcile":
                run = await session.get(ReconciliationRun, resource_id)
                if run is None:
                    raise ValueError("对账轮次不存在")
                if result is not None:
                    await consume_page(session, run, result, r.settings.merchant_id)
                elif run.status != "done":
                    await check_local(session, run)
                more = run.status != "done"
            operation.status = "retry_wait" if more else "done"
            operation.next_attempt_at = now() + timedelta(seconds=1)
            operation.lease_until, operation.last_error, operation.attempts = None, None, 0
    except (httpx.HTTPError, OSError, TimeoutError, ValueError) as exc:
        async with r.sessions.begin() as session:
            operation = await session.scalar(
                select(Operation).where(Operation.id == operation_id).with_for_update()
            )
            if operation is None or operation.fence != fence or operation.status != "running":
                return
            operation.attempts += 1
            operation.status = (
                "manual_review"
                if (
                    isinstance(exc, ValueError)
                    or operation.attempts >= r.settings.operation_max_attempts
                )
                else "retry_wait"
            )
            operation.last_error, operation.lease_until = type(exc).__name__, None
            operation.next_attempt_at = now() + timedelta(
                seconds=min(300, 2 ** min(operation.attempts, 8))
            )
