"""模拟支付 HTTP 契约。所有写入沿用本地稳定 ID；超时后必须先查询再决定重发。"""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from a_commerce.conf.resources import Resources


class ProviderResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    intent_id: UUID
    merchant_id: str
    amount_fen: int
    currency: Literal["CNY"]
    state: Literal["pending", "paid", "closed", "succeeded"]


async def request(
    r: Resources, method: str, path: str, payload: dict[str, str | int] | None = None
) -> ProviderResult | None:
    response = await r.http.request(
        method,
        f"{r.settings.provider_url}{path}",
        json=payload,
        headers={"Authorization": f"Bearer {r.settings.provider_key.get_secret_value()}"},
    )
    if method == "GET" and response.status_code == 404:
        return None
    response.raise_for_status()
    return ProviderResult.model_validate(response.json())
