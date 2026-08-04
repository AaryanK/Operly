from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from packages.database.db import Base


def uid() -> str:
    return str(uuid4())


class AgentConversation(Base):
    __tablename__ = "agent_conversations"

    id: Mapped[str] = mapped_column(String(120), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    principal_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(250), default="OPERLY conversation")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    __table_args__ = (
        Index(
            "ix_agent_conversation_scope",
            "tenant_id",
            "principal_id",
            "channel",
        ),
    )


class AgentMessage(Base):
    __tablename__ = "agent_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("agent_conversations.id"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text, default="")
    tool_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index(
            "ix_agent_message_scope_created",
            "tenant_id",
            "conversation_id",
            "created_at",
        ),
    )


class AgentToolAudit(Base):
    __tablename__ = "agent_tool_audits"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    principal_id: Mapped[str] = mapped_column(String(200), nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(120), nullable=False)
    channel: Mapped[str] = mapped_column(String(40), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    risk: Mapped[str] = mapped_column(String(20), default="low")
    arguments_json: Mapped[str] = mapped_column(Text, default="{}")
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AttachmentAudit(Base):
    __tablename__ = "attachment_audits"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    actor_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    guild_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    channel_id: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    message_id: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    attachment_count: Mapped[int] = mapped_column(default=0)
    filenames_json: Mapped[str] = mapped_column(Text, default="[]")
    hashes_json: Mapped[str] = mapped_column(Text, default="[]")
    categories_json: Mapped[str] = mapped_column(Text, default="[]")
    operation: Mapped[str] = mapped_column(String(60), default="summarize")
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    generated_output_count: Mapped[int] = mapped_column(default=0)
    error_category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (Index("ix_attachment_audit_scope", "tenant_id", "actor_id", "created_at"),)
