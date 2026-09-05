from __future__ import annotations

import json
from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import (
    AccountAuthContext,
    AuthContext,
    get_account_auth_context,
    get_auth_context,
    get_db,
)
from packages.agent_runtime.context import ContextItem, ContextKind
from packages.agent_runtime.inference import AgentInferenceError, OpenAICompatibleAgentModel
from packages.agent_runtime.interactive import Runtime1Agent
from packages.agent_runtime.runtime import AgentRuntimeDisabled
from packages.agent_runtime.telemetry import runtime_trace
from packages.artifacts.service import ArtifactScope, ArtifactService, artifact_json
from packages.database.agent_chat_models import AgentChatConversation, AgentChatMessage
from packages.personal_modules.runtime import build_personal_runtime
from packages.security.execution_context import (
    ExecutionContext,
    resolve_execution_context,
    resolve_personal_execution_context,
)
from packages.security.surfaces import SurfaceKind
from packages.workspace_modules.tools.runtime import build_workspace_runtime


workspace_router = APIRouter(prefix="/api/agent", tags=["agent-runtime-1"])
personal_router = APIRouter(prefix="/api/personal-agent", tags=["personal-agent-runtime-1"])


class WorkspaceChatInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=12_000)
    conversation_id: str | None = Field(default=None, max_length=120)
    application_id: str | None = Field(default=None, max_length=120)


class PersonalChatInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=12_000)
    conversation_id: str | None = Field(default=None, max_length=120)
    selected_workspace_id: str | None = Field(default=None, max_length=36)


def _agent() -> Runtime1Agent:
    return Runtime1Agent(model=OpenAICompatibleAgentModel())


def _title(message: str) -> str:
    clean = " ".join(str(message or "").split()).strip()
    return clean[:80] or "Operly conversation"


async def _conversation(
    db: AsyncSession,
    *,
    context: ExecutionContext,
    requested_id: str | None,
    first_message: str,
) -> AgentChatConversation:
    if requested_id:
        row = await db.get(AgentChatConversation, requested_id)
        if row is None:
            raise HTTPException(404, "Conversation not found")
        if row.scope_kind != context.scope_kind.value or row.principal_id != context.principal_id:
            raise HTTPException(404, "Conversation not found")
        if context.is_personal:
            if row.owner_user_id != context.user_id or row.workspace_id is not None:
                raise HTTPException(404, "Conversation not found")
        elif row.workspace_id != context.workspace_id or row.owner_user_id is not None:
            raise HTTPException(404, "Conversation not found")
        return row

    row = AgentChatConversation(
        id=str(uuid4()),
        scope_kind=context.scope_kind.value,
        workspace_id=context.workspace_id if context.is_workspace else None,
        owner_user_id=context.user_id if context.is_personal else None,
        authority_user_id=context.user_id,
        principal_id=str(context.principal_id or ""),
        channel=context.channel,
        surface=context.surface.value,
        title=_title(first_message),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(row)
    await db.flush()
    runtime_trace(
        "conversation.created",
        conversation_id=row.id,
        scope_kind=context.scope_kind.value,
        surface=context.surface.value,
    )
    return row


async def _history_context(
    db: AsyncSession,
    *,
    conversation_id: str,
    exclude_message_id: str | None = None,
) -> list[ContextItem]:
    rows = (
        await db.scalars(
            select(AgentChatMessage)
            .where(AgentChatMessage.conversation_id == conversation_id)
            .order_by(desc(AgentChatMessage.created_at))
            .limit(18)
        )
    ).all()
    ordered = list(reversed([row for row in rows if row.id != exclude_message_id]))
    items: list[ContextItem] = []
    total = len(ordered)
    for index, row in enumerate(ordered):
        recency = total - index
        priority = 20 if recency <= 2 else 0
        items.append(
            ContextItem(
                key=f"chat:{row.id}",
                kind=ContextKind.CONVERSATION,
                text=f"{row.role}: {row.content}",
                relevance=0.0,
                priority=priority,
            )
        )
    return items


def _artifact_context(items: list[dict]) -> ContextItem | None:
    if not items:
        return None
    handles = [
        {
            "artifact_id": item["artifact_id"],
            "filename": item["filename"],
            "content_type": item.get("content_type"),
            "size_bytes": item.get("size_bytes"),
        }
        for item in items
    ]
    return ContextItem(
        key=f"artifact-upload:{uuid4()}",
        kind=ContextKind.ARTIFACT,
        text=(
            "Trusted Operly Artifact Store handles for the user's uploads: "
            + json.dumps(handles, ensure_ascii=False, separators=(",", ":"))
            + ". Use a file capability only if understanding or transforming the files is necessary."
        ),
        relevance=1.0,
        priority=100,
    )


async def _run(
    db: AsyncSession,
    *,
    context: ExecutionContext,
    message: str,
    conversation: AgentChatConversation,
    extra_context: list[ContextItem] | None = None,
):
    user_row = AgentChatMessage(
        conversation_id=conversation.id,
        role="user",
        content=message,
        created_at=datetime.utcnow(),
    )
    db.add(user_row)
    conversation.updated_at = datetime.utcnow()
    await db.flush()

    context_items = await _history_context(
        db,
        conversation_id=conversation.id,
        exclude_message_id=user_row.id,
    )
    if extra_context:
        context_items.extend(extra_context)

    if context.is_personal:
        kernel = build_personal_runtime()
    else:
        facade = build_workspace_runtime()
        kernel = await facade.request_runtime(db, context=context)

    run_id = f"chat-{uuid4()}"
    try:
        result = await _agent().run(
            db,
            context=context,
            message=message,
            kernel=kernel,
            context_items=context_items,
            run_id=run_id,
        )
    except AgentRuntimeDisabled as error:
        runtime_trace("request.failed", run_id=run_id, error_code="agent_runtime_disabled")
        await db.rollback()
        raise HTTPException(
            503,
            detail={
                "code": "AGENT_RUNTIME_DISABLED",
                "message": "Operly Agent Runtime 1.0 is not enabled for this deployment.",
            },
        ) from error
    except AgentInferenceError as error:
        runtime_trace(
            "request.failed",
            run_id=run_id,
            error_code=error.code,
            retryable=error.retryable,
        )
        await db.rollback()
        raise HTTPException(
            503 if error.retryable or error.code == "inference_not_configured" else 502,
            detail={"code": error.code, "message": str(error), "retryable": error.retryable},
        ) from error

    db.add(
        AgentChatMessage(
            conversation_id=conversation.id,
            role="assistant",
            content=result.message,
            created_at=datetime.utcnow(),
        )
    )
    conversation.updated_at = datetime.utcnow()
    await db.commit()
    runtime_trace(
        "conversation.turn_committed",
        run_id=run_id,
        conversation_id=conversation.id,
        capability_calls=len(result.capability_calls),
        dispatch=result.dispatch,
    )
    payload = result.as_dict()
    payload["conversation_id"] = conversation.id
    payload["artifacts"] = []
    return payload


async def _save_uploads(
    db: AsyncSession,
    *,
    scope: ArtifactScope,
    files: list[UploadFile],
    source: str,
    created_by: str,
    conversation_id: str,
) -> list[dict]:
    if not files:
        raise HTTPException(422, "Attach at least one supported file")
    if len(files) > 10:
        raise HTTPException(413, "Maximum 10 attachments")
    max_each = 12 * 1024 * 1024
    max_total = 32 * 1024 * 1024
    total = 0
    service = ArtifactService(db)
    stored: list[dict] = []
    for index, upload in enumerate(files, 1):
        raw = await upload.read(max_each + 1)
        await upload.close()
        if len(raw) > max_each:
            raise HTTPException(413, f"{upload.filename or 'Attachment'} is too large")
        total += len(raw)
        if total > max_total:
            raise HTTPException(413, "Total attachment size limit exceeded")
        row = await service.create_bytes(
            scope,
            filename=upload.filename or f"attachment-{index}",
            content_type=upload.content_type,
            content=raw,
            source=source,
            created_by=created_by,
            metadata={"conversation_id": conversation_id, "ingress": "agent_runtime_1"},
        )
        stored.append(artifact_json(row))
    await db.flush()
    runtime_trace(
        "attachments.stored",
        conversation_id=conversation_id,
        count=len(stored),
        total_bytes=total,
        content_types=[item.get("content_type") for item in stored],
    )
    return stored


@workspace_router.post("/chat")
async def workspace_chat(
    payload: WorkspaceChatInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    context = await resolve_execution_context(
        db,
        workspace_id=auth.tenant.id,
        user_id=auth.user.id,
        channel="web",
        surface=SurfaceKind.WORKSPACE_PRIVATE,
        conversation_id=payload.conversation_id,
        require_membership=True,
    )
    conversation = await _conversation(
        db,
        context=context,
        requested_id=payload.conversation_id,
        first_message=payload.message,
    )
    context = await resolve_execution_context(
        db,
        workspace_id=auth.tenant.id,
        user_id=auth.user.id,
        channel="web",
        surface=SurfaceKind.WORKSPACE_PRIVATE,
        conversation_id=conversation.id,
        require_membership=True,
    )
    return await _run(db, context=context, message=payload.message, conversation=conversation)


@personal_router.post("/chat")
async def personal_chat(
    payload: PersonalChatInput,
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    context = await resolve_personal_execution_context(
        db,
        user_id=auth.user.id,
        channel="web",
        surface=SurfaceKind.PERSONAL_PRIVATE,
        conversation_id=payload.conversation_id,
        focus_workspace_id=payload.selected_workspace_id,
    )
    conversation = await _conversation(
        db,
        context=context,
        requested_id=payload.conversation_id,
        first_message=payload.message,
    )
    context = await resolve_personal_execution_context(
        db,
        user_id=auth.user.id,
        channel="web",
        surface=SurfaceKind.PERSONAL_PRIVATE,
        conversation_id=conversation.id,
        focus_workspace_id=payload.selected_workspace_id,
    )
    return await _run(db, context=context, message=payload.message, conversation=conversation)


@workspace_router.post("/chat-with-attachments")
async def workspace_chat_with_attachments(
    message: str = Form(default="", max_length=12_000),
    conversation_id: str | None = Form(default=None, max_length=120),
    application_id: str | None = Form(default=None, max_length=120),
    files: list[UploadFile] = File(default=[]),
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    del application_id
    text = message.strip() or "Work with the uploaded attachment(s)."
    context = await resolve_execution_context(
        db,
        workspace_id=auth.tenant.id,
        user_id=auth.user.id,
        channel="web",
        surface=SurfaceKind.WORKSPACE_PRIVATE,
        conversation_id=conversation_id,
        require_membership=True,
    )
    conversation = await _conversation(
        db,
        context=context,
        requested_id=conversation_id,
        first_message=text,
    )
    uploads = await _save_uploads(
        db,
        scope=ArtifactScope("workspace", auth.tenant.id, tenant_id=auth.tenant.id),
        files=files,
        source="workspace_agent_runtime_upload",
        created_by=auth.user.id,
        conversation_id=conversation.id,
    )
    context = await resolve_execution_context(
        db,
        workspace_id=auth.tenant.id,
        user_id=auth.user.id,
        channel="web",
        surface=SurfaceKind.WORKSPACE_PRIVATE,
        conversation_id=conversation.id,
        require_membership=True,
    )
    extra = [item for item in [_artifact_context(uploads)] if item is not None]
    result = await _run(db, context=context, message=text, conversation=conversation, extra_context=extra)
    result["attachments"] = {"accepted": [item["filename"] for item in uploads], "artifacts": uploads}
    return result


@personal_router.post("/chat-with-attachments")
async def personal_chat_with_attachments(
    message: str = Form(default="", max_length=12_000),
    conversation_id: str | None = Form(default=None, max_length=120),
    files: list[UploadFile] = File(default=[]),
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    text = message.strip() or "Work with the uploaded attachment(s)."
    context = await resolve_personal_execution_context(
        db,
        user_id=auth.user.id,
        channel="web",
        surface=SurfaceKind.PERSONAL_PRIVATE,
        conversation_id=conversation_id,
    )
    conversation = await _conversation(
        db,
        context=context,
        requested_id=conversation_id,
        first_message=text,
    )
    uploads = await _save_uploads(
        db,
        scope=ArtifactScope("personal", f"personal:{auth.user.id}", owner_user_id=auth.user.id),
        files=files,
        source="personal_agent_runtime_upload",
        created_by=auth.user.id,
        conversation_id=conversation.id,
    )
    context = await resolve_personal_execution_context(
        db,
        user_id=auth.user.id,
        channel="web",
        surface=SurfaceKind.PERSONAL_PRIVATE,
        conversation_id=conversation.id,
    )
    extra = [item for item in [_artifact_context(uploads)] if item is not None]
    result = await _run(db, context=context, message=text, conversation=conversation, extra_context=extra)
    result["attachment_processing"] = {
        "accepted": [item["filename"] for item in uploads],
        "artifacts": uploads,
        "artifact_ids": [item["artifact_id"] for item in uploads],
    }
    return result


async def _list_conversations(db: AsyncSession, *, context: ExecutionContext):
    query = select(AgentChatConversation).where(
        AgentChatConversation.scope_kind == context.scope_kind.value,
        AgentChatConversation.principal_id == context.principal_id,
    )
    if context.is_personal:
        query = query.where(
            AgentChatConversation.owner_user_id == context.user_id,
            AgentChatConversation.workspace_id.is_(None),
        )
    else:
        query = query.where(
            AgentChatConversation.workspace_id == context.workspace_id,
            AgentChatConversation.owner_user_id.is_(None),
        )
    rows = (await db.scalars(query.order_by(desc(AgentChatConversation.updated_at)).limit(50))).all()
    return [
        {"id": row.id, "title": row.title, "updated_at": row.updated_at.isoformat()}
        for row in rows
    ]


async def _messages(db: AsyncSession, *, context: ExecutionContext, conversation_id: str):
    conversation = await _conversation(
        db,
        context=context,
        requested_id=conversation_id,
        first_message="",
    )
    rows = (
        await db.scalars(
            select(AgentChatMessage)
            .where(AgentChatMessage.conversation_id == conversation.id)
            .order_by(AgentChatMessage.created_at)
            .limit(300)
        )
    ).all()
    return [
        {
            "id": row.id,
            "role": row.role,
            "content": row.content,
            "created_at": row.created_at.isoformat(),
            "artifacts": [],
        }
        for row in rows
    ]


@workspace_router.get("/conversations")
async def workspace_conversations(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    context = await resolve_execution_context(
        db,
        workspace_id=auth.tenant.id,
        user_id=auth.user.id,
        channel="web",
        surface=SurfaceKind.WORKSPACE_PRIVATE,
        require_membership=True,
    )
    return await _list_conversations(db, context=context)


@workspace_router.get("/conversations/{conversation_id}/messages")
async def workspace_messages(
    conversation_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    context = await resolve_execution_context(
        db,
        workspace_id=auth.tenant.id,
        user_id=auth.user.id,
        channel="web",
        surface=SurfaceKind.WORKSPACE_PRIVATE,
        conversation_id=conversation_id,
        require_membership=True,
    )
    return await _messages(db, context=context, conversation_id=conversation_id)


@personal_router.get("/conversations")
async def personal_conversations(
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    context = await resolve_personal_execution_context(
        db,
        user_id=auth.user.id,
        channel="web",
        surface=SurfaceKind.PERSONAL_PRIVATE,
    )
    return await _list_conversations(db, context=context)


@personal_router.get("/conversations/{conversation_id}/messages")
async def personal_messages(
    conversation_id: str,
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    context = await resolve_personal_execution_context(
        db,
        user_id=auth.user.id,
        channel="web",
        surface=SurfaceKind.PERSONAL_PRIVATE,
        conversation_id=conversation_id,
    )
    return await _messages(db, context=context, conversation_id=conversation_id)
