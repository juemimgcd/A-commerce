"""提供方资金流水快照契约；固定上界后分页，新增流水留给下一轮核对。"""

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from a_commerce.conf.resources import Resources
from a_commerce.integrations.payments.client import ProviderResult
from a_commerce.schemas.api import Schema


class LedgerEntry(ProviderResult):
    sequence: int = Field(gt=0)
    kind: Literal["payment", "refund"]
    observed_at: datetime
    time_basis: Literal["provider_observed", "migration_observed"]

    @model_validator(mode="after")
    def terminal_fact(self) -> "LedgerEntry":
        if self.state != ("paid" if self.kind == "payment" else "succeeded"):
            raise ValueError("资金流水只能记录已经确认的收款或退款")
        if self.kind == "payment" and self.id != self.intent_id:
            raise ValueError("收款流水必须使用同一个支付意图 ID")
        if self.amount_fen <= 0 or self.observed_at.tzinfo is None:
            raise ValueError("流水金额和时间无效")
        return self


class LedgerPage(Schema):
    through_sequence: int = Field(ge=0)
    next_after: int = Field(ge=0)
    complete: bool
    items: list[LedgerEntry] = Field(max_length=100)


async def page(r: Resources, after: int, through: int | None) -> LedgerPage:
    params = {"after": str(after), "limit": "100"}
    if through is not None:
        params["through"] = str(through)
    response = await r.http.get(
        f"{r.settings.provider_url}/ledger",
        params=params,
        headers={"Authorization": f"Bearer {r.settings.provider_key.get_secret_value()}"},
    )
    response.raise_for_status()
    result = LedgerPage.model_validate(response.json())
    if through is not None and result.through_sequence != through:
        raise ValueError("提供方改变了本轮资金快照的上界")
    previous = after
    for item in result.items:
        if not previous < item.sequence <= result.through_sequence:
            raise ValueError("提供方流水分页出现重复或逆序")
        previous = item.sequence
    if not after <= result.next_after <= result.through_sequence:
        raise ValueError("提供方流水游标越界")
    if result.complete:
        if result.next_after != result.through_sequence:
            raise ValueError("完整页没有到达资金快照上界")
    elif not result.items or result.next_after != previous or previous <= after:
        raise ValueError("提供方分页没有前进")
    return result
