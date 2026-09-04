from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from packages.database.db import Base


def _uid() -> str:
    return str(uuid4())


class AgentChatConversation(Base):
    __tablename__ = "agent_chat_conversations"

    id: Mapped[str] = mapped_column(String(120), primary_key=True, default=_uid)
    scope_kind: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True
    )
    owner_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("app_users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    authority_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    principal_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    surface: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(250), nullable=False, default="Operly conversation")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "(scope_kind = 'workspace' AND workspace_id IS NOT NULL AND owner_user_id IS NULL) OR "
            "(scope_kind = 'personal' AND workspace_id IS NULL AND owner_user_id IS NOT NULL)",
            name="ck_agent_chat_conversation_scope_owner",
        ),
        Index(
            "ix_agent_chat_conversation_scope_principal_updated",
            "scope_kind",
            "workspace_id",
            "owner_user_id",
            "principal_id",
            "updated_at",
        ),
    )


class AgentChatMessage(Base):
    __tablename__ = "agent_chat_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("agent_chat_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index(
            "ix_agent_chat_message_conversation_created",
            "conversation_id",
            "created_at",
        ),
    )
