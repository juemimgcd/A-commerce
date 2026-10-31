"""一次主库快照内生成 SKU 对比与可保存证据，不让模型自行汇总列表。"""

from collections import defaultdict
from datetime import date, datetime, time
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from a_commerce.conf.resources import Resources
from a_commerce.errors import CommerceError, missing
from a_commerce.models.base import now
from a_commerce.models.entities import (
    Order,
    OrderItem,
    Refund,
    RefundItem,
    ReturnItem,
    ReturnRequest,
)
from a_commerce.models.merchant_ops import BusinessReport, OrderExpense, ReportEvidence
from a_commerce.schemas.merchant_ops import (
    DiagnosticsOut,
    EvidenceOut,
    ExpenseIn,
    ExpenseOut,
    MerchantChangeOut,
    PeriodIn,
    ReportIndex,
    SkuPerformance,
)
from a_commerce.services.identity import Principal
from a_commerce.services.merchant_common import (
    digest,
    remember,
    replay,
    snapshot_write,
    version,
    writer,
)
from a_commerce.services.shop_access import audit, scope

ZONE = ZoneInfo("Asia/Shanghai")
EXPENSES = ("delivery", "packaging", "payment")


def bounds(start: date, end: date) -> tuple[datetime, datetime]:
    if end <= start or (end - start).days > 366:
        raise CommerceError("REPORT_RANGE", "请选择不超过 366 天的有效区间，结束日期不包含。", 422)
    return datetime.combine(start, time.min, ZONE), datetime.combine(end, time.min, ZONE)


async def lines(
    session: AsyncSession,
    shop_id: UUID,
    start: datetime,
    end: datetime,
) -> list[dict[str, object]]:
    # Keep order membership in SQL: expanding UUIDs into bind parameters hits
    # asyncpg's argument limit on busy shops, even when the response is paginated.
    order_ids = select(Order.id).where(
        Order.shop_id == shop_id, Order.paid_at >= start, Order.paid_at < end
    )
    orders = list(
        await session.scalars(
            select(Order)
            .where(
                Order.shop_id == shop_id,
                Order.paid_at >= start,
                Order.paid_at < end,
            )
            .order_by(Order.id)
        )
    )
    if not orders:
        return []
    by_order = {o.id: o for o in orders}
    items = list(
        await session.scalars(
            select(OrderItem)
            .where(
                OrderItem.order_id.in_(order_ids),
            )
            .order_by(OrderItem.id)
        )
    )
    refunds = {
        i: int(a)
        for i, a in await session.execute(
            select(RefundItem.order_item_id, func.sum(RefundItem.amount_fen))
            .join(Refund)
            .where(Refund.order_id.in_(order_ids), Refund.status == "succeeded")
            .group_by(RefundItem.order_item_id)
        )
    }
    incomplete = set(
        await session.scalars(
            select(Refund.order_id).where(
                Refund.order_id.in_(order_ids),
                Refund.shipping_fen.is_(None),
            )
        )
    )
    returned: dict[UUID, int] = {}
    resellable: dict[UUID, int] = {}
    for ident, quantity, resell in await session.execute(
        select(ReturnItem.order_item_id, ReturnItem.quantity, ReturnRequest.resellable)
        .join(ReturnRequest)
        .where(ReturnRequest.order_id.in_(order_ids), ReturnRequest.status == "inspected")
    ):
        returned[ident] = returned.get(ident, 0) + quantity
        if resell:
            resellable[ident] = resellable.get(ident, 0) + quantity
    latest: dict[tuple[UUID, str], OrderExpense] = {}
    for expense in await session.scalars(
        select(OrderExpense)
        .where(
            OrderExpense.order_id.in_(order_ids),
            OrderExpense.shop_id == shop_id,
        )
        .order_by(OrderExpense.revision)
    ):
        latest[(expense.order_id, expense.category)] = expense
    grouped: dict[UUID, list[OrderItem]] = defaultdict(list)
    for item in items:
        grouped[item.order_id].append(item)
    allocated: dict[UUID, int] = defaultdict(int)
    qualities: dict[UUID, str] = {}
    for oid, group in grouped.items():
        expenses = [latest.get((oid, c)) for c in EXPENSES]
        qualities[oid] = "unknown"
        if all(
            e and e.complete and e.amount_fen is not None and e.quality != "unknown"
            for e in expenses
        ):
            qualities[oid] = (
                "estimated" if any(e and e.quality == "estimated" for e in expenses) else "actual"
            )
            denominator = sum(i.price_fen * i.quantity for i in group)
            for entry in expenses:
                if entry is None or entry.amount_fen is None:
                    continue
                expense_amount = entry.amount_fen
                amounts = {
                    i.id: expense_amount * i.price_fen * i.quantity // denominator for i in group
                }
                remainder = expense_amount - sum(amounts.values())
                ordered = sorted(
                    group,
                    key=lambda i: (
                        -(expense_amount * i.price_fen * i.quantity % denominator),
                        i.id,
                    ),
                )
                for i in ordered[:remainder]:
                    amounts[i.id] += 1
                for ident, amount in amounts.items():
                    allocated[ident] += amount
    output: list[dict[str, object]] = []
    for item in items:
        order = by_order[item.order_id]
        reverse = (
            item.quantity
            if (
                order.status == "cancelled"
                and order.fulfillment_status == "unfulfilled"
                and order.refunded_fen == order.total_fen
            )
            else resellable.get(item.id, 0)
        )
        complete = item.cost_fen is not None and order.id not in incomplete
        net = item.price_fen * item.quantity - refunds.get(item.id, 0)
        cost = item.cost_fen * (item.quantity - reverse) if item.cost_fen is not None else None
        output.append(
            {
                "order_item_id": str(item.id),
                "order_id": str(order.id),
                "sku_id": str(item.sku_id),
                "product_id": str(item.product_id) if item.product_id else None,
                "title": item.title,
                "paid_at": order.paid_at.isoformat() if order.paid_at else None,
                "paid_quantity": item.quantity,
                "price_fen": item.price_fen,
                "regular_price_fen": item.regular_price_fen,
                "unit_cost_fen": item.cost_fen,
                "net_goods_fen": net if order.id not in incomplete else None,
                "refund_fen": refunds.get(item.id, 0),
                "returned_quantity": returned.get(item.id, 0),
                "resellable_quantity": resellable.get(item.id, 0),
                "cost_reversed_quantity": reverse,
                "known_cost_fen": cost if complete else None,
                "complete": complete,
                "expense_fen": allocated[item.id] if qualities[order.id] != "unknown" else None,
                "expense_quality": qualities[order.id],
                "expense_entries": [
                    {
                        "id": str(e.id),
                        "category": e.category,
                        "quality": e.quality,
                        "amount_fen": e.amount_fen,
                        "complete": e.complete,
                        "revision": e.revision,
                    }
                    for c in EXPENSES
                    if (e := latest.get((order.id, c)))
                ],
                "flash_sale_id": str(item.flash_sale_id) if item.flash_sale_id else None,
                "order_status": order.status,
                "fulfillment_status": order.fulfillment_status,
                "refund_pending_fen": order.refund_reserved_fen,
            }
        )
    return output


def integer(row: dict[str, object], key: str) -> int:
    value = row[key]
    return value if isinstance(value, int) else 0


def aggregate(rows: list[dict[str, object]]) -> list[SkuPerformance]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["sku_id"])].append(row)
    output: list[SkuPerformance] = []
    for ident, group in grouped.items():
        complete = [r for r in group if r["complete"]]
        actual = [r for r in complete if r["expense_quality"] == "actual"]
        estimates = [r for r in complete if r["expense_quality"] in {"actual", "estimated"}]
        gross = sum(integer(r, "net_goods_fen") - integer(r, "known_cost_fen") for r in complete)
        output.append(
            SkuPerformance(
                sku_id=UUID(ident),
                product_id=UUID(str(group[0]["product_id"])) if group[0]["product_id"] else None,
                title=str(group[0]["title"]),
                paid_quantity=sum(integer(r, "paid_quantity") for r in group),
                net_goods_fen=sum(integer(r, "net_goods_fen") for r in group)
                if all(r["net_goods_fen"] is not None for r in group)
                else None,
                known_net_goods_fen=sum(integer(r, "net_goods_fen") for r in complete),
                known_cost_fen=sum(integer(r, "known_cost_fen") for r in complete),
                gross_profit_fen=gross if complete else None,
                contribution_fen=sum(
                    integer(r, "net_goods_fen")
                    - integer(r, "known_cost_fen")
                    - integer(r, "expense_fen")
                    for r in actual
                )
                if actual
                else None,
                estimated_contribution_fen=sum(
                    integer(r, "net_goods_fen")
                    - integer(r, "known_cost_fen")
                    - integer(r, "expense_fen")
                    for r in estimates
                )
                if estimates
                else None,
                line_count=len(group),
                profit_line_count=len(complete),
                contribution_line_count=len(actual),
            )
        )
    return sorted(
        output, key=lambda r: (r.gross_profit_fen is None, -(r.gross_profit_fen or 0), r.sku_id)
    )


@snapshot_write
async def diagnose(
    r: Resources,
    p: Principal,
    shop_id: UUID,
    data: PeriodIn,
    key: str,
    run_id: UUID | None = None,
) -> DiagnosticsOut:
    begin, finish = bounds(data.start, data.end)
    async with r.sessions.begin() as session:
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
        await writer(session, p, shop_id, run_id)
        old = await replay(
            session, p, shop_id, "merchant.diagnose", key, data.model_dump(mode="json")
        )
        if old:
            saved = await session.get(BusinessReport, old)
            if saved is None or saved.shop_id != shop_id:
                raise missing()
            return DiagnosticsOut.model_validate(saved.payload)
        as_of = now()
        current = await lines(session, shop_id, begin, finish)
        previous = await lines(session, shop_id, begin - (finish - begin), begin)
        a, b = aggregate(current), aggregate(previous)
        for records in (a, b):
            records.sort(
                key=lambda x: (
                    getattr(x, data.ranking) is None,
                    -(getattr(x, data.ranking) or 0),
                    x.sku_id,
                )
            )
        summary: dict[str, int | float | None] = {}
        for key_name in (
            "paid_quantity",
            "known_net_goods_fen",
            "gross_profit_fen",
            "contribution_fen",
            "estimated_contribution_fen",
        ):
            av = [getattr(x, key_name) for x in a if getattr(x, key_name) is not None]
            bv = [getattr(x, key_name) for x in b if getattr(x, key_name) is not None]
            v, w = sum(av) if av else None, sum(bv) if bv else None
            summary[key_name] = v
            summary[f"previous_{key_name}"] = w
            summary[f"change_percent_{key_name}"] = (
                (v - w) * 100 / w if v is not None and w else None
            )
        for label, records in (("current", a), ("previous", b)):
            total = sum(x.line_count for x in records)
            summary[f"{label}_profit_coverage"] = (
                sum(x.profit_line_count for x in records) / total if total else None
            )
            summary[f"{label}_contribution_coverage"] = (
                sum(x.contribution_line_count for x in records) / total if total else None
            )
        for period, facts in (("current", current), ("previous", previous)):
            original = sum(integer(x, "price_fen") * integer(x, "paid_quantity") for x in facts)
            known = sum(
                integer(x, "price_fen") * integer(x, "paid_quantity")
                for x in facts
                if x["unit_cost_fen"] is not None
            )
            summary[f"{period}_cost_amount_coverage"] = known / original if original else None
        anomalies: list[str] = []
        enough = min(len(current), len(previous)) >= data.minimum_samples
        if summary["current_profit_coverage"] != summary["previous_profit_coverage"]:
            anomalies.append("前后期毛利覆盖不同，应先核对样本，不能直接判断经营恶化。")
        elif enough:
            for label, field in (("毛利", "gross_profit_fen"), ("贡献", "contribution_fen")):
                if (
                    field == "contribution_fen"
                    and summary["current_contribution_coverage"]
                    != summary["previous_contribution_coverage"]
                ):
                    anomalies.append("前后期实际费用覆盖不同，贡献差额不能直接比较。")
                    continue
                value, prior = summary[field], summary[f"previous_{field}"]
                if isinstance(value, int) and isinstance(prior, int) and prior > 0:
                    if (
                        prior - value >= data.minimum_change_fen
                        and (prior - value) * 100 >= prior * data.change_percent
                    ):
                        anomalies.append(
                            f"{label}下降超过设定金额与幅度阈值，请核对价格、退款、成本和费用分解。"
                        )
        if not enough:
            anomalies.append("对比样本不足，仅展示事实与差额。")
        current_refunds = sum(integer(x, "refund_fen") for x in current)
        previous_refunds = sum(integer(x, "refund_fen") for x in previous)
        summary["refund_fen"] = current_refunds
        summary["previous_refund_fen"] = previous_refunds
        if enough and current_refunds - previous_refunds >= data.minimum_change_fen:
            if (
                previous_refunds == 0
                or (current_refunds - previous_refunds) * 100
                >= previous_refunds * data.change_percent
            ):
                anomalies.append("成功商品退款增加，达到设定金额及幅度阈值。")
        for period, facts in (("current", current), ("previous", previous)):
            complete = [x for x in facts if x["complete"]]
            income = sum(integer(x, "net_goods_fen") for x in complete)
            margin = sum(
                integer(x, "net_goods_fen") - integer(x, "known_cost_fen") for x in complete
            )
            summary[f"{period}_gross_margin_rate"] = margin / income if income > 0 else None
        if enough and summary["current_profit_coverage"] == summary["previous_profit_coverage"]:
            rate, old_rate = (
                summary["current_gross_margin_rate"],
                summary["previous_gross_margin_rate"],
            )
            if (
                isinstance(rate, float)
                and isinstance(old_rate, float)
                and (old_rate - rate) * 100 >= data.change_percent
            ):
                anomalies.append("已覆盖部分毛利率下降达到设定百分点阈值。")
        decomposition: list[MerchantChangeOut] = []
        for dimension, field in (("商品", "product_id"), ("活动", "flash_sale_id")):
            groups: dict[str, tuple[list[dict[str, object]], list[dict[str, object]]]] = {}
            for index, facts in enumerate((current, previous)):
                for fact in facts:
                    ident = str(fact[field] or "普通销售/未知归属")
                    if ident not in groups:
                        groups[ident] = ([], [])
                    groups[ident][index].append(fact)
            for ident, (cur, prev) in groups.items():
                values = [
                    sum(
                        integer(x, "net_goods_fen") - integer(x, "known_cost_fen")
                        for x in group
                        if x["complete"]
                    )
                    if any(x["complete"] for x in group)
                    else None
                    for group in (cur, prev)
                ]
                cv, pv = values
                decomposition.append(
                    MerchantChangeOut(
                        dimension=dimension,
                        name=str((cur or prev)[0]["title"]) if dimension == "商品" else ident,
                        metric="已覆盖部分毛利",
                        current_fen=cv,
                        previous_fen=pv,
                        delta_fen=cv - pv if cv is not None and pv is not None else None,
                    )
                )
        for category in EXPENSES:
            fees: list[int | None] = []
            for facts in (current, previous):
                entries: dict[str, dict[str, object]] = {}
                for fact in facts:
                    raw = fact["expense_entries"]
                    if isinstance(raw, list):
                        for entry in raw:
                            if isinstance(entry, dict) and entry.get("category") == category:
                                entries[str(entry["id"])] = entry
                fees.append(
                    sum(integer(e, "amount_fen") for e in entries.values())
                    if any(isinstance(e.get("amount_fen"), int) for e in entries.values())
                    else None
                )
            cv, pv = fees
            decomposition.append(
                MerchantChangeOut(
                    dimension="费用",
                    name={"delivery": "发货配送费", "packaging": "包装费", "payment": "支付手续费"}[
                        category
                    ],
                    metric="已登记费用（含估算，完整性见证据）",
                    current_fen=cv,
                    previous_fen=pv,
                    delta_fen=cv - pv if cv is not None and pv is not None else None,
                )
            )
        breakdown: dict[str, object] = {}
        for label, rows in (("current", current), ("previous", previous)):
            breakdown[label] = {
                "refund_fen": sum(integer(x, "refund_fen") for x in rows),
                "known_expense_fen": sum(integer(x, "expense_fen") for x in rows),
                "unknown_cost_lines": sum(x["unit_cost_fen"] is None for x in rows),
                "unknown_expense_lines": sum(x["expense_quality"] == "unknown" for x in rows),
                "activities": sorted({str(x["flash_sale_id"]) for x in rows if x["flash_sale_id"]}),
            }
        result = DiagnosticsOut(
            report_id=uuid4(),
            shop_id=shop_id,
            start=data.start,
            end=data.end,
            as_of=as_of,
            current=a,
            previous=b,
            total_skus=len(a),
            next_offset=None,
            summary=summary,
            anomalies=anomalies,
            breakdown=breakdown,
            decomposition=decomposition,
            decomposition_total=len(decomposition),
            limitations=[
                "商品毛利及贡献仅覆盖可计算明细；贡献不含运费收入。",
                "估算费用单列；未知费用不按零处理。",
                "前后变化属于观察结果，不代表确定因果。",
            ],
        )
        payload = result.model_dump(mode="json")
        session.add(
            BusinessReport(
                id=result.report_id, shop_id=shop_id, payload=payload, digest=digest(payload)
            )
        )
        await session.flush()
        session.add_all(
            ReportEvidence(report_id=result.report_id, payload={**x, "period": period})
            for period, rows in (("current", current), ("previous", previous))
            for x in rows
        )
        remember(
            session,
            p,
            shop_id,
            "merchant.diagnose",
            key,
            data.model_dump(mode="json"),
            result.report_id,
        )
        session.add(
            audit(p, shop_id, "merchant.report_saved", result.report_id, "保存同快照经营诊断及证据")
        )
        return result


async def report(
    r: Resources,
    p: Principal,
    shop_id: UUID,
    ident: UUID,
    offset: int = 0,
    limit: int = 20,
) -> DiagnosticsOut:
    async with r.sessions() as session:
        await scope(session, p, shop_id, "audit")
        row = await session.get(BusinessReport, ident)
        if row is None or row.shop_id != shop_id:
            raise missing()
        result = DiagnosticsOut.model_validate(row.payload)
        total_rows = max(result.total_skus, len(result.previous), result.decomposition_total)
        result.current = result.current[offset : offset + limit]
        result.previous = result.previous[offset : offset + limit]
        result.decomposition = result.decomposition[offset : offset + limit]
        result.next_offset = offset + limit if offset + limit < total_rows else None
        return result


async def evidence(
    r: Resources,
    p: Principal,
    shop_id: UUID,
    ident: UUID,
    cursor: UUID | None,
    limit: int,
) -> EvidenceOut:
    async with r.sessions() as session:
        await scope(session, p, shop_id, "audit")
        parent = await session.get(BusinessReport, ident)
        if parent is None or parent.shop_id != shop_id:
            raise missing()
        query = select(ReportEvidence).where(ReportEvidence.report_id == ident)
        if cursor:
            query = query.where(ReportEvidence.id > cursor)
        rows = list(await session.scalars(query.order_by(ReportEvidence.id).limit(limit + 1)))
        return EvidenceOut(
            report_id=ident,
            items=[x.payload for x in rows[:limit]],
            next_cursor=rows[limit - 1].id if len(rows) > limit else None,
        )


async def save_expense(
    r: Resources, p: Principal, shop_id: UUID, order_id: UUID, data: ExpenseIn, key: str
) -> ExpenseOut:
    async with r.sessions.begin() as session:
        await writer(session, p, shop_id)
        order = await session.scalar(
            select(Order).where(Order.id == order_id, Order.shop_id == shop_id).with_for_update()
        )
        if order is None:
            raise missing()
        value = [str(order_id), data.model_dump(mode="json")]
        prior = await replay(session, p, shop_id, "merchant.expense", key, value)
        if prior:
            row = await session.get(OrderExpense, prior)
            if row is None:
                raise missing()
        else:
            revision = (
                await session.scalar(
                    select(func.max(OrderExpense.revision)).where(
                        OrderExpense.order_id == order_id, OrderExpense.category == data.category
                    )
                )
                or 0
            )
            version(revision, data.expected_revision)
            row = OrderExpense(
                shop_id=shop_id,
                order_id=order_id,
                revision=revision + 1,
                **data.model_dump(exclude={"expected_revision"}),
            )
            session.add(row)
            await session.flush()
            remember(session, p, shop_id, "merchant.expense", key, value, row.id)
            session.add(
                audit(p, shop_id, "merchant.expense", row.id, f"追加费用修订 {row.revision}")
            )
        return ExpenseOut(
            id=row.id,
            order_id=row.order_id,
            revision=row.revision,
            expected_revision=row.revision - 1,
            category=data.category,
            quality=row.quality,
            amount_fen=row.amount_fen,
            complete=row.complete,
            note=row.note,
        )


async def expenses(r: Resources, p: Principal, shop_id: UUID, order_id: UUID) -> list[ExpenseOut]:
    async with r.sessions() as session:
        await scope(session, p, shop_id, "audit")
        if not await session.scalar(
            select(Order.id).where(Order.id == order_id, Order.shop_id == shop_id)
        ):
            raise missing()
        latest: dict[str, OrderExpense] = {}
        for row in await session.scalars(
            select(OrderExpense)
            .where(OrderExpense.shop_id == shop_id, OrderExpense.order_id == order_id)
            .order_by(OrderExpense.revision)
        ):
            latest[row.category] = row
        return [
            ExpenseOut(
                id=row.id,
                order_id=row.order_id,
                revision=row.revision,
                expected_revision=row.revision - 1,
                category=row.category,
                quality=row.quality,
                amount_fen=row.amount_fen,
                complete=row.complete,
                note=row.note,
            )
            for row in latest.values()
        ]


async def reports(
    r: Resources, p: Principal, shop_id: UUID, cursor: UUID | None, limit: int
) -> list[ReportIndex]:
    async with r.sessions() as session:
        await scope(session, p, shop_id, "audit")
        query = select(BusinessReport).where(BusinessReport.shop_id == shop_id)
        if cursor:
            query = query.where(BusinessReport.id > cursor)
        return [
            ReportIndex.model_validate({f: row.payload[f] for f in ReportIndex.model_fields})
            for row in await session.scalars(query.order_by(BusinessReport.id).limit(limit))
        ]
