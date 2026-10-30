"""店主经营查询和成本维护；不提升为平台员工身份。"""

from uuid import UUID

from sqlalchemy import select

from a_commerce.conf.resources import Resources
from a_commerce.crud.queries import product_outputs, return_outputs
from a_commerce.errors import CommerceError, missing
from a_commerce.models.entities import SKU, Media, Order, Product, ReturnRequest, Stock
from a_commerce.schemas.api import ReturnOut
from a_commerce.schemas.merchant import CostIn, CostOut, MerchantProductOut, MerchantProductPage
from a_commerce.services.identity import Principal
from a_commerce.services.media import MediaOut
from a_commerce.services.shop_access import audit, scope


async def products(
    r: Resources, p: Principal, shop_id: UUID, cursor: UUID | None, limit: int, q: str = ""
) -> MerchantProductPage:
    async with r.sessions() as session:
        await scope(session, p, shop_id, "catalog")
        query = select(Product).where(Product.shop_id == shop_id)
        if cursor:
            query = query.where(Product.id > cursor)
        if q:
            query = query.where(Product.title.icontains(q, autoescape=True))
        records = list(await session.scalars(query.order_by(Product.id).limit(limit + 1)))
        output = await product_outputs(session, records[:limit])
        # 管理界面展示商品原始上架开关；公开有效性还取决于店铺状态。
        for result, record in zip(output, records[:limit], strict=True):
            result.active = record.active
        costs: dict[UUID, list[CostOut]] = {}
        for sku, stock in await session.execute(
            select(SKU, Stock)
            .join(Stock, Stock.sku_id == SKU.id)
            .where(SKU.product_id.in_([row.id for row in records[:limit]]))
        ):
            costs.setdefault(sku.product_id, []).append(
                CostOut(
                    sku_id=sku.id,
                    cost_fen=sku.cost_fen,
                    cost_version=sku.cost_version,
                    on_hand=stock.on_hand,
                    reserved=stock.reserved,
                    allocated=stock.allocated,
                )
            )
        return MerchantProductPage(
            items=[MerchantProductOut(product=row, costs=costs.get(row.id, [])) for row in output],
            next_cursor=records[limit - 1].id if len(records) > limit else None,
        )


async def cost(r: Resources, p: Principal, shop_id: UUID, sku_id: UUID, data: CostIn) -> CostOut:
    async with r.sessions.begin() as session:
        await scope(session, p, shop_id, "catalog", write=True, catalog=True)
        product = await session.scalar(
            select(Product)
            .join(SKU)
            .where(SKU.id == sku_id, Product.shop_id == shop_id)
            .with_for_update(of=Product)
        )
        if product is None:
            raise missing()
        sku = (await session.scalars(select(SKU).where(SKU.id == sku_id).with_for_update())).one()
        if sku.cost_version != data.expected_version:
            raise CommerceError("VERSION_CONFLICT", "成本已变化，请刷新后提交。", 409)
        sku.cost_fen, sku.cost_version = data.cost_fen, sku.cost_version + 1
        stock = (await session.scalars(select(Stock).where(Stock.sku_id == sku_id))).one()
        session.add(audit(p, shop_id, "sku.cost", sku_id, "更新单位成本快照来源"))
        return CostOut(
            sku_id=sku.id,
            cost_fen=sku.cost_fen,
            cost_version=sku.cost_version,
            on_hand=stock.on_hand,
            reserved=stock.reserved,
            allocated=stock.allocated,
        )


async def returns(
    r: Resources, p: Principal, shop_id: UUID, cursor: UUID | None, limit: int
) -> list[ReturnOut]:
    async with r.sessions() as session:
        await scope(session, p, shop_id, "aftersales")
        query = select(ReturnRequest).join(Order).where(Order.shop_id == shop_id)
        if cursor:
            query = query.where(ReturnRequest.id > cursor)
        records = list(await session.scalars(query.order_by(ReturnRequest.id).limit(limit)))
        return await return_outputs(session, records, r.settings.shop.return_address)


async def media_list(r: Resources, p: Principal, shop_id: UUID, product_id: UUID) -> list[MediaOut]:
    async with r.sessions() as session:
        await scope(session, p, shop_id, "catalog")
        if not await session.scalar(
            select(Product.id).where(Product.id == product_id, Product.shop_id == shop_id)
        ):
            raise missing()
        return [
            MediaOut.model_validate(row)
            for row in await session.scalars(
                select(Media)
                .where(Media.product_id == product_id)
                .order_by(Media.created_at, Media.id)
                .limit(20)
            )
        ]
