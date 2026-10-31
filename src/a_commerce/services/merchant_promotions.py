"""店主确认固定版本后发布；预算与旧秒杀共享报价和库存事务。"""

from uuid import UUID

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from a_commerce.conf.resources import Resources
from a_commerce.errors import CommerceError, missing
from a_commerce.models.base import now
from a_commerce.models.entities import SKU, Audit, FlashSale, OrderItem, Product, Shop, Stock
from a_commerce.models.merchant_ops import (
    CustomerSegment,
    MerchantPromotion,
    PromotionClaim,
    SegmentMember,
)
from a_commerce.schemas.api import VersionIn
from a_commerce.schemas.merchant_ops import (
    OperationLogOut,
    PromotionIn,
    PromotionOut,
    PromotionReportOut,
    SimulationOut,
)
from a_commerce.services.identity import Principal
from a_commerce.services.merchant_analysis import aggregate, integer, lines
from a_commerce.services.merchant_common import digest, remember, replay, version, writer
from a_commerce.services.shop_access import audit, scope


async def check_segment(session: AsyncSession, shop_id: UUID, ident: UUID | None) -> None:
    if ident is None:
        return
    row = await session.scalar(
        select(CustomerSegment).where(CustomerSegment.id == ident).with_for_update(read=True)
    )
    if row is None or row.shop_id != shop_id or not row.active:
        raise CommerceError("SEGMENT_UNAVAILABLE", "客户快照不可用，请重新选择。", 409)


def simulation(sku: SKU, data: PromotionIn) -> SimulationOut:
    discount = sku.price_fen - data.price_fen
    margin = (
        data.price_fen - sku.cost_fen - (data.unit_expense_fen or 0)
        if sku.cost_fen is not None
        else None
    )
    blockers = []
    if discount <= 0:
        blockers.append("活动价必须低于当前普通售价。")
    if margin is None:
        blockers.append("单位成本未知，不能发布。")
    elif margin < data.minimum_margin_fen:
        blockers.append("活动单件毛利或估算贡献低于设定底线。")
    if data.ends_at <= now():
        blockers.append("活动已过期。")
    maximum = min(data.quantity, data.budget_fen // discount) if discount > 0 else 0
    return SimulationOut(
        reference_price_fen=sku.price_fen,
        discount_fen=discount,
        unit_margin_fen=margin,
        margin_basis="商品毛利" if data.unit_expense_fen is None else "基于填写单位费用的估算贡献",
        max_units=maximum,
        blockers=blockers,
        scenarios=[
            {
                "quantity": q,
                "discount_fen": q * discount,
                "margin_fen": q * margin if margin is not None else None,
            }
            for q in sorted({maximum // 4, maximum // 2, maximum})
        ],
    )


async def simulate(r: Resources, p: Principal, shop_id: UUID, data: PromotionIn) -> SimulationOut:
    async with r.sessions() as session:
        await scope(session, p, shop_id, "catalog")
        sku = await session.scalar(
            select(SKU).join(Product).where(SKU.id == data.sku_id, Product.shop_id == shop_id)
        )
        if sku is None:
            raise missing()
        await check_segment(session, shop_id, data.segment_id)
        return simulation(sku, data)


async def draft(
    r: Resources,
    p: Principal,
    shop_id: UUID,
    data: PromotionIn,
    key: str,
    run_id: UUID | None = None,
) -> PromotionOut:
    async with r.sessions.begin() as session:
        await writer(session, p, shop_id, run_id)
        prior = await replay(
            session, p, shop_id, "promotion.draft", key, data.model_dump(mode="json")
        )
        row = await session.get(MerchantPromotion, prior) if prior else None
        if row is None:
            # 与修改成本、下单一致，先锁商品，再锁 SKU。
            product = await session.scalar(
                select(Product)
                .join(SKU)
                .where(SKU.id == data.sku_id, Product.shop_id == shop_id)
                .with_for_update(of=Product)
            )
            sku = await session.scalar(select(SKU).where(SKU.id == data.sku_id).with_for_update())
            if product is None or sku is None:
                raise missing()
            await check_segment(session, shop_id, data.segment_id)
            result = simulation(sku, data)
            if result.discount_fen <= 0:
                raise CommerceError("PROMOTION_PRICE", "活动价必须低于普通售价。", 422)
            row = MerchantPromotion(
                shop_id=shop_id,
                reference_price_fen=sku.price_fen,
                sku_version=sku.version,
                cost_fen=sku.cost_fen,
                cost_version=sku.cost_version,
                **data.model_dump(),
            )
            session.add(row)
            await session.flush()
            remember(
                session, p, shop_id, "promotion.draft", key, data.model_dump(mode="json"), row.id
            )
            session.add(audit(p, shop_id, "promotion.draft", row.id, "创建草稿，尚未生效"))
        return PromotionOut.model_validate(row)


async def promotions(
    r: Resources,
    p: Principal,
    shop_id: UUID,
    cursor: UUID | None,
    limit: int,
    ident: UUID | None = None,
) -> list[PromotionOut]:
    async with r.sessions() as session:
        await scope(session, p, shop_id, "catalog")
        query = select(MerchantPromotion).where(MerchantPromotion.shop_id == shop_id)
        if cursor:
            query = query.where(MerchantPromotion.id > cursor)
        if ident:
            query = query.where(MerchantPromotion.id == ident)
        rows = list(await session.scalars(query.order_by(MerchantPromotion.id).limit(limit)))
        if ident and not rows:
            raise missing()
        return [await output(session, x, r.settings.merchant_promotions_enabled) for x in rows]


async def output(session: AsyncSession, row: MerchantPromotion, enabled: bool) -> PromotionOut:
    result = PromotionOut.model_validate(row)
    if row.status != "published":
        return result
    sku = await session.get(SKU, row.sku_id)
    product = await session.get(Product, sku.product_id) if sku else None
    shop = await session.get(Shop, row.shop_id)
    segment = await session.get(CustomerSegment, row.segment_id) if row.segment_id else None
    if not enabled:
        result.validation_issues.append("平台暂停了新商家活动订单。")
    if sku is None or sku.version != row.sku_version or sku.cost_version != row.cost_version:
        result.validation_issues.append("价格或成本版本变化，需要重新测算确认。")
    if (
        sku is None
        or product is None
        or shop is None
        or not sku.active
        or not product.active
        or shop.status != "active"
    ):
        result.validation_issues.append("店铺、商品或规格当前不可销售。")
    if row.segment_id and (segment is None or not segment.active):
        result.validation_issues.append("活动人群快照已失效。")
    return result


async def validate_draft(r: Resources, p: Principal, shop_id: UUID, ident: UUID) -> SimulationOut:
    async with r.sessions.begin() as session:
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
        shop = await scope(session, p, shop_id, "catalog")
        row = await session.get(MerchantPromotion, ident)
        if row is None or row.shop_id != shop_id:
            raise missing()
        sku = await session.get(SKU, row.sku_id)
        if sku is None:
            raise missing()
        result = simulation(
            sku, PromotionIn.model_validate({f: getattr(row, f) for f in PromotionIn.model_fields})
        )
        if row.sku_version != sku.version or row.cost_version != sku.cost_version:
            result.blockers.append("价格或成本版本变化，请创建新草稿重新确认。")
        product = await session.get(Product, sku.product_id)
        stock = await session.scalar(select(Stock).where(Stock.sku_id == sku.id))
        if (
            shop is None
            or shop.status != "active"
            or product is None
            or not product.active
            or not sku.active
        ):
            result.blockers.append("店铺、商品或规格当前不可销售。")
        if row.status == "draft" and (
            stock is None or row.quantity > stock.on_hand - stock.reserved - stock.allocated
        ):
            result.blockers.append("活动限量超过当前可售库存。")
        if result.max_units <= 0:
            result.blockers.append("预算不足以支持一件活动商品。")
        overlap = await session.scalar(
            select(FlashSale.id)
            .where(
                FlashSale.sku_id == sku.id,
                FlashSale.active.is_(True),
                or_(
                    FlashSale.merchant_promotion_id.is_(None),
                    FlashSale.merchant_promotion_id != row.id,
                ),
                FlashSale.starts_at < row.ends_at,
                FlashSale.ends_at > row.starts_at,
            )
            .limit(1)
        )
        if overlap:
            result.blockers.append("同规格已有重叠活动，请先停用或调整新草稿时间。")
        await check_segment(session, shop_id, row.segment_id)
        return result


async def execute_publish(
    r: Resources, p: Principal, shop_id: UUID, ident: UUID, data: VersionIn, key: str
) -> PromotionOut:
    async with r.sessions.begin() as session:
        await writer(session, p, shop_id, catalog=True)
        value = [ident, data.expected_version]
        prior = await replay(session, p, shop_id, "promotion.publish", key, value)
        preview = await session.get(MerchantPromotion, ident)
        if preview is None or preview.shop_id != shop_id:
            raise missing()
        if prior:
            return PromotionOut.model_validate(preview)
        if not r.settings.merchant_promotions_enabled:
            raise CommerceError("PROMOTIONS_DISABLED", "活动发布尚未启用，请联系平台。", 409)
        product = await session.scalar(
            select(Product)
            .join(SKU)
            .where(SKU.id == preview.sku_id, Product.shop_id == shop_id)
            .with_for_update(of=Product)
        )
        sku = await session.scalar(select(SKU).where(SKU.id == preview.sku_id).with_for_update())
        stock = await session.scalar(
            select(Stock).where(Stock.sku_id == preview.sku_id).with_for_update()
        )
        # 先锁已有活动，再锁活动控制记录，与下单/库存状态迁移保持一致。
        await session.scalars(
            select(FlashSale)
            .where(FlashSale.sku_id == preview.sku_id)
            .order_by(FlashSale.id)
            .with_for_update()
        )
        await session.refresh(preview, with_for_update=True)
        version(preview.version, data.expected_version)
        if preview.status != "draft":
            raise CommerceError("PROMOTION_STATE", "此版本已经处理，请读取原执行结果。", 409)
        if product is None or sku is None or stock is None or not product.active or not sku.active:
            raise CommerceError("PRODUCT_UNAVAILABLE", "商品未上架或不可用。", 409)
        result = simulation(
            sku,
            PromotionIn.model_validate({f: getattr(preview, f) for f in PromotionIn.model_fields}),
        )
        if (
            result.blockers
            or preview.sku_version != sku.version
            or preview.cost_version != sku.cost_version
        ):
            raise CommerceError(
                "PROMOTION_STALE", "草稿不符合价格、成本或贡献约束，请重新测算创建。", 409
            )
        if (
            result.max_units <= 0
            or preview.quantity > stock.on_hand - stock.reserved - stock.allocated
        ):
            raise CommerceError("PROMOTION_STOCK", "活动限量超过可售库存或预算不足一件。", 409)
        await check_segment(session, shop_id, preview.segment_id)
        overlap = await session.scalar(
            select(FlashSale.id)
            .where(
                FlashSale.sku_id == sku.id,
                FlashSale.active.is_(True),
                FlashSale.starts_at < preview.ends_at,
                FlashSale.ends_at > preview.starts_at,
            )
            .limit(1)
        )
        if overlap:
            raise CommerceError(
                "FLASH_SALE_OVERLAP", "同规格已有重叠活动，请先停用或调整新草稿时间。", 409
            )
        preview.approved_by, preview.approved_at = p.id, now()
        preview.approval_digest = digest(
            {f: getattr(preview, f) for f in PromotionIn.model_fields}
            | {
                "shop_id": shop_id,
                "version": preview.version,
                "reference_price_fen": preview.reference_price_fen,
                "cost_version": preview.cost_version,
                "cost_fen": preview.cost_fen,
                "sku_version": preview.sku_version,
            }
        )
        preview.status = "published"
        session.add(
            FlashSale(
                merchant_promotion_id=preview.id,
                sku_id=sku.id,
                price_fen=preview.price_fen,
                stock=preview.quantity,
                starts_at=preview.starts_at,
                ends_at=preview.ends_at,
                active=True,
            )
        )
        remember(session, p, shop_id, "promotion.publish", key, value, preview.id)
        session.add(
            audit(
                p,
                shop_id,
                "promotion.published",
                preview.id,
                f"店主确认版本 {preview.version}，摘要 {preview.approval_digest}",
            )
        )
        return PromotionOut.model_validate(preview)


async def stop(
    r: Resources,
    p: Principal,
    shop_id: UUID,
    ident: UUID,
    data: VersionIn,
    key: str,
    reason: str = "店主停用未来新增优惠",
) -> PromotionOut:
    async with r.sessions.begin() as session:
        await writer(session, p, shop_id)
        row = await session.get(MerchantPromotion, ident)
        if row is None or row.shop_id != shop_id:
            raise missing()
        value = [ident, data.expected_version]
        value.append(reason)
        prior = await replay(session, p, shop_id, "promotion.stop", key, value)
        if prior:
            return PromotionOut.model_validate(row)
        sale = await session.scalar(
            select(FlashSale).where(FlashSale.merchant_promotion_id == ident).with_for_update()
        )
        await session.refresh(row, with_for_update=True)
        version(row.version, data.expected_version)
        if row.status != "published" or sale is None:
            raise CommerceError("PROMOTION_STATE", "只有已发布活动可以停用。", 409)
        sale.active, row.status = False, "stopped"
        sale.version += 1
        remember(session, p, shop_id, "promotion.stop", key, value, row.id)
        session.add(audit(p, shop_id, "promotion.stopped", row.id, reason))
        return PromotionOut.model_validate(row)


async def qualify(
    session: AsyncSession, sale: FlashSale, sku: SKU, user_id: UUID, quantity: int, shop_id: UUID
) -> MerchantPromotion | None:
    if sale.merchant_promotion_id is None:
        return None
    row = await session.scalar(
        select(MerchantPromotion)
        .where(MerchantPromotion.id == sale.merchant_promotion_id)
        .with_for_update()
    )
    if row is None or row.shop_id != shop_id or row.status != "published" or row.sku_id != sku.id:
        raise CommerceError("PROMOTION_UNAVAILABLE", "活动不可用。", 409)
    if (
        row.sku_version != sku.version
        or row.reference_price_fen != sku.price_fen
        or row.cost_version != sku.cost_version
        or row.cost_fen != sku.cost_fen
    ):
        raise CommerceError("PROMOTION_STALE", "活动的价格或成本依据已变化，请重新报价。", 409)
    await check_segment(session, row.shop_id, row.segment_id)
    if row.segment_id and not await session.scalar(
        select(SegmentMember.id).where(
            SegmentMember.segment_id == row.segment_id, SegmentMember.user_id == user_id
        )
    ):
        raise CommerceError(
            "PROMOTION_INELIGIBLE", "当前账号不符合该活动资格，请选择普通价重新报价。", 403
        )
    discount = (row.reference_price_fen - row.price_fen) * quantity
    if row.reserved_fen + row.consumed_fen + discount > row.budget_fen:
        raise CommerceError("PROMOTION_BUDGET", "活动剩余预算不足，请重新报价。", 409)
    return row


async def claim(session: AsyncSession, promotion: MerchantPromotion, item: OrderItem) -> None:
    amount = (promotion.reference_price_fen - promotion.price_fen) * item.quantity
    promotion.reserved_fen += amount
    session.add(
        PromotionClaim(
            promotion_id=promotion.id,
            order_item_id=item.id,
            quantity=item.quantity,
            discount_fen=amount,
            promotion_version=promotion.version,
        )
    )


async def move_claim(session: AsyncSession, sale: FlashSale, item: OrderItem, action: str) -> None:
    if sale.merchant_promotion_id is None:
        if action in {"release_reserved", "release_allocated"}:
            sale.claimed -= item.quantity
        return
    promotion = await session.scalar(
        select(MerchantPromotion)
        .where(MerchantPromotion.id == sale.merchant_promotion_id)
        .with_for_update()
    )
    record = await session.scalar(
        select(PromotionClaim).where(PromotionClaim.order_item_id == item.id).with_for_update()
    )
    if promotion is None or record is None or record.promotion_id != promotion.id:
        raise CommerceError("PROMOTION_LEDGER", "活动占用记录缺失，等待人工核查。", 409)
    if record.status != "reserved":
        return
    if action == "allocate":
        promotion.reserved_fen -= record.discount_fen
        promotion.consumed_fen += record.discount_fen
        record.status = "consumed"
    elif action == "release_reserved":
        promotion.reserved_fen -= record.discount_fen
        sale.claimed -= record.quantity
        record.status = "released"


async def promotion_report(
    r: Resources, p: Principal, shop_id: UUID, ident: UUID
) -> PromotionReportOut:
    async with r.sessions.begin() as session:
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        await scope(session, p, shop_id, "audit")
        row = await session.get(MerchantPromotion, ident)
        if row is None or row.shop_id != shop_id:
            raise missing()
        sale = await session.scalar(
            select(FlashSale).where(FlashSale.merchant_promotion_id == ident)
        )
        records = list(
            await session.scalars(
                select(PromotionClaim).where(PromotionClaim.promotion_id == ident)
            )
        )
        as_of = now()
        facts = await lines(session, shop_id, row.created_at, as_of)
        selected = [x for x in facts if sale and x["flash_sale_id"] == str(sale.id)]
        paid = [
            x
            for x in selected
            if any(
                str(c.order_item_id) == x["order_item_id"] and c.status == "consumed"
                for c in records
            )
        ]
        refunded = sum(integer(x, "refund_fen") for x in paid)
        discount = row.reference_price_fen - row.price_fen
        # 当前只支持按原成交价、整件退款，商家活动价不可原地修改。
        net_discount = row.consumed_fen - sum(
            integer(x, "refund_fen") // row.price_fen * discount for x in paid
        )
        return PromotionReportOut(
            promotion=await output(session, row, r.settings.merchant_promotions_enabled),
            flash_sale_id=sale.id if sale else None,
            as_of=as_of,
            reserved_quantity=sum(x.quantity for x in records if x.status == "reserved"),
            consumed_quantity=sum(x.quantity for x in records if x.status == "consumed"),
            released_quantity=sum(x.quantity for x in records if x.status == "released"),
            refunded_goods_fen=refunded,
            net_discount_fen=net_discount,
            performance=aggregate(selected),
            limitations=[
                "正常支付后退款不恢复可发放预算或限量。",
                "经营表现含资金事实；释放后迟到付款属于补偿退款，不计为正常活动兑换。",
                "毛利和贡献只覆盖可计算明细，前后变化不代表因果收益。",
            ],
            executions=[
                OperationLogOut.model_validate(x)
                for x in await session.scalars(
                    select(Audit)
                    .where(Audit.shop_id == shop_id, Audit.resource_id == ident)
                    .order_by(Audit.created_at.desc(), Audit.id.desc())
                    .limit(50)
                )
            ],
        )


async def publish(
    r: Resources, p: Principal, shop_id: UUID, ident: UUID, data: VersionIn, key: str
) -> PromotionOut:
    try:
        return await execute_publish(r, p, shop_id, ident, data, key)
    except CommerceError as exc:
        # 业务事务已回滚，再记录固定错误码，不记录原始异常或凭证。
        async with r.sessions.begin() as session:
            await writer(session, p, shop_id)
            row = await session.get(MerchantPromotion, ident)
            if row is not None and row.shop_id == shop_id:
                session.add(
                    audit(
                        p,
                        shop_id,
                        "promotion.publish_failed",
                        ident,
                        f"error={exc.code};version={data.expected_version};key_hash={digest(key)}",
                    )
                )
        raise
