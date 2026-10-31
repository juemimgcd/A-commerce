"""低优先级邮件/导出任务：独立进程消费，慢邮件与文件操作不会挤占支付队列。"""

import asyncio
import csv
import io
from datetime import timedelta
from email.message import EmailMessage
from pathlib import Path
from uuid import UUID

from redis.exceptions import RedisError
from sqlalchemy import select

from a_commerce.conf.resources import Resources
from a_commerce.integrations.mail import send
from a_commerce.models.base import now
from a_commerce.models.entities import (
    CatalogChange,
    DailyReport,
    Event,
    ExportJob,
    MailJob,
    Operation,
    PasswordReset,
    User,
)
from a_commerce.services.password_reset import token_for


def write_csv(path: Path, rows: list[tuple[str, int, int, int]]) -> None:
    # 仅输出日期与整数金额，不包含用户输入，避免地址泄露和 CSV 公式注入。
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["日期（UTC）", "实收（分）", "实退（分）", "净收款（分）"])
    writer.writerows(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(stream.getvalue(), encoding="utf-8-sig")
    temporary.replace(path)


async def execute(
    r: Resources, operation_id: UUID, fence: int, kind: str, resource_id: UUID
) -> None:
    try:
        if kind == "cache":
            async with r.sessions() as session:
                if await session.get(CatalogChange, resource_id) is None:
                    raise ValueError("目录变更记录不存在")
            # 重复失效只多增加一次代数，不影响业务。旧代缓存由短 TTL 回收。
            await r.redis.incr("catalog:generation")
        elif kind == "export":
            async with r.sessions() as session:
                export = await session.get(ExportJob, resource_id)
                if export is None:
                    raise ValueError("导出任务不存在")
                reports = await session.scalars(
                    select(DailyReport)
                    .where(DailyReport.day.between(export.start, export.end))
                    .order_by(DailyReport.day)
                )
                rows = [
                    (str(row.day), row.sales_fen, row.refunds_fen, row.sales_fen - row.refunds_fen)
                    for row in reports
                ]
            await asyncio.to_thread(
                write_csv,
                r.settings.files_directory / "exports" / f"{resource_id}-{fence}.csv",
                rows,
            )
        else:
            async with r.sessions() as session:
                job = await session.get(MailJob, resource_id)
                if job is None:
                    raise ValueError("邮件任务不存在")
                user = await session.get(User, job.user_id)
                if user is None or not user.active:
                    raise ValueError("邮件收件账号已失效")
                message = EmailMessage()
                message["To"] = user.email
                message["Message-ID"] = f"<{job.id}@a-commerce.local>"
                if job.reset_id:
                    reset = await session.get(PasswordReset, job.reset_id)
                    if reset is None or reset.used or reset.expires_at <= now():
                        raise ValueError("密码重置请求已过期")
                    message["Subject"] = "A-commerce 密码重置"
                    message.set_content(
                        "请打开以下链接设置新密码，30 分钟内有效：\n"
                        + str(r.settings.web_origin).rstrip("/")
                        + "/reset-password?token="
                        + token_for(r.settings, reset.id)
                    )
                else:
                    event = await session.get(Event, job.event_id)
                    if event is None:
                        raise ValueError("通知事件不存在")
                    message["Subject"] = "A-commerce 订单进度通知"
                    message.set_content(
                        f"订单 {event.order_id} 有新进度：{event.kind}。\n"
                        "请登录后查询当前订单状态。"
                    )
            async with asyncio.timeout(20):
                await send(r.settings, message)
        async with r.sessions.begin() as session:
            operation = await session.scalar(
                select(Operation).where(Operation.id == operation_id).with_for_update()
            )
            if operation is None or operation.fence != fence or operation.status != "running":
                return
            operation.status = "done"
            operation.lease_until = None
            if kind == "export":
                export = await session.get(ExportJob, resource_id)
                if export:
                    export.status = "done"
                    export.file_key = f"{resource_id}-{fence}.csv"
            elif kind == "mail":
                job = await session.get(MailJob, resource_id)
                if job:
                    job.status = "sent"
    except (OSError, TimeoutError, ValueError, RedisError) as exc:
        # SMTP 错误的 adapter 会转成 OSError；数据库异常不在这里吞掉，外层不得 ack。
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
            operation.last_error = type(exc).__name__
            operation.lease_until = None
            operation.next_attempt_at = now() + timedelta(seconds=min(300, 2**operation.attempts))
