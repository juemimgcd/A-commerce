"""按完整经营日测算库存风险；收货和任务进度在同一事务中提交。"""

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select, text

from a_commerce.conf.resources import Resources
from a_commerce.errors import CommerceError, missing
from a_commerce.models.base import now
from a_commerce.models.entities import SKU, Product, Stock, StockMovement
from a_commerce.models.merchant_ops import RestockReceipt, RestockRule, RestockTask
from a_commerce.schemas.api import VersionIn
from a_commerce.schemas.merchant_ops import (
    MerchantInventoryOut,
    ReceiptIn,
    RestockEdit,
    RestockIn,
    RestockOut,
    RiskOut,
    RuleIn,
)
from a_commerce.services.identity import Principal
from a_commerce.services.merchant_analysis import ZONE, bounds, integer, lines
from a_commerce.services.merchant_common import remember, replay, version, writer
from a_commerce.services.shop_access import audit, scope


async def inventory(
    r: Resources,
    p: Principal,
    shop_id: UUID,
    offset: int = 0,
    limit: int = 20,
    sku_id: UUID | None = None,
) -> MerchantInventoryOut:
    async with r.sessions.begin() as session:
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        await scope(session, p, shop_id, "catalog")
        as_of = now()
        today = as_of.astimezone(ZONE).date()
        start, end = bounds(today - timedelta(days=366), today)
        facts = await lines(session, shop_id, start, end)
        rules = {
            x.sku_id: x
            for x in await session.scalars(
                select(RestockRule).where(RestockRule.shop_id == shop_id)
            )
        }
        tasks = list(
            await session.scalars(
                select(RestockTask).where(
                    RestockTask.shop_id == shop_id,
                    RestockTask.status.in_(["confirmed", "in_progress", "partially_received"]),
                )
            )
        )
        query = (
            select(SKU, Product, Stock)
            .join(Product)
            .join(Stock, Stock.sku_id == SKU.id)
            .where(Product.shop_id == shop_id)
        )
        if sku_id:
            query = query.where(SKU.id == sku_id)
        output: list[RiskOut] = []
        for sku, product, stock in await session.execute(query.order_by(SKU.id)):
            rule = rules.get(sku.id)
            available = stock.on_hand - stock.reserved - stock.allocated
            sold = cancelled = returned = 0
            incoming = 0
            quantity: int | None = None
            coverage: float | None = None
            risk: str = "unconfigured"
            warnings = ["未提供完整缺货时段，成交量可能低于真实需求。"]
            if rule:
                begin, _ = bounds(today - timedelta(days=rule.window_days), today)
                matching = [
                    x
                    for x in facts
                    if x["sku_id"] == str(sku.id)
                    and datetime.fromisoformat(str(x["paid_at"])) >= begin
                ]
                for fact in matching:
                    if (
                        fact["order_status"] == "cancelled"
                        and fact["fulfillment_status"] == "unfulfilled"
                    ):
                        cancelled += integer(fact, "paid_quantity")
                    else:
                        sold += integer(fact, "paid_quantity")
                        returned += integer(fact, "returned_quantity")
                sold = max(0, sold - returned)
                horizon = today + timedelta(days=rule.lead_days + rule.review_days)
                incoming = sum(
                    t.quantity - t.received
                    for t in tasks
                    if t.sku_id == sku.id
                    and t.expected_on is not None
                    and today <= t.expected_on <= horizon
                )
                unreliable = any(
                    t.sku_id == sku.id and (t.expected_on is None or t.expected_on < today)
                    for t in tasks
                )
                if unreliable:
                    warnings.append("有日期未知或逾期的补货任务，未计入可靠在途。")
                if sold:
                    coverage = available * rule.window_days / sold
                    target = (
                        sold * (rule.lead_days + rule.review_days) + rule.window_days - 1
                    ) // rule.window_days + rule.safety_stock
                    quantity = max(0, target - available - incoming)
                    risk = (
                        "low"
                        if available <= rule.low_stock or coverage <= rule.lead_days
                        else "slow"
                        if sold <= rule.slow_sales and available > rule.low_stock
                        else "normal"
                    )
                else:
                    quantity = max(0, rule.low_stock + rule.safety_stock - available - incoming)
                    risk = "insufficient"
                    warnings.append("有效销量不足，仅按固定库存阈值建议。")
            else:
                warnings.append("请先设置交货期、复查周期和库存阈值。")
            output.append(
                RiskOut(
                    sku_id=sku.id,
                    title=product.title,
                    code=sku.code,
                    on_hand=stock.on_hand,
                    reserved=stock.reserved,
                    allocated=stock.allocated,
                    available=available,
                    cost_fen=sku.cost_fen,
                    rule=RuleIn(
                        expected_version=rule.version,
                        **{
                            f: getattr(rule, f)
                            for f in RuleIn.model_fields
                            if f != "expected_version"
                        },
                    )
                    if rule
                    else None,
                    adjusted_sales=sold,
                    excluded_cancelled=cancelled,
                    inspected_returns=returned,
                    coverage_days=coverage,
                    incoming=incoming,
                    suggested_quantity=quantity,
                    estimated_cost_fen=quantity * sku.cost_fen
                    if quantity is not None and sku.cost_fen is not None
                    else None,
                    risk=risk,
                    limitations=warnings,
                )
            )
        if sku_id and not output:
            raise missing()
        output.sort(
            key=lambda x: (
                {"low": 0, "insufficient": 1, "slow": 2, "unconfigured": 3, "normal": 4}[x.risk],
                x.coverage_days if x.coverage_days is not None else float("inf"),
                x.sku_id,
            )
        )
        return MerchantInventoryOut(
            shop_id=shop_id,
            as_of=as_of,
            total=len(output),
            items=output[offset : offset + limit],
            next_offset=offset + limit if offset + limit < len(output) else None,
        )


async def save_rule(
    r: Resources, p: Principal, shop_id: UUID, sku_id: UUID, data: RuleIn
) -> RuleIn:
    async with r.sessions.begin() as session:
        await writer(session, p, shop_id, catalog=True)
        sku = await session.scalar(
            select(SKU).join(Product).where(SKU.id == sku_id, Product.shop_id == shop_id)
        )
        if sku is None:
            raise missing()
        rule = await session.scalar(
            select(RestockRule).where(RestockRule.sku_id == sku_id).with_for_update()
        )
        version(rule.version if rule else 0, data.expected_version)
        if rule is None:
            rule = RestockRule(shop_id=shop_id, sku_id=sku_id, version=0)
            session.add(rule)
        for key, value in data.model_dump(exclude={"expected_version"}).items():
            setattr(rule, key, value)
        rule.version += 1
        session.add(audit(p, shop_id, "restock.rule", sku_id, f"补货规则版本 {rule.version}"))
        return data.model_copy(update={"expected_version": rule.version})


async def draft(
    r: Resources, p: Principal, shop_id: UUID, data: RestockIn, key: str, run_id: UUID | None = None
) -> RestockOut:
    evidence = await inventory(r, p, shop_id, sku_id=data.sku_id)
    async with r.sessions.begin() as session:
        await writer(session, p, shop_id, run_id)
        prior = await replay(
            session, p, shop_id, "restock.draft", key, data.model_dump(mode="json")
        )
        row = await session.get(RestockTask, prior) if prior else None
        if row is None:
            row = RestockTask(
                shop_id=shop_id, evidence=evidence.model_dump(mode="json"), **data.model_dump()
            )
            session.add(row)
            await session.flush()
            remember(
                session, p, shop_id, "restock.draft", key, data.model_dump(mode="json"), row.id
            )
            session.add(audit(p, shop_id, "restock.draft", row.id, "创建内部草稿，未入库"))
        return RestockOut.model_validate(row)


async def tasks(
    r: Resources,
    p: Principal,
    shop_id: UUID,
    cursor: UUID | None = None,
    limit: int = 20,
    ident: UUID | None = None,
) -> list[RestockOut]:
    async with r.sessions() as session:
        await scope(session, p, shop_id, "catalog")
        query = select(RestockTask).where(RestockTask.shop_id == shop_id)
        if cursor:
            query = query.where(RestockTask.id > cursor)
        if ident:
            query = query.where(RestockTask.id == ident)
        rows = list(await session.scalars(query.order_by(RestockTask.id).limit(limit)))
        if ident and not rows:
            raise missing()
        return [RestockOut.model_validate(row) for row in rows]


async def transition(
    r: Resources,
    p: Principal,
    shop_id: UUID,
    ident: UUID,
    action: str,
    data: RestockEdit | ReceiptIn | VersionIn,
    key: str,
) -> RestockOut:
    async with r.sessions.begin() as session:
        await writer(session, p, shop_id, catalog=action != "cancel")
        row = await session.scalar(
            select(RestockTask)
            .where(RestockTask.id == ident, RestockTask.shop_id == shop_id)
            .with_for_update()
        )
        if row is None:
            raise missing()
        value = [ident, data.model_dump(mode="json")]
        prior = await replay(session, p, shop_id, f"restock.{action}", key, value)
        if prior:
            return RestockOut.model_validate(row)
        version(row.version, data.expected_version)
        if row.status in {"completed", "cancelled"}:
            raise CommerceError("RESTOCK_STATE", "任务已结束。", 409)
        if action == "confirm" and isinstance(data, RestockEdit):
            if data.quantity < row.received or data.expected_on < now().astimezone(ZONE).date():
                raise CommerceError(
                    "RESTOCK_RANGE", "确认量不能小于已收数量，到货日不能早于今天。", 422
                )
            row.quantity, row.expected_on, row.note = data.quantity, data.expected_on, data.note
            row.status = (
                "completed"
                if row.received == row.quantity
                else "partially_received"
                if row.received
                else "confirmed"
            )
        elif action == "receive" and isinstance(data, ReceiptIn):
            if row.status == "draft" or row.received + data.quantity > row.quantity:
                raise CommerceError("RESTOCK_QUANTITY", "任务未确认或收货量超过未收数量。", 409)
            stock = await session.scalar(
                select(Stock).where(Stock.sku_id == row.sku_id).with_for_update()
            )
            if stock is None:
                raise missing()
            receipt = RestockReceipt(task_id=row.id, quantity=data.quantity, actor_id=p.id)
            session.add(receipt)
            await session.flush()
            stock.on_hand += data.quantity
            session.add(
                StockMovement(
                    reference=f"restock:{receipt.id}",
                    sku_id=row.sku_id,
                    on_hand_delta=data.quantity,
                    reserved_delta=0,
                    allocated_delta=0,
                    reason="补货实际收货",
                )
            )
            row.received += data.quantity
            row.status = "completed" if row.received == row.quantity else "partially_received"
        elif action == "start":
            if row.status != "confirmed":
                raise CommerceError("RESTOCK_STATE", "只有已确认任务可开始处理。", 409)
            row.status = "in_progress"
        elif action == "cancel":
            row.status = "cancelled"
        else:
            raise CommerceError("RESTOCK_ACTION", "不支持的补货操作。", 422)
        row.version += 1
        remember(session, p, shop_id, f"restock.{action}", key, value, row.id)
        session.add(
            audit(
                p,
                shop_id,
                f"restock.{action}",
                row.id,
                f"版本 {row.version}，已收 {row.received}/{row.quantity}",
            )
        )
        return RestockOut.model_validate(row)
