"""商家公开资料由部署提供；不把演示政策和虚构联系方式冒充正式商家信息。"""

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ReturnAddress(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recipient: str = Field(min_length=1, max_length=80)
    phone: str = Field(min_length=5, max_length=32)
    region: str = Field(min_length=1, max_length=120)
    detail: str = Field(min_length=1, max_length=300)


class ShopSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="A-commerce", min_length=1, max_length=100)
    support_email: EmailStr | None = None
    support_phone: str | None = Field(default=None, max_length=32)
    return_address: ReturnAddress | None = None
    privacy_text: str | None = Field(default=None, max_length=20000)
    terms_text: str | None = Field(default=None, max_length=20000)
