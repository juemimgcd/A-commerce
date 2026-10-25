"""所有进程使用同一配置契约，连接预算按进程分别配置。"""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from a_commerce.conf.redis import RedisSentinelSettings
from a_commerce.conf.traffic import TrafficSettings


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="COMMERCE_",
        env_nested_delimiter="__",
        extra="ignore",
        hide_input_in_errors=True,
    )

    environment: Literal["development", "production"] = "development"
    database_url: SecretStr
    redis_url: SecretStr
    # 单独的 Redis 进程，不与 allkeys-lru 商品缓存共用内存淘汰策略。
    rate_redis_url: SecretStr = SecretStr("redis://127.0.0.1:56380/0")
    redis_sentinel: RedisSentinelSettings = Field(default_factory=RedisSentinelSettings)
    traffic: TrafficSettings = Field(default_factory=TrafficSettings)
    api_role: Literal["all", "public", "callbacks"] = "all"
    # 后台容器通过独立内网端口导出指标；0 表示关闭。
    metrics_port: int = Field(default=0, ge=0, le=65535)
    amqp_url: SecretStr
    provider_url: str = "http://provider:8001"
    provider_key: SecretStr = Field(min_length=32)
    callback_secret: SecretStr = Field(min_length=32)
    merchant_id: str = "a-commerce"
    db_pool_size: int = Field(default=5, ge=1, le=30)
    db_pool_timeout: float = Field(default=3, gt=0, le=30)
    redis_connections: int = Field(default=10, ge=1, le=100)
    http_connections: int = Field(default=10, ge=1, le=100)
    session_hours: int = Field(default=168, ge=1, le=720)
    quote_seconds: int = Field(default=600, ge=30, le=3600)
    payment_seconds: int = Field(default=900, ge=60, le=3600)
    shipping_fen: int = Field(default=600, ge=0, le=100000)
    worker_concurrency: int = Field(default=4, ge=1, le=32)
    worker_group: Literal["critical", "general", "maintenance"] = "critical"
    operation_lease_seconds: int = Field(default=60, ge=30, le=300)
    operation_max_attempts: int = Field(default=12, ge=1, le=100)
    backlog_limit: int = Field(default=1000, ge=1)
    cache_seconds: int = Field(default=30, ge=1, le=300)
    cache_origin_concurrency: int = Field(default=4, ge=1, le=20)
    password_concurrency: int = Field(default=2, ge=1, le=8)
    return_days: int = Field(default=7, ge=1, le=30)
    files_directory: Path = Path("var/files")
    mail_enabled: bool = False
    smtp_host: str = "localhost"
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_start_tls: bool = True
    mail_from: str = "commerce@localhost"
    reset_secret: SecretStr | None = None
    storage_backend: Literal["local", "s3"] = "local"
    s3_bucket: str | None = None
    s3_endpoint: str | None = None
    reconciliation_enabled: bool = True
    reconciliation_interval_seconds: int = Field(default=86400, ge=300, le=604800)
    maintenance_interval_seconds: int = Field(default=3600, ge=300, le=86400)
    event_retention_days: int = Field(default=90, ge=7, le=3650)
    snapshot_seconds: int = Field(default=900, ge=60, le=3600)
    orphan_grace_seconds: int = Field(default=86400, ge=3600, le=604800)
