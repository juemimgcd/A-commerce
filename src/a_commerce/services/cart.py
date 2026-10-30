"""购物车只保存购买意向，不占库存；展示时始终重新读取价格与商品有效性。"""

from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert

from a_commerce.conf.resources import Resources
from a_commerce.crud.queries import product_covers, product_outputs
from a_commerce.errors import CommerceError, missing
from a_commerce.models.entities import SKU, CartItem, Favorite, Product, Shop, Stock, User
from a_commerce.schemas.api import CartOut, ProductPage
from a_commerce.services.identity import Principal, authorize

MAX_CART_ITEMS = 100


async def get_cart(r: Resources, p: Principal) -> list[CartOut]:
    async with r.sessions() as session:
        await authorize(session, p)
        rows = list(
            (
                await session.execute(
                    select(CartItem, SKU, Product, Stock)
                    .join(SKU, SKU.id == CartItem.sku_id)
                    .join(Product, Product.id == SKU.product_id)
                    .join(Stock, Stock.sku_id == SKU.id)
                    .where(CartItem.user_id == p.id)
                    .order_by(CartItem.id)
                    .limit(MAX_CART_ITEMS)
                )
            ).all()
        )
        shops = {
            s.id: s
            for s in await session.scalars(
                select(Shop).where(Shop.id.in_([product.shop_id for _, _, product, _ in rows]))
            )
        }
        covers = await product_covers(session, [product.id for _, _, product, _ in rows])
        return [
            CartOut(
                shop_id=product.shop_id,
                shop_name=shops[product.shop_id].name,
                sku_id=sku.id,
                quantity=item.quantity,
                title=product.title,
                price_fen=sku.price_fen,
                available=stock.on_hand - stock.reserved - stock.allocated,
                active=sku.active and product.active and shops[product.shop_id].status == "active",
                product_id=product.id,
                attributes=sku.attributes,
                cover_media_id=covers.get(product.id),
            )
            for item, sku, product, stock in rows
        ]


async def set_item(r: Resources, p: Principal, sku_id: UUID, quantity: int) -> None:
    async with r.sessions.begin() as session:
        # 先排他锁定账号，再鉴权/修改购物车；计数与插入串行，两个新增请求
        # 不会同时看见第 99 项后都通过上限检查，也不需要升级已有共享锁。
        await session.scalar(select(User.id).where(User.id == p.id).with_for_update())
        await authorize(session, p)
        if quantity == 0:
            await session.execute(
                delete(CartItem).where(CartItem.user_id == p.id, CartItem.sku_id == sku_id)
            )
            return
        if await session.get(SKU, sku_id) is None:
            raise missing()
        existing = await session.scalar(
            select(CartItem.id).where(CartItem.user_id == p.id, CartItem.sku_id == sku_id)
        )
        if existing is None:
            count = await session.scalar(
                select(func.count()).select_from(CartItem).where(CartItem.user_id == p.id)
            )
            if count is not None and count >= MAX_CART_ITEMS:
                raise CommerceError(
                    "CART_LIMIT_REACHED",
                    f"购物车最多保留 {MAX_CART_ITEMS} 种商品规格，请先移除已有条目。",
                )
        # 满额时仍允许修改已有条目的数量；删除已在前面处理，不受容量限制。
        await session.execute(
            insert(CartItem)
            .values(user_id=p.id, sku_id=sku_id, quantity=quantity)
            .on_conflict_do_update(
                index_elements=["user_id", "sku_id"], set_={"quantity": quantity}
            )
        )


async def favorite(r: Resources, p: Principal, product_id: UUID, enabled: bool) -> None:
    async with r.sessions.begin() as session:
        await authorize(session, p, lock=True)
        if await session.get(Product, product_id) is None:
            raise missing()
        if enabled:
            await session.execute(
                insert(Favorite)
                .values(user_id=p.id, product_id=product_id)
                .on_conflict_do_nothing()
            )
        else:
            await session.execute(
                delete(Favorite).where(Favorite.user_id == p.id, Favorite.product_id == product_id)
            )


async def favorites(r: Resources, p: Principal, cursor: UUID | None, limit: int) -> ProductPage:
    async with r.sessions() as session:
        await authorize(session, p)
        query = (
            select(Product)
            .join(Favorite, Favorite.product_id == Product.id)
            .where(Favorite.user_id == p.id)
        )
        if cursor:
            query = query.where(Product.id > cursor)
        rows = list((await session.scalars(query.order_by(Product.id).limit(limit + 1))).all())
        result = await product_outputs(session, rows[:limit])
        return ProductPage(items=result, next_cursor=result[-1].id if len(rows) > limit else None)
