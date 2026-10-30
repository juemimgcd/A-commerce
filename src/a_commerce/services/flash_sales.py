"""秒杀配置与额度锁。交易始终读取主库，不使用目录缓存或只读副本。"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a_commerce.conf.resources import Resources
from a_commerce.errors import CommerceError, missing
from a_commerce.models.base import now
from a_commerce.models.entities import SKU, Audit, FlashSale, Media, Product, Stock
from a_commerce.schemas.flash_sales import FlashSaleIn, FlashSaleOut, FlashSalePage, FlashSaleUpdate
from a_commerce.services.identity import Principal, authorize


async def lock_sales(session: AsyncSession, ids: list[UUID]) -> dict[UUID, FlashSale]:
    if not ids:
        return {}
    return {
        row.id: row
        for row in await session.scalars(
            select(FlashSale).where(FlashSale.id.in_(ids)).order_by(FlashSale.id).with_for_update()
        )
    }


def validate_sale(sale: FlashSale | None, sku_id: UUID, quantity: int) -> FlashSale:
    if sale is None or sale.sku_id != sku_id or not sale.active:
        raise CommerceError("FLASH_SALE_UNAVAILABLE", "秒杀已下架或商品不匹配，请重新选择。")
    current = now()
    if not sale.starts_at <= current < sale.ends_at:
        raise CommerceError("FLASH_SALE_INACTIVE", "秒杀尚未开始或已经结束。")
    if sale.stock - sale.claimed < quantity:
        raise CommerceError("FLASH_SALE_SOLD_OUT", "秒杀剩余数量不足，请重新选择。")
    return sale


async def sale_output(session: AsyncSession, rows: list[FlashSale]) -> list[FlashSaleOut]:
    if not rows:
        return []
    cover = (
        select(Media.id)
        .where(Media.product_id == Product.id)
        .order_by(Media.created_at, Media.id)
        .limit(1)
        .scalar_subquery()
    )
    catalog = {
        sku.id: (sku, product, stock, media_id)
        for sku, product, stock, media_id in await session.execute(
            select(SKU, Product, Stock, cover)
            .join(Product, Product.id == SKU.product_id)
            .join(Stock, Stock.sku_id == SKU.id)
            .where(SKU.id.in_([row.sku_id for row in rows]))
        )
    }
    current = now()
    output: list[FlashSaleOut] = []
    for row in rows:
        sku, product, stock, media_id = catalog[row.sku_id]
        available = min(row.stock - row.claimed, stock.on_hand - stock.reserved - stock.allocated)
        status = (
            "disabled"
            if not row.active
            else "unavailable"
            if not sku.active or not product.active
            else "ended"
            if current >= row.ends_at
            else "scheduled"
            if current < row.starts_at
            else "sold_out"
            if available <= 0
            else "active"
        )
        output.append(
            FlashSaleOut(
                **{field: getattr(row, field) for field in FlashSaleIn.model_fields},
                id=row.id,
                version=row.version,
                claimed=row.claimed,
                available=max(0, available),
                product_id=product.id,
                title=product.title,
                attributes=sku.attributes,
                regular_price_fen=sku.price_fen,
                cover_media_id=media_id,
                status=status,
            )
        )
    return output


async def list_sales(
    r: Resources,
    cursor: UUID | None,
    limit: int,
    p: Principal | None = None,
) -> FlashSalePage:
    async with r.sessions() as session:
        query = select(FlashSale)
        if p is not None:
            await authorize(session, p, "catalog")
        else:
            query = (
                query.join(SKU, SKU.id == FlashSale.sku_id)
                .join(Product)
                .where(
                    FlashSale.active.is_(True),
                    FlashSale.ends_at > now(),
                    SKU.active.is_(True),
                    Product.active.is_(True),
                )
            )
        if cursor:
            query = query.where(FlashSale.id > cursor)
        rows = list((await session.scalars(query.order_by(FlashSale.id).limit(limit + 1))).all())
        return FlashSalePage(
            items=await sale_output(session, rows[:limit]),
            next_cursor=rows[limit - 1].id if len(rows) > limit else None,
        )


async def get_sale(r: Resources, sale_id: UUID) -> FlashSaleOut:
    async with r.sessions() as session:
        sale = await session.get(FlashSale, sale_id)
        if sale is None or not sale.active:
            raise missing()
        output = (await sale_output(session, [sale]))[0]
        if output.status == "unavailable":
            raise missing()
        return output


async def save_sale(
    r: Resources,
    p: Principal,
    data: FlashSaleIn,
    sale_id: UUID | None = None,
) -> FlashSaleOut:
    async with r.sessions.begin() as session:
        await authorize(session, p, "catalog", lock=True)
        # 与下单一致：商品 → SKU → 实物库存 → 秒杀。SKU 锁串行化同规格活动的重叠检查。
        product = await session.scalar(
            select(Product)
            .where(
                Product.id == select(SKU.product_id).where(SKU.id == data.sku_id).scalar_subquery()
            )
            .with_for_update()
        )
        sku = await session.scalar(select(SKU).where(SKU.id == data.sku_id).with_for_update())
        if product is None or sku is None:
            raise missing()
        stock = await session.scalar(select(Stock).where(Stock.sku_id == sku.id).with_for_update())
        if stock is None:
            raise missing()
        sale = (await lock_sales(session, [sale_id])).get(sale_id) if sale_id else None
        if sale_id:
            if sale is None:
                raise missing()
            if sale.sku_id != sku.id:
                raise CommerceError("FLASH_SALE_SKU_FIXED", "已创建活动不能更换规格，请新增活动。")
            if not isinstance(data, FlashSaleUpdate) or sale.version != data.expected_version:
                raise CommerceError("FLASH_SALE_CONFLICT", "活动已被修改，请刷新后重新编辑。")
        claimed = sale.claimed if sale else 0
        if data.stock < claimed:
            raise CommerceError("FLASH_SALE_STOCK_OCCUPIED", "活动数量不能少于已占用及成交数量。")
        if data.active:
            if not product.active or not sku.active:
                raise CommerceError("PRODUCT_UNAVAILABLE", "请先上架商品和规格。")
            if data.price_fen > sku.price_fen:
                raise CommerceError("FLASH_SALE_PRICE", "秒杀价不能高于当前商品售价。", 422)
            if data.stock - claimed > stock.on_hand - stock.reserved - stock.allocated:
                raise CommerceError("OUT_OF_STOCK", "秒杀剩余额度超过可售实物库存，请先补库存。")
            overlap = await session.scalar(
                select(FlashSale.id)
                .where(
                    FlashSale.sku_id == sku.id,
                    FlashSale.active.is_(True),
                    FlashSale.id != sale_id,
                    FlashSale.starts_at < data.ends_at,
                    FlashSale.ends_at > data.starts_at,
                )
                .limit(1)
            )
            if overlap:
                raise CommerceError("FLASH_SALE_OVERLAP", "同一规格不能存在时间重叠的已上架秒杀。")
        values = data.model_dump(exclude={"expected_version"})
        if sale is None:
            sale = FlashSale(**values)
            session.add(sale)
        else:
            for key, value in values.items():
                setattr(sale, key, value)
            sale.version += 1
        await session.flush()
        session.add(
            Audit(
                actor_id=p.id,
                action="flash_sale.save",
                resource_id=sale.id,
                detail=(
                    f"版本 {sale.version}，价格 {sale.price_fen} 分，"
                    f"额度 {sale.stock}，上架 {sale.active}"
                ),
            )
        )
        return (await sale_output(session, [sale]))[0]
