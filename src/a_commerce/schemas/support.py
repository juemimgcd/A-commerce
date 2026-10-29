"""客服专用契约。工具只能选择列出的业务能力，不能指定用户身份或任意 URL。"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from a_commerce.schemas.api import Schema

MemoryKind = Literal["category", "brand", "budget", "attribute", "service"]


class SupportSettingsIn(Schema):
    memory_enabled: bool = False
    share_with_merchant: bool = False
    marketing_enabled: bool = False

    @model_validator(mode="after")
    def independent_consent(self) -> "SupportSettingsIn":
        if self.share_with_merchant and not self.memory_enabled:
            raise ValueError("共享画像需要先启用长期记忆")
        if self.marketing_enabled and not self.share_with_merchant:
            raise ValueError("基于画像的营销推荐需要先允许共享画像")
        return self


class MemoryIn(Schema):
    kind: MemoryKind
    value: str = Field(min_length=1, max_length=120, pattern=r"\S")


class MemoryOut(MemoryIn):
    id: UUID
    evidence: str
    source: str
    status: Literal["candidate", "confirmed", "dismissed"]
    updated_at: datetime


class MemoryDecision(Schema):
    status: Literal["confirmed", "dismissed"]


class UsageOut(Schema):
    feature: str
    uses: int
    last_used_at: datetime


class MemoryProfile(Schema):
    settings: SupportSettingsIn
    memories: list[MemoryOut]
    usage: list[UsageOut]


class ConversationIn(Schema):
    title: str = Field(default="新的客服咨询", min_length=1, max_length=80)


class ConversationOut(Schema):
    id: UUID
    title: str
    created_at: datetime


class MessageIn(Schema):
    request_id: UUID
    text: str = Field(min_length=1, max_length=2000, pattern=r"\S")


class SupportLink(Schema):
    title: str
    href: str


class SupportRunOut(Schema):
    id: UUID
    conversation_id: UUID
    request_id: UUID
    user_text: str
    assistant_text: str
    status: Literal["running", "completed", "failed"]
    error_code: str | None
    created_at: datetime
    links: list[SupportLink]
    feedback: Literal["helpful", "unhelpful"] | None
    runtime: dict[str, object]


class FeedbackIn(Schema):
    feedback: Literal["helpful", "unhelpful"]


class SearchTool(Schema):
    tool: Literal["search_products", "recommend_products"]
    q: str = Field(default="", max_length=80)
    category: UUID | None = None
    brand: str = Field(default="", max_length=100)
    cursor: UUID | None = None
    min_price: int = Field(default=0, ge=0, le=100000000)
    max_price: int = Field(default=100000000, ge=0, le=100000000)

    @model_validator(mode="after")
    def prices(self) -> "SearchTool":
        if self.min_price > self.max_price:
            raise ValueError("最低价格不能高于最高价格")
        return self


class ProductTool(Schema):
    tool: Literal["get_product"]
    product_id: UUID


class OrderTool(Schema):
    tool: Literal["get_order", "get_shipment", "get_return_options"]
    order_id: UUID


class ReturnTool(Schema):
    tool: Literal["get_return"]
    return_id: UUID


class SimpleTool(Schema):
    tool: Literal[
        "get_categories",
        "get_account",
        "get_cart",
        "get_orders",
        "get_returns",
        "get_policy",
        "get_memory",
    ]


class ProposeMemoryTool(MemoryIn):
    tool: Literal["propose_memory"]
    evidence: str = Field(min_length=2, max_length=240)


SupportTool = Annotated[
    SearchTool | ProductTool | OrderTool | ReturnTool | SimpleTool | ProposeMemoryTool,
    Field(discriminator="tool"),
]


class AdapterReply(Schema):
    text: str = Field(min_length=1, max_length=12000)
    session_id: UUID
    outcome: Literal["answered", "limited"]
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost: float = Field(ge=0, allow_inf_nan=False)
    total_tokens: int = Field(ge=0)
    provider: str = Field(max_length=100)
    model: str = Field(max_length=200)


class SupportEventIn(Schema):
    type: Literal[
        "turn_start",
        "tool_execution_start",
        "tool_execution_end",
        "compaction_start",
        "compaction_end",
        "auto_retry_start",
        "auto_retry_end",
        "agent_end",
    ]
    elapsed_ms: int = Field(ge=0, le=180000)
    tool: str | None = Field(default=None, max_length=80, pattern=r"^commerce_[a-z_]+$")
    call_id: str | None = Field(default=None, max_length=200)
    is_error: bool = False


class InsightCount(Schema):
    kind: str
    value: str
    users: int


class InsightsOut(Schema):
    shared_users: int
    marketing_users: int
    interests: list[InsightCount]
    features: list[InsightCount]
    helpful: int
    unhelpful: int


class AudiencePreference(MemoryIn):
    id: UUID
    updated_at: datetime


class AudienceUser(Schema):
    user_id: UUID
    email: str
    marketing_enabled: bool
    preferences: list[AudiencePreference]
    usage: list[UsageOut]


class AudiencePage(Schema):
    items: list[AudienceUser]
    next_cursor: UUID | None
