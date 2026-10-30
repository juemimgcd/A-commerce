"""店铺经营分析、内部资料与店主明确确认入口。"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query

from a_commerce.dependencies import Admin, Customer, Key, R
from a_commerce.schemas.api import VersionIn
from a_commerce.schemas.merchant_ops import (
    DiagnosticsOut,
    EmergencyStopIn,
    EvidenceOut,
    ExpenseIn,
    ExpenseOut,
    MerchantInventoryOut,
    PeriodIn,
    PromotionIn,
    PromotionOut,
    PromotionReportOut,
    ReceiptIn,
    RepeatOut,
    ReportIndex,
    RestockEdit,
    RestockIn,
    RestockOut,
    RuleIn,
    SegmentIn,
    SegmentOut,
    SegmentPreview,
    SimulationOut,
)
from a_commerce.services import (
    merchant_analysis,
    merchant_customers,
    merchant_inventory,
    merchant_promotions,
)
from a_commerce.services.shop_access import scope

router = APIRouter(prefix="/api/v1/merchant/shops/{shop_id}", tags=["商家经营助手"])
platform_router = APIRouter(prefix="/api/v1/admin/shops/{shop_id}", tags=["商家活动应急处置"])
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=1000000)]


@router.get("/operations/capabilities")
async def capabilities(shop_id: UUID, r: R, p: Customer) -> dict[str, bool]:
    async with r.sessions() as session:
        await scope(session, p, shop_id, "audit")
    return {"promotions_enabled": r.settings.merchant_promotions_enabled}


@router.post("/diagnostics", response_model=DiagnosticsOut)
async def diagnose(shop_id: UUID, data: PeriodIn, r: R, p: Customer, key: Key) -> DiagnosticsOut:
    row = await merchant_analysis.diagnose(r, p, shop_id, data, key)
    return await merchant_analysis.report(r, p, shop_id, row.report_id)


@router.get("/diagnostics", response_model=list[ReportIndex])
async def reports(
    shop_id: UUID, r: R, p: Customer, cursor: UUID | None = None, limit: Limit = 20
) -> list[ReportIndex]:
    return await merchant_analysis.reports(r, p, shop_id, cursor, limit)


@router.get("/diagnostics/{report_id}", response_model=DiagnosticsOut)
async def report(
    shop_id: UUID, report_id: UUID, r: R, p: Customer, offset: Offset = 0, limit: Limit = 20
) -> DiagnosticsOut:
    return await merchant_analysis.report(r, p, shop_id, report_id, offset, limit)


@router.get("/diagnostics/{report_id}/evidence", response_model=EvidenceOut)
async def evidence(
    shop_id: UUID, report_id: UUID, r: R, p: Customer, cursor: UUID | None = None, limit: Limit = 20
) -> EvidenceOut:
    return await merchant_analysis.evidence(r, p, shop_id, report_id, cursor, limit)


@router.post("/orders/{order_id}/expenses", response_model=ExpenseOut)
async def expense(
    shop_id: UUID, order_id: UUID, data: ExpenseIn, r: R, p: Customer, key: Key
) -> ExpenseOut:
    return await merchant_analysis.save_expense(r, p, shop_id, order_id, data, key)


@router.get("/orders/{order_id}/expenses", response_model=list[ExpenseOut])
async def expenses(shop_id: UUID, order_id: UUID, r: R, p: Customer) -> list[ExpenseOut]:
    return await merchant_analysis.expenses(r, p, shop_id, order_id)


@router.get("/inventory-risks", response_model=MerchantInventoryOut)
async def inventory(
    shop_id: UUID,
    r: R,
    p: Customer,
    offset: Offset = 0,
    limit: Limit = 20,
    sku_id: UUID | None = None,
) -> MerchantInventoryOut:
    return await merchant_inventory.inventory(r, p, shop_id, offset, limit, sku_id)


@router.put("/restock-rules/{sku_id}", response_model=RuleIn)
async def rule(shop_id: UUID, sku_id: UUID, data: RuleIn, r: R, p: Customer) -> RuleIn:
    return await merchant_inventory.save_rule(r, p, shop_id, sku_id, data)


@router.get("/restock-tasks", response_model=list[RestockOut])
async def tasks(
    shop_id: UUID, r: R, p: Customer, cursor: UUID | None = None, limit: Limit = 20
) -> list[RestockOut]:
    return await merchant_inventory.tasks(r, p, shop_id, cursor, limit)


@router.post("/restock-tasks", response_model=RestockOut, status_code=201)
async def draft_restock(shop_id: UUID, data: RestockIn, r: R, p: Customer, key: Key) -> RestockOut:
    return await merchant_inventory.draft(r, p, shop_id, data, key)


@router.get("/restock-tasks/{task_id}", response_model=RestockOut)
async def task(shop_id: UUID, task_id: UUID, r: R, p: Customer) -> RestockOut:
    return (await merchant_inventory.tasks(r, p, shop_id, ident=task_id))[0]


@router.post("/restock-tasks/{task_id}/confirm", response_model=RestockOut)
async def confirm(
    shop_id: UUID, task_id: UUID, data: RestockEdit, r: R, p: Customer, key: Key
) -> RestockOut:
    return await merchant_inventory.transition(r, p, shop_id, task_id, "confirm", data, key)


@router.post("/restock-tasks/{task_id}/receive", response_model=RestockOut)
async def receive(
    shop_id: UUID, task_id: UUID, data: ReceiptIn, r: R, p: Customer, key: Key
) -> RestockOut:
    return await merchant_inventory.transition(r, p, shop_id, task_id, "receive", data, key)


@router.post("/restock-tasks/{task_id}/{action}", response_model=RestockOut)
async def progress(
    shop_id: UUID,
    task_id: UUID,
    action: Literal["start", "cancel"],
    data: VersionIn,
    r: R,
    p: Customer,
    key: Key,
) -> RestockOut:
    return await merchant_inventory.transition(r, p, shop_id, task_id, action, data, key)


@router.post("/segments/preview", response_model=SegmentPreview)
async def preview(shop_id: UUID, data: SegmentIn, r: R, p: Customer) -> SegmentPreview:
    return await merchant_customers.preview(r, p, shop_id, data)


@router.post("/segments", response_model=SegmentOut, status_code=201)
async def save_segment(shop_id: UUID, data: SegmentIn, r: R, p: Customer, key: Key) -> SegmentOut:
    return await merchant_customers.save(r, p, shop_id, data, key)


@router.get("/segments", response_model=list[SegmentOut])
async def segments(
    shop_id: UUID, r: R, p: Customer, cursor: UUID | None = None, limit: Limit = 20
) -> list[SegmentOut]:
    return await merchant_customers.segments(r, p, shop_id, cursor, limit)


@router.post("/segments/{segment_id}/deactivate", response_model=SegmentOut)
async def deactivate(shop_id: UUID, segment_id: UUID, r: R, p: Customer) -> SegmentOut:
    return await merchant_customers.deactivate(r, p, shop_id, segment_id)


@router.post("/repeat-purchase", response_model=RepeatOut)
async def repeat(
    shop_id: UUID, data: PeriodIn, r: R, p: Customer, days: Annotated[int, Query(ge=1, le=366)] = 30
) -> RepeatOut:
    return await merchant_customers.repeat(r, p, shop_id, data, days)


@router.post("/promotions/simulate", response_model=SimulationOut)
async def simulate(shop_id: UUID, data: PromotionIn, r: R, p: Customer) -> SimulationOut:
    return await merchant_promotions.simulate(r, p, shop_id, data)


@router.post("/promotions", response_model=PromotionOut, status_code=201)
async def draft_promotion(
    shop_id: UUID, data: PromotionIn, r: R, p: Customer, key: Key
) -> PromotionOut:
    return await merchant_promotions.draft(r, p, shop_id, data, key)


@router.get("/promotions", response_model=list[PromotionOut])
async def promotions(
    shop_id: UUID, r: R, p: Customer, cursor: UUID | None = None, limit: Limit = 20
) -> list[PromotionOut]:
    return await merchant_promotions.promotions(r, p, shop_id, cursor, limit)


@router.get("/promotions/{promotion_id}", response_model=PromotionOut)
async def promotion(shop_id: UUID, promotion_id: UUID, r: R, p: Customer) -> PromotionOut:
    return (await merchant_promotions.promotions(r, p, shop_id, None, 1, promotion_id))[0]


@router.get("/promotions/{promotion_id}/validation", response_model=SimulationOut)
async def validate(shop_id: UUID, promotion_id: UUID, r: R, p: Customer) -> SimulationOut:
    return await merchant_promotions.validate_draft(r, p, shop_id, promotion_id)


@router.post("/promotions/{promotion_id}/publish", response_model=PromotionOut)
async def publish(
    shop_id: UUID, promotion_id: UUID, data: VersionIn, r: R, p: Customer, key: Key
) -> PromotionOut:
    return await merchant_promotions.publish(r, p, shop_id, promotion_id, data, key)


@router.post("/promotions/{promotion_id}/stop", response_model=PromotionOut)
async def stop(
    shop_id: UUID, promotion_id: UUID, data: VersionIn, r: R, p: Customer, key: Key
) -> PromotionOut:
    return await merchant_promotions.stop(r, p, shop_id, promotion_id, data, key)


@router.get("/promotions/{promotion_id}/report", response_model=PromotionReportOut)
async def promotion_report(
    shop_id: UUID, promotion_id: UUID, r: R, p: Customer
) -> PromotionReportOut:
    return await merchant_promotions.promotion_report(r, p, shop_id, promotion_id)


@platform_router.post("/promotions/{promotion_id}/stop", response_model=PromotionOut)
async def emergency_stop(
    shop_id: UUID, promotion_id: UUID, data: EmergencyStopIn, r: R, p: Admin, key: Key
) -> PromotionOut:
    return await merchant_promotions.stop(
        r, p, shop_id, promotion_id, data, key, reason=data.reason
    )
