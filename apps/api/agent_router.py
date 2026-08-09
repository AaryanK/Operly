from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext, get_auth_context, get_db
from packages.business_brain import AgentInput, get_agent_service
from packages.business_brain.ollama_client import OllamaError
from packages.business_brain.security import AgentSecurityError
from packages.database.agent_models import AgentConversation, AgentMessage
from packages.model_runtime.semantic_router import SemanticRoutingError

router = APIRouter(prefix="/api/agent", tags=["agent"])


class ChatInput(BaseModel):
    message: str = Field(min_length=1, max_length=12_000)
    conversation_id: str | None = Field(default=None, max_length=120)
    application_id: str | None = Field(default=None, max_length=120)


@router.post("/chat")
async def chat(
    payload: ChatInput,
    auth: AuthContext = Depends(get_auth_context),
):
    service = get_agent_service()

    try:
        return await service.run(
            AgentInput(
                tenant_id=auth.tenant.id,
                principal_id=f"web-user:{auth.user.id}",
                actor_name=auth.user.display_name,
                channel="web",
                conversation_id=payload.conversation_id,
                text=payload.message,
                metadata={"application_id":payload.application_id,"user_id":auth.user.id,"role":auth.role},
            )
        )
    except AgentSecurityError as error:
        raise HTTPException(status_code=429, detail=str(error)) from error
    except SemanticRoutingError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    except OllamaError as error:
        raise HTTPException(
            status_code=503,
            detail=error.public_message,
        ) from error


@router.get("/conversations")
async def conversations(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    principal_id = f"web-user:{auth.user.id}"

    rows = (
        await db.scalars(
            select(AgentConversation)
            .where(
                AgentConversation.tenant_id == auth.tenant.id,
                AgentConversation.principal_id == principal_id,
                AgentConversation.channel == "web",
            )
            .order_by(desc(AgentConversation.updated_at))
            .limit(30)
        )
    ).all()

    return [
        {
            "id": row.id,
            "title": row.title,
            "updated_at": row.updated_at.isoformat(),
        }
        for row in rows
    ]


@router.get("/conversations/{conversation_id}/messages")
async def conversation_messages(
    conversation_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    principal_id = f"web-user:{auth.user.id}"

    conversation = await db.scalar(
        select(AgentConversation).where(
            AgentConversation.id == conversation_id,
            AgentConversation.tenant_id == auth.tenant.id,
            AgentConversation.principal_id == principal_id,
            AgentConversation.channel == "web",
        )
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    rows = (
        await db.scalars(
            select(AgentMessage)
            .where(
                AgentMessage.tenant_id == auth.tenant.id,
                AgentMessage.conversation_id == conversation_id,
                AgentMessage.role.in_(["user", "assistant"]),
            )
            .order_by(AgentMessage.created_at)
            .limit(200)
        )
    ).all()

    return [
        {
            "id": row.id,
            "role": row.role,
            "content": row.content,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]
