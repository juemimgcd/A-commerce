"""流量参数是初始保护预算，不代表经过压测证明的吞吐能力。"""

from pydantic import BaseModel, ConfigDict, Field


class Quota(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # rate 是每秒恢复的令牌数，burst 是最多积攒的令牌数；一次操作消耗一个。
    rate: float = Field(gt=0, le=100000, allow_inf_nan=False)
    burst: int = Field(ge=1, le=100000)


class TrafficSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # IP 只用作入口保护；共享出口下的用户仍有各自的身份额度。
    entry_ip: Quota = Quota(rate=30, burst=60)
    auth_ip: Quota = Quota(rate=20 / 60, burst=20)
    user_read: Quota = Quota(rate=10, burst=20)
    user_write: Quota = Quota(rate=2, burst=5)
    quote_user: Quota = Quota(rate=1, burst=3)
    order_user: Quota = Quota(rate=0.5, burst=2)
    # 报价也会锁定商品行，因此报价与下单分别保护各自的全局额度和 SKU。
    quote_global: Quota = Quota(rate=40, burst=60)
    order_global: Quota = Quota(rate=20, burst=30)
    quote_sku: Quota = Quota(rate=10, burst=15)
    order_sku: Quota = Quota(rate=5, burst=8)
    payment_callback: Quota = Quota(rate=20, burst=40)
    shipping_callback: Quota = Quota(rate=10, burst=20)
