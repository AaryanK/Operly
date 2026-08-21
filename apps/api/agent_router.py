import json
import tempfile
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext, get_auth_context, get_db
from packages.business_brain import AgentInput, get_agent_service
from packages.business_brain.attachments import AttachmentBundle, AttachmentInput, MultimodalProcessor
from packages.business_brain.attachments.multimodal_processor import attachment_hashes
from packages.business_brain.attachments.privacy import redacted_name
from packages.business_brain.ollama_client import OllamaError
from packages.business_brain.security import AgentSecurityError
from packages.capabilities.time_context import user_time_context
from packages.database.agent_models import AgentConversation, AgentMessage, AttachmentAudit
from packages.database.db import session_scope
from packages.model_runtime.semantic_router import SemanticRoutingError

router = APIRouter(prefix="/api/agent", tags=["agent"])
attachment_processor = MultimodalProcessor()


class ChatInput(BaseModel):
    message: str = Field(min_length=1, max_length=12_000)
    conversation_id: str | None = Field(default=None, max_length=120)
    application_id: str | None = Field(default=None, max_length=120)
    user_timezone: str | None = Field(default=None, max_length=100)


def _time_metadata(requested_timezone: str | None) -> dict:
    context = user_time_context(requested_timezone)
    return {
        **context,
        "dashboard_context": {
            "user_time": context,
        },
    }


async def _run_agent(auth: AuthContext, request: AgentInput):
    try:
        return await get_agent_service().run(request)
    except AgentSecurityError as error:
        raise HTTPException(status_code=429, detail=str(error)) from error
    except SemanticRoutingError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    except OllamaError as error:
        raise HTTPException(status_code=503, detail=error.public_message) from error


@router.post("/chat")
async def chat(
    payload: ChatInput,
    auth: AuthContext = Depends(get_auth_context),
):
    return await _run_agent(
        auth,
        AgentInput(
            tenant_id=auth.tenant.id,
            principal_id=f"web-user:{auth.user.id}",
            actor_name=auth.user.display_name,
            channel="web",
            conversation_id=payload.conversation_id,
            text=payload.message,
            metadata={
                "application_id": payload.application_id,
                "user_id": auth.user.id,
                "role": auth.role,
                "allow_tenant_context": True,
                **_time_metadata(payload.user_timezone),
            },
        ),
    )


@router.post("/chat-with-attachments")
async def chat_with_attachments(
    message: str = Form(default="", max_length=8_000),
    conversation_id: str | None = Form(default=None, max_length=120),
    application_id: str | None = Form(default=None, max_length=120),
    user_timezone: str | None = Form(default=None, max_length=100),
    files: list[UploadFile] = File(default=[]),
    auth: AuthContext = Depends(get_auth_context),
):
    limits = attachment_processor.limits
    if not files:
        raise HTTPException(422, "Attach at least one supported file")
    if len(files) > limits.max_attachments:
        raise HTTPException(413, f"Maximum {limits.max_attachments} attachments")

    inputs: list[AttachmentInput] = []
    total = 0
    for index, upload in enumerate(files, 1):
        raw = await upload.read(limits.max_attachment_bytes + 1)
        await upload.close()
        if len(raw) > limits.max_attachment_bytes:
            raise HTTPException(413, f"{upload.filename or 'Attachment'} is too large")
        total += len(raw)
        if total > limits.max_total_bytes:
            raise HTTPException(413, "Total attachment size limit exceeded")
        inputs.append(
            AttachmentInput(
                index=index,
                filename=upload.filename or f"attachment-{index}",
                declared_content_type=upload.content_type,
                size_bytes=len(raw),
                content_bytes=raw,
            )
        )

    audit_id = str(uuid4())
    bundle = AttachmentBundle(
        user_request=message.strip() or "Analyze the supplied attachment(s).",
        attachments=inputs,
        requested_output_format="message",
        tenant_id=auth.tenant.id,
        actor_id=auth.user.id,
    )
    try:
        with tempfile.TemporaryDirectory(prefix="operly-web-") as temp_dir:
            processed = await attachment_processor.process(bundle, temp_dir)
    except (ValueError, RuntimeError) as error:
        raise HTTPException(422, str(error)) from error

    async with session_scope() as db:
        db.add(
            AttachmentAudit(
                tenant_id=auth.tenant.id,
                actor_id=auth.user.id,
                guild_id=None,
                channel_id="web",
                message_id=audit_id,
                attachment_count=len(inputs),
                filenames_json=json.dumps([redacted_name(item.filename) for item in inputs]),
                hashes_json=json.dumps(attachment_hashes(bundle)),
                categories_json=json.dumps(
                    [item.detected_content_type or "rejected" for item in inputs]
                ),
                operation=processed.operation_summary or "analyze",
                success=bool(processed.accepted),
                generated_output_count=0,
                error_category=None,
            )
        )

    if not processed.accepted:
        raise HTTPException(
            422,
            {
                "message": "No supported attachments could be processed",
                "skipped": processed.skipped,
            },
        )

    result = await _run_agent(
        auth,
        AgentInput(
            tenant_id=auth.tenant.id,
            principal_id=f"web-user:{auth.user.id}",
            actor_name=auth.user.display_name,
            channel="web",
            conversation_id=conversation_id,
            text=message.strip(),
            attachment_context=processed.message,
            attachment_names=list(processed.accepted),
            metadata={
                "application_id": application_id,
                "user_id": auth.user.id,
                "role": auth.role,
                "allow_tenant_context": True,
                "attachment_audit_id": audit_id,
                **_time_metadata(user_timezone),
            },
        ),
    )
    result["attachments"] = {
        "accepted": processed.accepted,
        "skipped": processed.skipped,
        "warnings": processed.warnings,
    }
    return result


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
