"""账单对账只记录差异；管理员主动复核后，才使用同一资金入账函数补齐事实。"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from a_commerce.conf.resources import Resources
from a_commerce.crud.queries import order_locked
from a_commerce.errors import CommerceError, missing
from a_commerce.integrations.payments.client import ProviderResult, request
from a_commerce.integrations.payments.ledger import LedgerPage
from a_commerce.models.base import now
from a_commerce.models.entities import (
    Audit,
    Order,
    PaymentIntent,
    ReconciliationCase,
    ReconciliationEntry,
    ReconciliationRun,
    Refund,
)
from a_commerce.schemas.api import Schema
from a_commerce.services.identity import Principal, authorize
from a_commerce.services.operations import enqueue
from a_commerce.services.payments import apply_payment
from a_commerce.services.refunds import apply_refund


class RunOut(Schema):
    id: UUID
    status: str
    through_sequence: int | None
    after_sequence: int
    entries_checked: int
    created_at: datetime
    finished_at: datetime | None


class CaseOut(Schema):
    id: UUID
    kind: str
    resource_id: UUID
    order_id: UUID | None
    status: str
    reason: str
    evidence: dict[str, object]
    checked_at: datetime
    resolution_note: str | None


async def start(session: AsyncSession) -> ReconciliationRun:
    run = ReconciliationRun()
    session.add(run)
    await session.flush()
    await enqueue(session, "reconcile", run.id)
    return run


async def create(r: Resources, p: Principal) -> RunOut:
    async with r.sessions.begin() as session:
        await authorize(session, p, "audit", lock=True)
        run = await start(session)
        session.add(
            Audit(
                actor_id=p.id,
                action="reconciliation.start",
                resource_id=run.id,
                detail="按提供方固定流水快照核对收款和退款",
            )
        )
        return RunOut.model_validate(run)


async def runs(r: Resources, p: Principal, cursor: UUID | None, limit: int) -> list[RunOut]:
    async with r.sessions() as session:
        await authorize(session, p, "audit")
        query = select(ReconciliationRun)
        if cursor:
            query = query.where(ReconciliationRun.id > cursor)
        return [
            RunOut.model_validate(row)
            for row in await session.scalars(query.order_by(ReconciliationRun.id).limit(limit))
        ]


async def get_run(r: Resources, p: Principal, run_id: UUID) -> RunOut:
    async with r.sessions() as session:
        await authorize(session, p, "audit")
        run = await session.get(ReconciliationRun, run_id)
        if run is None:
            raise missing()
        return RunOut.model_validate(run)


async def cases(
    r: Resources, p: Principal, status: str, cursor: UUID | None, limit: int
) -> list[CaseOut]:
    async with r.sessions() as session:
        await authorize(session, p, "audit")
        query = select(ReconciliationCase).where(ReconciliationCase.status == status)
        if cursor:
            query = query.where(ReconciliationCase.id > cursor)
        return [
            CaseOut.model_validate(row)
            for row in await session.scalars(query.order_by(ReconciliationCase.id).limit(limit))
        ]


async def compare(
    session: AsyncSession,
    kind: str,
    resource_id: UUID,
    result: ProviderResult | None,
    merchant: str,
) -> tuple[str | None, UUID | None]:
    """先对身份和金额，再对状态；金额不同绝不自动修账。"""
    if kind == "payment":
        intent = await session.get(PaymentIntent, resource_id)
        order = await session.get(Order, intent.order_id) if intent else None
        amount = order.total_fen if order else None
        confirmed = order is not None and order.paid_at is not None
    else:
        refund = await session.get(Refund, resource_id)
        order = await session.get(Order, refund.order_id) if refund else None
        intent = (
            await session.scalar(select(PaymentIntent).where(PaymentIntent.order_id == order.id))
            if order
            else None
        )
        amount = refund.amount_fen if refund else None
        confirmed = refund is not None and refund.status == "succeeded"
    if order is None or intent is None:
        return "local_record_missing", None
    if result is None:
        return "provider_record_missing", order.id
    if (
        result.id != resource_id
        or result.intent_id != intent.id
        or result.amount_fen != amount
        or result.merchant_id != merchant
    ):
        return "identity_or_amount_mismatch", order.id
    if result.state != ("paid" if kind == "payment" else "succeeded"):
        return "provider_state_mismatch", order.id
    return (None if confirmed else "local_confirmation_missing"), order.id


async def save_case(
    session: AsyncSession,
    run: ReconciliationRun,
    kind: str,
    resource_id: UUID,
    reason: str | None,
    order_id: UUID | None,
    evidence: dict[str, object],
    expected_revision: int | None,
) -> None:
    if expected_revision is None:
        # 读取证据时尚无差异单，只允许首次创建；竞争者已创建时保留对方结论。
        # 不能先 INSERT DO NOTHING，再不加判断地覆盖刚被其他事务写入的记录。
        await session.execute(
            insert(ReconciliationCase)
            .values(
                run_id=run.id,
                kind=kind,
                resource_id=resource_id,
                order_id=order_id,
                status="open" if reason else "resolved",
                reason=reason or "matched",
                evidence=evidence,
                checked_at=now(),
                revision=1,
            )
            .on_conflict_do_nothing(index_elements=["kind", "resource_id"])
        )
        return
    case = (
        await session.scalars(
            select(ReconciliationCase)
            .where(ReconciliationCase.kind == kind, ReconciliationCase.resource_id == resource_id)
            .with_for_update()
        )
    ).one()
    # 版本必须在比较本地状态之前读取。等待差异单锁期间如果有复核/其他写回，
    # 此次 reason 就不再可靠，不能把已经 resolved 的记录重新改成 open。
    if case.revision != expected_revision:
        return
    # 反向检查使用整轮固定账单；即使现在才读到新版本，其账单也可能早于复核。
    # 人工复核后的下一轮仍可检查新差异，只有复核之前启动的旧轮次被挡住。
    if case.reviewed_at is not None and case.reviewed_at >= run.created_at:
        return
    previous_run = await session.get(ReconciliationRun, case.run_id)
    if previous_run and previous_run.created_at > run.created_at:
        return
    case.run_id, case.order_id = run.id, order_id
    case.status, case.reason = ("open", reason) if reason else ("resolved", "matched")
    case.evidence, case.checked_at = evidence, now()
    case.revision += 1


async def consume_page(
    session: AsyncSession, run: ReconciliationRun, page: LedgerPage, merchant: str
) -> None:
    for entry in page.items:
        duplicate = await session.scalar(
            select(ReconciliationEntry.id).where(
                ReconciliationEntry.run_id == run.id,
                ReconciliationEntry.kind == entry.kind,
                ReconciliationEntry.resource_id == entry.id,
            )
        )
        if duplicate:
            raise ValueError("提供方资金流水重复出现同一业务 ID")
        evidence = entry.model_dump(mode="json")
        session.add(
            ReconciliationEntry(
                run_id=run.id, kind=entry.kind, resource_id=entry.id, evidence=evidence
            )
        )
        # 只预读版本，不把实体放入 identity map，之后加锁时必须获取最新字段。
        expected_revision = await session.scalar(
            select(ReconciliationCase.revision).where(
                ReconciliationCase.kind == entry.kind, ReconciliationCase.resource_id == entry.id
            )
        )
        reason, order_id = await compare(session, entry.kind, entry.id, entry, merchant)
        # 正常记录只存本轮证据；已有异常单在事实一致且版本未变化时才自动解决。
        if reason or expected_revision is not None:
            await save_case(
                session, run, entry.kind, entry.id, reason, order_id, evidence, expected_revision
            )
    run.through_sequence = page.through_sequence
    run.after_sequence = page.next_after
    run.entries_checked += len(page.items)
    if page.complete:
        run.status = "local_payments"
        run.local_cursor = None


async def check_local(session: AsyncSession, run: ReconciliationRun) -> None:
    # 反向检查本地已确认资金，防止只遍历提供方账单而漏掉本地多记账。
    payment_phase = run.status == "local_payments"
    kind = "payment" if payment_phase else "refund"
    if payment_phase:
        query = (
            select(PaymentIntent.id, Order.id).join(Order).where(Order.paid_at <= run.created_at)
        )
        column = PaymentIntent.id
    else:
        query = select(Refund.id, Refund.order_id).where(
            Refund.status == "succeeded", Refund.confirmed_at <= run.created_at
        )
        column = Refund.id
    if run.local_cursor:
        query = query.where(column > run.local_cursor)
    rows = list((await session.execute(query.order_by(column).limit(100))).all())
    revisions = dict(
        (
            await session.execute(
                select(ReconciliationCase.resource_id, ReconciliationCase.revision).where(
                    ReconciliationCase.kind == kind,
                    ReconciliationCase.resource_id.in_([row[0] for row in rows]),
                )
            )
        )
        .tuples()
        .all()
    )
    present = set(
        await session.scalars(
            select(ReconciliationEntry.resource_id).where(
                ReconciliationEntry.run_id == run.id,
                ReconciliationEntry.kind == kind,
                ReconciliationEntry.resource_id.in_([row[0] for row in rows]),
            )
        )
    )
    for resource_id, order_id in rows:
        if resource_id not in present:
            await save_case(
                session,
                run,
                kind,
                resource_id,
                "missing_from_statement",
                order_id,
                {"through_sequence": run.through_sequence},
                revisions.get(resource_id),
            )
    run.entries_checked += len(rows)
    if rows:
        run.local_cursor = rows[-1][0]
    if len(rows) < 100:
        run.local_cursor = None
        run.status = "local_refunds" if payment_phase else "done"
        if run.status == "done":
            run.finished_at = now()


async def recheck(r: Resources, p: Principal, case_id: UUID, note: str) -> CaseOut:
    async with r.sessions() as session:
        await authorize(session, p, "audit")
        case = await session.get(ReconciliationCase, case_id)
        if case is None:
            raise missing()
        kind, resource_id, expected_revision = case.kind, case.resource_id, case.revision
    # 复核走认证的主动查询。网络期间不持有数据库锁，写回再次校验权限及本地金额。
    result = await request(
        r, "GET", f"/{'payments' if kind == 'payment' else 'refunds'}/{resource_id}"
    )
    async with r.sessions.begin() as session:
        await authorize(session, p, "audit", lock=True)
        reason, order_id = await compare(session, kind, resource_id, result, r.settings.merchant_id)
        order = None
        if order_id is not None:
            order = await order_locked(session, order_id)
            # compare 的预读对象可能在等待订单锁期间变旧；补账前刷新同事务 identity map。
            await session.refresh(order)
            if kind == "refund":
                current_refund = await session.get(Refund, resource_id)
                if current_refund is not None:
                    await session.refresh(current_refund)
            reason, _ = await compare(session, kind, resource_id, result, r.settings.merchant_id)
        case = await session.scalar(
            select(ReconciliationCase).where(ReconciliationCase.id == case_id).with_for_update()
        )
        if case is None:
            raise missing()
        # 锁顺序仍为订单 → 差异单。先验证查询前的版本，再补账；并发复核时，
        # 迟到的外部响应只能返回冲突，不能覆盖先完成的复核，也不能先产生资金副作用。
        if case.revision != expected_revision:
            raise CommerceError("RECONCILIATION_CHANGED", "差异记录已更新，请重新查询并复核。")
        if reason == "local_confirmation_missing" and result is not None and order is not None:
            if kind == "payment":
                await apply_payment(session, order, result)
            else:
                refund = await session.get(Refund, resource_id)
                if refund is None:
                    raise missing()
                await apply_refund(session, order, refund)
            reason = None
        case.status = "open" if reason else "resolved"
        case.reason = reason or "matched"
        case.checked_at, case.resolution_note = now(), note
        case.reviewed_at = case.checked_at
        case.revision += 1
        case.evidence = result.model_dump(mode="json") if result else {"provider_record": "missing"}
        session.add(
            Audit(actor_id=p.id, action="reconciliation.recheck", resource_id=case.id, detail=note)
        )
        return CaseOut.model_validate(case)
