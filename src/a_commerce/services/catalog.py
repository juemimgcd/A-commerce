"""目录、商品版本与库存调整；所有更新与下单遵守相同的加锁顺序。"""

import hashlib
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a_commerce.cache.catalog import cached_catalog
from a_commerce.conf.resources import Resources
from a_commerce.crud.queries import product_outputs
from a_commerce.errors import CommerceError, missing
from a_commerce.models.entities import (
    SKU,
    Audit,
    CatalogChange,
    Category,
    IdempotencyRecord,
    Product,
    Stock,
    StockMovement,
)
from a_commerce.schemas.api import (
    CategoryIn,
    CategoryOut,
    ProductIn,
    ProductOut,
    ProductPage,
    SKUIn,
    SKUOut,
    StockAdjustment,
    StockOut,
)
from a_commerce.services.identity import Principal, authorize
from a_commerce.services.operations import enqueue


async def admin_products(
    r: Resources, p: Principal, query: str, cursor: UUID | None, limit: int
) -> ProductPage:
    async with r.sessions() as session:
        await authorize(session, p, "catalog")
        statement = select(Product)
        if query:
            statement = statement.where(Product.title.icontains(query, autoescape=True))
        if cursor:
            statement = statement.where(Product.id > cursor)
        rows = list((await session.scalars(statement.order_by(Product.id).limit(limit + 1))).all())
        output = await product_outputs(session, rows[:limit])
        return ProductPage(items=output, next_cursor=output[-1].id if len(rows) > limit else None)


async def record_change(session: AsyncSession, product: Product) -> ProductOut:
    output = (await product_outputs(session, [product]))[0]
    change = CatalogChange(
        product_id=product.id, version=product.version, snapshot=output.model_dump(mode="json")
    )
    session.add(change)
    await session.flush()
    await enqueue(session, "cache", change.id)
    return output


async def categories(r: Resources) -> list[CategoryOut]:
    async with r.sessions() as session:
        return [
            CategoryOut.model_validate(row)
            for row in await session.scalars(select(Category).order_by(Category.name).limit(200))
        ]


async def create_category(r: Resources, p: Principal, data: CategoryIn) -> CategoryOut:
    async with r.sessions.begin() as session:
        await authorize(session, p, "catalog", lock=True)
        category = Category(name=data.name)
        session.add(category)
        await session.flush()
        session.add(
            Audit(
                actor_id=p.id, action="category.create", resource_id=category.id, detail="新增分类"
            )
        )
        return CategoryOut.model_validate(category)


async def list_products(
    r: Resources,
    query: str,
    category: UUID | None,
    cursor: UUID | None,
    limit: int,
    min_price: int,
    max_price: int,
    brand: str = "",
    attributes: dict[str, str] | None = None,
) -> ProductPage:
    if max_price < min_price:
        raise CommerceError("PRICE_RANGE", "最高价格不能低于最低价格。", 422)

    async def load() -> ProductPage:
        async with r.sessions() as session:
            stmt = select(Product).where(Product.active.is_(True))
            if query:
                stmt = stmt.where(Product.title.icontains(query, autoescape=True))
            if category:
                stmt = stmt.where(Product.category_id == category)
            if brand:
                stmt = stmt.where(Product.brand == brand)
            if cursor:
                stmt = stmt.where(Product.id > cursor)
            stmt = stmt.where(
                select(SKU.id)
                .where(
                    SKU.product_id == Product.id,
                    SKU.active.is_(True),
                    SKU.price_fen.between(min_price, max_price),
                    SKU.attributes.contains(attributes or {}),
                )
                .exists()
            )
            products = list(
                (await session.scalars(stmt.order_by(Product.id).limit(limit + 1))).all()
            )
            more = len(products) > limit
            output = await product_outputs(session, products[:limit])
            return ProductPage(items=output, next_cursor=output[-1].id if more else None)

    # 只缓存无搜索词的第一页，避免任意查询组合生成海量缓存 key。
    if query or cursor or brand or attributes or min_price != 0 or max_price != 100000000:
        return await load()
    key = f"catalog:v1:{category}:{limit}:{min_price}:{max_price}"
    return await cached_catalog(r, key, load)


async def get_product(r: Resources, product_id: UUID) -> ProductOut:
    async def load() -> ProductPage:
        async with r.sessions() as session:
            product = await session.get(Product, product_id)
            if product is None or not product.active:
                return ProductPage(items=[])
            return ProductPage(items=await product_outputs(session, [product]))

    page = await cached_catalog(r, f"catalog:product:{product_id}", load)
    if not page.items:
        raise missing()
    return page.items[0]


async def save_product(
    r: Resources, p: Principal, data: ProductIn, product_id: UUID | None = None
) -> ProductOut:
    async with r.sessions.begin() as session:
        await authorize(session, p, "catalog", lock=True)
        if await session.get(Category, data.category_id) is None:
            raise missing()
        if product_id is None:
            product = Product(**data.model_dump())
            session.add(product)
        else:
            existing = await session.scalar(
                select(Product).where(Product.id == product_id).with_for_update()
            )
            if existing is None:
                raise missing()
            product = existing
            for key, value in data.model_dump().items():
                setattr(product, key, value)
            product.version += 1
        await session.flush()
        session.add(
            Audit(
                actor_id=p.id,
                action="product.save",
                resource_id=product.id,
                detail=f"商品版本 {product.version}",
            )
        )
        return await record_change(session, product)


async def save_sku(
    r: Resources, p: Principal, product_id: UUID, data: SKUIn, sku_id: UUID | None = None
) -> SKUOut:
    async with r.sessions.begin() as session:
        await authorize(session, p, "catalog", lock=True)
        product = await session.scalar(
            select(Product).where(Product.id == product_id).with_for_update()
        )
        if product is None:
            raise missing()
        if sku_id is None:
            sku = SKU(product_id=product.id, **data.model_dump())
            session.add(sku)
            await session.flush()
            stock = Stock(sku_id=sku.id)
            session.add(stock)
        else:
            existing = await session.scalar(
                select(SKU).where(SKU.id == sku_id, SKU.product_id == product_id).with_for_update()
            )
            if existing is None:
                raise missing()
            sku = existing
            for key, value in data.model_dump().items():
                setattr(sku, key, value)
            sku.version += 1
        product.version += 1
        await session.flush()
        session.add(
            Audit(
                actor_id=p.id,
                action="sku.save",
                resource_id=sku.id,
                detail=f"SKU 版本 {sku.version}",
            )
        )
        result = await record_change(session, product)
        return next(item for item in result.skus if item.id == sku.id)


async def adjust_stock(
    r: Resources, p: Principal, sku_id: UUID, data: StockAdjustment, key: str
) -> StockOut:
    fingerprint = hashlib.sha256(f"{sku_id}:{data.model_dump_json()}".encode()).hexdigest()
    async with r.sessions.begin() as session:
        await authorize(session, p, "catalog", lock=True)
        stock = await session.scalar(select(Stock).where(Stock.sku_id == sku_id).with_for_update())
        if stock is None:
            raise missing()
        previous = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.actor_id == p.id,
                IdempotencyRecord.action == "inventory.adjust",
                IdempotencyRecord.key == key,
            )
        )
        if previous:
            if previous.fingerprint != fingerprint:
                raise CommerceError("IDEMPOTENCY_CONFLICT", "同一幂等键不能用于不同库存调整。")
            return StockOut.model_validate(stock)
        if stock.on_hand + data.delta < stock.reserved + stock.allocated:
            raise CommerceError("STOCK_OCCUPIED", "调整后库存不能少于已占用库存。")
        stock.on_hand += data.delta
        session.add_all(
            [
                StockMovement(
                    reference=f"adjust:{p.id}:{key}",
                    sku_id=sku_id,
                    on_hand_delta=data.delta,
                    reserved_delta=0,
                    allocated_delta=0,
                    reason=data.reason,
                ),
                IdempotencyRecord(
                    actor_id=p.id,
                    action="inventory.adjust",
                    key=key,
                    fingerprint=fingerprint,
                    result_id=stock.id,
                ),
                Audit(
                    actor_id=p.id, action="inventory.adjust", resource_id=sku_id, detail=data.reason
                ),
            ]
        )
        return StockOut.model_validate(stock)
