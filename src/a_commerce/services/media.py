"""商品媒体在真实解码通过后才关联商品，外部对象写入不占数据库事务。"""

import asyncio
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import select

from a_commerce.conf.resources import Resources
from a_commerce.errors import CommerceError, missing
from a_commerce.integrations.storage import delete_object, put, sanitize_image
from a_commerce.models.base import now
from a_commerce.models.entities import Audit, Media, Product, StoredObject
from a_commerce.schemas.api import Schema
from a_commerce.services.catalog import record_change
from a_commerce.services.identity import Principal, authorize


class MediaOut(Schema):
    id: UUID
    product_id: UUID
    content_type: str
    size_bytes: int


async def upload(r: Resources, p: Principal, product_id: UUID, body: bytes) -> MediaOut:
    if len(body) > 8 * 1024 * 1024:
        raise CommerceError("MEDIA_TOO_LARGE", "图片不能超过 8 MiB。", 413)
    async with r.sessions() as session:
        await authorize(session, p, "catalog")
        if await session.get(Product, product_id) is None:
            raise missing()
    # 与密码线程分开使用有界回源槽位，避免任意上传无限占用解码线程和内存。
    async with asyncio.timeout(15), r.origin_slots:
        try:
            content, content_type, extension = await asyncio.to_thread(sanitize_image, body)
        except ValueError as exc:
            raise CommerceError("INVALID_MEDIA", str(exc), 422) from exc
        media_id = uuid4()
        key = f"media/{media_id}.{extension}"
        # 外部写入之前登记意图；进程在上传后、关联商品前崩溃，清理任务仍能找到对象。
        async with r.sessions.begin() as session:
            session.add(StoredObject(storage_key=key, backend=r.settings.storage_backend))
        await asyncio.to_thread(put, r.settings, key, content, content_type)
    async with r.sessions.begin() as session:
        await authorize(session, p, "catalog", lock=True)
        product = await session.scalar(
            select(Product).where(Product.id == product_id).with_for_update()
        )
        if product is None:
            raise missing()
        stored = (
            await session.scalars(
                select(StoredObject).where(StoredObject.storage_key == key).with_for_update()
            )
        ).one()
        if stored.status != "pending":
            raise CommerceError("UPLOAD_EXPIRED", "上传已进入清理，请重新上传。")
        stored.status = "ready"
        record = Media(
            id=media_id,
            product_id=product_id,
            staff_id=p.id,
            storage_key=key,
            content_type=content_type,
            size_bytes=len(content),
            backend=r.settings.storage_backend,
        )
        session.add(record)
        product.version += 1
        session.add(
            Audit(
                actor_id=p.id,
                action="media.upload",
                resource_id=media_id,
                detail="图片已解码、去元数据并关联商品",
            )
        )
        await session.flush()
        await record_change(session, product)
        return MediaOut.model_validate(record)


async def clean_orphans(r: Resources) -> bool:
    """每次处理最多两个未关联对象；先标记再删除，防止清理与迟到的关联事务竞争。"""
    async with r.sessions.begin() as session:
        rows = list(
            await session.scalars(
                select(StoredObject)
                .where(
                    StoredObject.status.in_(["pending", "deleting"]),
                    StoredObject.created_at
                    < now() - timedelta(seconds=r.settings.orphan_grace_seconds),
                    ~select(Media.id).where(Media.storage_key == StoredObject.storage_key).exists(),
                )
                .order_by(StoredObject.created_at)
                .limit(2)
                .with_for_update(skip_locked=True)
            )
        )
        targets = [(row.id, row.storage_key, row.backend) for row in rows]
        for row in rows:
            row.status = "deleting"
    for object_id, key, backend in targets:
        await asyncio.to_thread(delete_object, r.settings, key, backend)
        async with r.sessions.begin() as session:
            stored = await session.scalar(
                select(StoredObject).where(StoredObject.id == object_id).with_for_update()
            )
            if stored and stored.status == "deleting":
                stored.status, stored.deleted_at = "deleted", now()
    return len(targets) == 2


async def get_media(r: Resources, media_id: UUID) -> Media:
    async with r.sessions() as session:
        record = await session.scalar(
            select(Media)
            .join(Product, Product.id == Media.product_id)
            .where(Media.id == media_id, Product.active.is_(True))
        )
        if record is None:
            raise missing()
        return record


async def list_media(r: Resources, product_id: UUID) -> list[MediaOut]:
    async with r.sessions() as session:
        product = await session.get(Product, product_id)
        if product is None or not product.active:
            raise missing()
        rows = await session.scalars(
            select(Media).where(Media.product_id == product_id).order_by(Media.created_at).limit(20)
        )
        return [MediaOut.model_validate(row) for row in rows]
