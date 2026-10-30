"""经营工具仅开放分析及内部资料写入，生效动作不注册给模型。"""

from uuid import UUID

from a_commerce.conf.resources import Resources
from a_commerce.errors import missing
from a_commerce.schemas.merchant_ops import PeriodIn, PromotionIn, RestockIn, SegmentIn
from a_commerce.schemas.support import (
    BusinessResourceTool,
    DiagnosticTool,
    InventoryTool,
    PromotionTool,
    RepeatTool,
    RestockDraftTool,
    SegmentTool,
    SupportTool,
)
from a_commerce.services import (
    merchant_analysis,
    merchant_customers,
    merchant_inventory,
    merchant_promotions,
)
from a_commerce.services.identity import Principal
from a_commerce.services.merchant_common import digest

OPS_TOOLS = (
    DiagnosticTool,
    InventoryTool,
    RestockDraftTool,
    BusinessResourceTool,
    SegmentTool,
    RepeatTool,
    PromotionTool,
)


async def call(
    r: Resources, p: Principal, shop_id: UUID, run_id: UUID, data: SupportTool
) -> tuple[dict[str, object], list[dict[str, str]]]:
    key = digest([run_id, data.model_dump(mode="json")])
    href = f"/merchant?shop={shop_id}"
    label = "打开本店工作台"
    if isinstance(data, DiagnosticTool):
        row = await merchant_analysis.diagnose(
            r, p, shop_id, PeriodIn.model_validate(data.model_dump(exclude={"tool"})), key, run_id
        )
        result = (await merchant_analysis.report(r, p, shop_id, row.report_id)).model_dump(
            mode="json"
        )
        href += f"&tab=diagnostics&report={row.report_id}"
        label = "查看诊断与证据"
    elif isinstance(data, InventoryTool):
        result = (
            await merchant_inventory.inventory(r, p, shop_id, data.offset, data.limit, data.sku_id)
        ).model_dump(mode="json")
        href += "&tab=inventory"
    elif isinstance(data, RestockDraftTool):
        task = await merchant_inventory.draft(
            r, p, shop_id, RestockIn.model_validate(data.model_dump(exclude={"tool"})), key, run_id
        )
        result = task.model_dump(mode="json")
        href += f"&tab=inventory&task={task.id}"
        label = "核对补货草稿"
    elif isinstance(data, SegmentTool):
        rules = SegmentIn.model_validate(data.model_dump(exclude={"tool"}))
        if data.tool == "preview_customer_segment":
            result = (await merchant_customers.preview(r, p, shop_id, rules)).model_dump(
                mode="json"
            )
        else:
            result = (await merchant_customers.save(r, p, shop_id, rules, key, run_id)).model_dump(
                mode="json"
            )
        href += "&tab=customers"
    elif isinstance(data, RepeatTool):
        result = (
            await merchant_customers.repeat(
                r,
                p,
                shop_id,
                PeriodIn.model_validate(data.model_dump(exclude={"tool", "days"})),
                data.days,
            )
        ).model_dump(mode="json")
        href += "&tab=customers"
    elif isinstance(data, PromotionTool):
        proposal = PromotionIn.model_validate(data.model_dump(exclude={"tool"}))
        if data.tool == "simulate_promotion":
            result = (await merchant_promotions.simulate(r, p, shop_id, proposal)).model_dump(
                mode="json"
            )
        else:
            promotion = await merchant_promotions.draft(r, p, shop_id, proposal, key, run_id)
            result = promotion.model_dump(mode="json")
            href += f"&tab=promotions&promotion={promotion.id}"
            label = "核对活动草稿"
    elif isinstance(data, BusinessResourceTool):
        if data.tool == "get_saved_business_report":
            result = (
                await merchant_analysis.report(
                    r, p, shop_id, data.resource_id, data.offset, data.limit
                )
            ).model_dump(mode="json")
            href += f"&tab=diagnostics&report={data.resource_id}"
        elif data.tool == "get_report_evidence":
            result = (
                await merchant_analysis.evidence(
                    r, p, shop_id, data.resource_id, data.cursor, data.limit
                )
            ).model_dump(mode="json")
            href += f"&tab=diagnostics&report={data.resource_id}"
        elif data.tool == "get_restock_task":
            result = (await merchant_inventory.tasks(r, p, shop_id, ident=data.resource_id))[
                0
            ].model_dump(mode="json")
            href += f"&tab=inventory&task={data.resource_id}"
        elif data.tool == "validate_promotion_draft":
            result = (
                await merchant_promotions.validate_draft(r, p, shop_id, data.resource_id)
            ).model_dump(mode="json")
            href += f"&tab=promotions&promotion={data.resource_id}"
        else:
            result = (
                await merchant_promotions.promotion_report(r, p, shop_id, data.resource_id)
            ).model_dump(mode="json")
            href += f"&tab=promotions&promotion={data.resource_id}"
    else:
        raise missing()
    return result, [{"title": label, "href": href}]
