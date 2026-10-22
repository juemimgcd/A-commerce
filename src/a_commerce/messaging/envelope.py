"""消息只携带操作引用；金额、账号和业务参数必须从数据库读取。"""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    message_id: UUID
    operation_id: UUID
    operation_type: Literal[
        "payment", "close", "refund", "mail", "export", "cache", "reconcile", "housekeeping"
    ]
    dispatch_version: int = Field(ge=1)
