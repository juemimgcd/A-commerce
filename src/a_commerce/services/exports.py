"""异步生成不含私人信息的资金日报 CSV，下载必须属于创建该导出的管理员。"""

from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

from pydantic import model_validator
from sqlalchemy import select

from a_commerce.conf.resources import Resources
from a_commerce.errors import CommerceError, missing
from a_commerce.models.entities import ExportJob
from a_commerce.schemas.api import Schema
from a_commerce.services.identity import Principal, authorize
from a_commerce.services.operations import enqueue


class ExportIn(Schema):
    start: date
    end: date

    @model_validator(mode="after")
    def bounded_range(self) -> "ExportIn":
        if self.end < self.start or self.end - self.start > timedelta(days=366):
            raise ValueError("最多导出 366 天资金汇总")
        return self


class ExportOut(ExportIn):
    id: UUID
    status: str


async def create_export(r: Resources, p: Principal, data: ExportIn) -> ExportOut:
    async with r.sessions.begin() as session:
        await authorize(session, p, "audit", lock=True)
        job = ExportJob(staff_id=p.id, start=data.start, end=data.end)
        session.add(job)
        await session.flush()
        await enqueue(session, "export", job.id)
        return ExportOut.model_validate(job)


async def get_export(r: Resources, p: Principal, export_id: UUID) -> ExportOut:
    async with r.sessions() as session:
        await authorize(session, p, "audit")
        job = await session.scalar(
            select(ExportJob).where(ExportJob.id == export_id, ExportJob.staff_id == p.id)
        )
        if job is None:
            raise missing()
        return ExportOut.model_validate(job)


async def download(r: Resources, p: Principal, export_id: UUID) -> Path:
    async with r.sessions() as session:
        await authorize(session, p, "audit")
        job = await session.scalar(
            select(ExportJob).where(ExportJob.id == export_id, ExportJob.staff_id == p.id)
        )
        if job is None:
            raise missing()
        if job.status != "done" or job.file_key is None:
            raise CommerceError("EXPORT_NOT_READY", "导出文件尚未生成。")
        return r.settings.files_directory / "exports" / job.file_key
