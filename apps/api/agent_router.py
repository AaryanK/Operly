from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext, get_auth_context, get_db
from packages.artifacts.delivery import artifacts_by_assistant_message, project_agent_result
from packages.artifacts.service import ArtifactScope, ArtifactService, artifact_json
from packages.business_brain import AgentInput, get_agent_service
from packages.business_brain.attachments import MultimodalProcessor
from packages.business_brain.ollama_client import OllamaError
from packages.business_brain.security import AgentSecurityError
from packages.database.agent_models import AgentConversation, AgentMessage
from packages.database.db import session_scope
from packages.model_runtime import ModelInferenceError
from packages.model_runtime.semantic_router import SemanticRoutingError

router = APIRouter(prefix="/api/agent", tags=["agent"])
# Kept only as the canonical attachment size/count policy. Uploads are no longer
# eagerly analyzed here; the agent chooses files.convert or files.process later.
attachment_limits = MultimodalProcessor().limits


class ChatInput(BaseModel):
    message: str = Field(min_length=1, max_length=12_000)
    conversation_id: str | None = Field(default=None, max_length=120)
    application_id: str | None = Field(default=None, max_length=120)


def _workspace_artifact_scope(auth: AuthContext) -> ArtifactScope:
    return ArtifactScope("workspace", auth.tenant.id, tenant_id=auth.tenant.id)


async def _project_result(auth: AuthContext, result: dict) -> dict:
    async with session_scope() as db:
        return await project_agent_result(db, _workspace_artifact_scope(auth), result)


async def _run_agent(auth: AuthContext, request: AgentInput):
    try:
        return await get_agent_service().run(request)
    except AgentSecurityError as error:
        raise HTTPException(status_code=429, detail=str(error)) from error
    except SemanticRoutingError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    except ModelInferenceError as error:
        raise HTTPException(
            status_code=503 if error.retryable else 502,
            detail={
                "error": "model_inference_failed",
                "classification": error.classification,
                "retryable": bool(error.retryable),
            },
        ) from error
    except OllamaError as error:
        raise HTTPException(status_code=503, detail=error.public_message) from error


@router.post("/chat")
async def chat(
    payload: ChatInput,
    auth: AuthContext = Depends(get_auth_context),
):
    result = await _run_agent(
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
                # Workspace time is application-owned context. Relative dates such
                # as today/tomorrow must not silently fall back to UTC.
                "timezone": str(auth.tenant.timezone or "UTC"),
            },
        ),
    )
    return await _project_result(auth, result)


@router.post("/chat-with-attachments")
async def chat_with_attachments(
    message: str = Form(default="", max_length=8_000),
    conversation_id: str | None = Form(default=None, max_length=120),
    application_id: str | None = Form(default=None, max_length=120),
    files: list[UploadFile] = File(default=[]),
    auth: AuthContext = Depends(get_auth_context),
):
    limits = attachment_limits
    if not files:
        raise HTTPException(422, "Attach at least one supported file")
    if len(files) > limits.max_attachments:
        raise HTTPException(413, f"Maximum {limits.max_attachments} attachments")

    effective_conversation_id = conversation_id or str(uuid4())
    uploaded_artifacts: list[dict] = []
    total = 0
    async with session_scope() as db:
        service = ArtifactService(db)
        scope = _workspace_artifact_scope(auth)
        for index, upload in enumerate(files, 1):
            raw = await upload.read(limits.max_attachment_bytes + 1)
            await upload.close()
            if len(raw) > limits.max_attachment_bytes:
                raise HTTPException(413, f"{upload.filename or 'Attachment'} is too large")
            total += len(raw)
            if total > limits.max_total_bytes:
                raise HTTPException(413, "Total attachment size limit exceeded")
            row = await service.create_bytes(
                scope,
                filename=upload.filename or f"attachment-{index}",
                content_type=upload.content_type,
                content=raw,
                source="web_chat_upload",
                created_by=auth.user.id,
                metadata={
                    "ingress": "web_chat_v2",
                    "conversation_id": effective_conversation_id,
                },
            )
            uploaded_artifacts.append(artifact_json(row))

    # Artifact IDs are application-assigned trusted handles. The file bytes themselves
    # remain untrusted. Crucially, no perception model runs at ingress: a format-only
    # request can discover files.convert, while a content question can discover
    # files.process. This avoids forcing vision inference for image->PDF conversion.
    trusted_upload_context = {
        "uploaded_artifacts": [
            {
                "artifact_id": item["artifact_id"],
                "filename": item["filename"],
                "content_type": item["content_type"],
                "size_bytes": item["size_bytes"],
            }
            for item in uploaded_artifacts
        ],
        "attachment_contract": (
            "These are application-assigned Artifact Store handles. Use files.convert for format-only conversion; "
            "use files.process only when the contents must be understood."
        ),
    }
    result = await _run_agent(
        auth,
        AgentInput(
            tenant_id=auth.tenant.id,
            principal_id=f"web-user:{auth.user.id}",
            actor_name=auth.user.display_name,
            channel="web",
            conversation_id=effective_conversation_id,
            text=message.strip() or "Work with the uploaded attachment(s).",
            attachment_names=[item["filename"] for item in uploaded_artifacts],
            metadata={
                "application_id": application_id,
                "user_id": auth.user.id,
                "role": auth.role,
                "allow_tenant_context": True,
                "timezone": str(auth.tenant.timezone or "UTC"),
                "attachment_artifact_ids": [item["artifact_id"] for item in uploaded_artifacts],
                "dashboard_context": trusted_upload_context,
            },
        ),
    )
    result = await _project_result(auth, result)
    result["attachments"] = {
        "accepted": [item["filename"] for item in uploaded_artifacts],
        "skipped": [],
        "warnings": [],
        "artifacts": uploaded_artifacts,
        "artifact_ids": [item["artifact_id"] for item in uploaded_artifacts],
        "ingress": "artifact_store_v2",
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
        {"id": row.id, "title": row.title, "updated_at": row.updated_at.isoformat()}
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
    artifacts_by_message = await artifacts_by_assistant_message(
        db,
        _workspace_artifact_scope(auth),
        conversation_id=conversation_id,
        messages=rows,
    )
    return [
        {
            "id": row.id,
            "role": row.role,
            "content": row.content,
            "created_at": row.created_at.isoformat(),
            "artifacts": artifacts_by_message.get(row.id, []),
        }
        for row in rows
    ]
