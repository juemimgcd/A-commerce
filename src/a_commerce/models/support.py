"""客服对话与用户记忆。聊天记录不承担交易事实，候选偏好不等于用户确认。"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from a_commerce.models.base import Record


class SupportPreference(Record):
    __tablename__ = "support_preferences"
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), unique=True)
    memory_enabled: Mapped[bool] = mapped_column(default=False)
    share_with_merchant: Mapped[bool] = mapped_column(default=False)
    marketing_enabled: Mapped[bool] = mapped_column(default=False)
    # 清除/关闭记忆时递增；旧模型调用不能在用户清除之后重新写回。
    generation: Mapped[int] = mapped_column(default=1)


class SupportConversation(Record):
    __tablename__ = "support_conversations"
    __table_args__ = (Index("ix_support_conversations_user_created", "user_id", "created_at"),)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    shop_id: Mapped[UUID | None] = mapped_column(ForeignKey("shops.id"), index=True)
    title: Mapped[str] = mapped_column(String(80))


class SupportRun(Record):
    __tablename__ = "support_runs"
    __table_args__ = (
        UniqueConstraint("user_id", "request_id"),
        Index("ix_support_runs_conversation_created", "conversation_id", "created_at"),
        Index("ix_support_runs_user_status", "user_id", "status", "expires_at"),
        CheckConstraint("tool_calls >= 0 AND tool_calls <= 12", name="tool_budget"),
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    shop_id: Mapped[UUID | None] = mapped_column(ForeignKey("shops.id"), index=True)
    session_id: Mapped[UUID] = mapped_column(ForeignKey("sessions.id"))
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey("support_conversations.id"))
    request_id: Mapped[UUID]
    token_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    memory_generation: Mapped[int]
    status: Mapped[str] = mapped_column(String(16), default="running")
    user_text: Mapped[str] = mapped_column(String(2000))
    assistant_text: Mapped[str] = mapped_column(String(12000), default="")
    error_code: Mapped[str | None] = mapped_column(String(60))
    tool_calls: Mapped[int] = mapped_column(default=0)
    links: Mapped[list[dict[str, str]]] = mapped_column(JSONB, default=list)
    feedback: Mapped[str | None] = mapped_column(String(16))
    runtime: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict)


class CustomerMemory(Record):
    __tablename__ = "customer_memories"
    __table_args__ = (
        UniqueConstraint("user_id", "kind", "value"),
        Index("ix_customer_memories_user_status", "user_id", "status"),
        CheckConstraint("status IN ('candidate','confirmed','dismissed')", name="status"),
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(20))
    value: Mapped[str] = mapped_column(String(120))
    evidence: Mapped[str] = mapped_column(String(240))
    source: Mapped[str] = mapped_column(String(20))
    source_run_id: Mapped[UUID | None] = mapped_column(ForeignKey("support_runs.id"))
    status: Mapped[str] = mapped_column(String(16), default="candidate")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SupportUsage(Record):
    __tablename__ = "support_usage"
    __table_args__ = (UniqueConstraint("user_id", "feature"),)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    feature: Mapped[str] = mapped_column(String(32))
    uses: Mapped[int] = mapped_column(default=0)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
