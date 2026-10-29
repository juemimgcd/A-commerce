"""可选客服进程配置，未配置时只关闭模型对话，不影响商城交易。"""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class SupportConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="COMMERCE_SUPPORT_", env_file=".env", extra="ignore"
    )
    adapter_url: str = "http://127.0.0.1:58010"
    bridge_secret: SecretStr | None = None
    timeout_seconds: int = Field(default=90, ge=10, le=180)
    hourly_messages: int = Field(default=30, ge=1, le=300)


@lru_cache
def support_config() -> SupportConfig:
    return SupportConfig()
