"""运费规则使用区域精确匹配；报价记录规则版本，规则变化要求重新确认。"""

from uuid import UUID

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a_commerce.conf.resources import Resources
from a_commerce.models.entities import Audit, ShippingRule
from a_commerce.schemas.api import Schema
from a_commerce.services.identity import Principal, authorize


class ShippingRuleIn(Schema):
    region: str = Field(min_length=1, max_length=120)
    fee_fen: int = Field(ge=0, le=100000)
    free_above_fen: int | None = Field(default=None, ge=0, le=100000000)


class ShippingRuleOut(ShippingRuleIn):
    id: UUID
    version: int


async def shipping_price(
    session: AsyncSession, region: str, subtotal: int, default_fee: int
) -> tuple[int, str]:
    rule = await session.scalar(
        select(ShippingRule).where(ShippingRule.region == region).with_for_update(read=True)
    )
    if rule is None:
        return default_fee, f"default:{default_fee}"
    fee = 0 if rule.free_above_fen is not None and subtotal >= rule.free_above_fen else rule.fee_fen
    return fee, f"{rule.id}:{rule.version}"


async def save_rule(r: Resources, p: Principal, data: ShippingRuleIn) -> ShippingRuleOut:
    async with r.sessions.begin() as session:
        await authorize(session, p, "catalog", lock=True)
        rule = await session.scalar(
            select(ShippingRule).where(ShippingRule.region == data.region).with_for_update()
        )
        if rule is None:
            rule = ShippingRule(**data.model_dump())
            session.add(rule)
        else:
            rule.fee_fen = data.fee_fen
            rule.free_above_fen = data.free_above_fen
            rule.version += 1
        await session.flush()
        session.add(
            Audit(
                actor_id=p.id,
                action="shipping_rule.save",
                resource_id=rule.id,
                detail=f"运费规则版本 {rule.version}",
            )
        )
        return ShippingRuleOut.model_validate(rule)
