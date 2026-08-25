from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AccountAuthContext, get_account_auth_context, get_db
from packages.artifacts.delivery import artifacts_by_assistant_message, project_agent_result
from packages.artifacts.service import ArtifactScope, ArtifactService, artifact_json
from packages.assets.service import (
    remove_workspace_icon,
    store_workspace_icon,
    workspace_icon_path,
)
from packages.business_brain.attachments import MultimodalProcessor
from packages.business_brain.personal_agent import get_personal_agent_service
from packages.database.db import session_scope
from packages.database.models import AppUser, Tenant, TenantMember
from packages.security.permissions import resolve_workspace_permissions


router = APIRouter(prefix="/api/personal-agent", tags=["personal-agent"])
# Size/count policy only. Personal web attachment ingress is artifact-first and does
# not invoke a perception model before the Personal agent chooses an operation.
attachment_limits = MultimodalProcessor().limits


class PersonalChatInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=12_000)
    conversation_id: str | None = Field(default=None, max_length=255)
    selected_workspace_id: str | None = Field(default=None, max_length=36)


class AccountProfilePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str = Field(min_length=1, max_length=200)


class WorkspacePresentationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=200)
    timezone: str | None = Field(default=None, min_length=1, max_length=100)
    # Kept temporarily for compatibility with the current shell form. New image
    # values are never accepted here; workspace icons must go through the binary
    # upload endpoint so Operly can validate and store them first-party.
    logo_url: str | None = Field(default=None, max_length=1000)


def _clean(value: str, limit: int) -> str:
    return " ".join(str(value or "").replace("\x00", "").split()).strip()[:limit]


def _personal_artifact_scope(auth: AccountAuthContext) -> ArtifactScope:
    return ArtifactScope("personal", f"personal:{auth.user.id}", owner_user_id=auth.user.id)


async def _project_personal_result(auth: AccountAuthContext, result: dict) -> dict:
    async with session_scope() as db:
        return await project_agent_result(db, _personal_artifact_scope(auth), result)


def _workspace_icon_url(workspace_id: str, key: str) -> str:
    return f"/api/personal-agent/workspaces/{workspace_id}/icon/{key}"


def _workspace_icon_key(workspace_id: str, value: str | None) -> str | None:
    raw = str(value or "").strip()
    prefix = f"/api/personal-agent/workspaces/{workspace_id}/icon/"
    if not raw.startswith(prefix):
        return None
    key = raw[len(prefix):]
    return key if "/" not in key and "?" not in key and "#" not in key else None


async def _workspace_membership(
    db: AsyncSession,
    *,
    user_id: str,
    workspace_id: str,
) -> tuple[TenantMember, Tenant]:
    row = (
        await db.execute(
            select(TenantMember, Tenant)
            .join(Tenant, Tenant.id == TenantMember.tenant_id)
            .where(
                TenantMember.user_id == user_id,
                TenantMember.tenant_id == workspace_id,
            )
        )
    ).first()
    if row is None:
        raise HTTPException(404, "Workspace not found")
    return row


async def _require_workspace_settings(
    db: AsyncSession,
    *,
    user_id: str,
    workspace_id: str,
) -> tuple[TenantMember, Tenant]:
    member, tenant = await _workspace_membership(
        db,
        user_id=user_id,
        workspace_id=workspace_id,
    )
    permissions = await resolve_workspace_permissions(db, tenant_id=tenant.id, role=member.role)
    if member.role != "owner" and "workspace:settings:manage" not in permissions:
        raise HTTPException(403, "Workspace settings permission denied")
    return member, tenant


@router.get("/me")
async def personal_me(auth: AccountAuthContext = Depends(get_account_auth_context)):
    return {
        "id": auth.user.id,
        "email": auth.user.email,
        "display_name": auth.user.display_name,
        "scope": "workspace" if auth.session.tenant_id else "personal",
        "current_workspace_id": auth.session.tenant_id,
    }


@router.patch("/me")
async def update_personal_me(
    payload: AccountProfilePatch,
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    user = await db.get(AppUser, auth.user.id)
    if not user or not user.active:
        raise HTTPException(401, "Account unavailable")
    name = _clean(payload.display_name, 200)
    if not name:
        raise HTTPException(422, "Display name is required")
    user.display_name = name
    await db.commit()
    return {"id": user.id, "email": user.email, "display_name": user.display_name}


@router.get("/workspaces")
async def personal_workspaces(
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            select(Tenant, TenantMember.role)
            .join(TenantMember, TenantMember.tenant_id == Tenant.id)
            .where(TenantMember.user_id == auth.user.id)
            .order_by(Tenant.name)
        )
    ).all()
    return [
        {
            "id": tenant.id,
            "name": tenant.name,
            "slug": tenant.slug,
            "timezone": tenant.timezone,
            "logo_url": tenant.logo_url,
            "role": role,
            "current": tenant.id == auth.session.tenant_id,
        }
        for tenant, role in rows
    ]


@router.patch("/workspaces/{workspace_id}")
async def update_personal_workspace(
    workspace_id: str,
    payload: WorkspacePresentationPatch,
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    member, tenant = await _require_workspace_settings(
        db,
        user_id=auth.user.id,
        workspace_id=workspace_id,
    )

    if payload.name is not None:
        name = _clean(payload.name, 200)
        if not name:
            raise HTTPException(422, "Workspace name is required")
        tenant.name = name
    if payload.timezone is not None:
        timezone = _clean(payload.timezone, 100)
        if not timezone:
            raise HTTPException(422, "Timezone is required")
        tenant.timezone = timezone
    if "logo_url" in payload.model_fields_set:
        requested = str(payload.logo_url or "").strip() or None
        if requested is None:
            old_key = _workspace_icon_key(tenant.id, tenant.logo_url)
            tenant.logo_url = None
            await db.commit()
            remove_workspace_icon(tenant_id=tenant.id, key=old_key)
        elif requested != tenant.logo_url:
            raise HTTPException(
                422,
                "Upload workspace icons through the workspace icon control; remote image URLs are not accepted",
            )
    if "logo_url" not in payload.model_fields_set or payload.logo_url:
        await db.commit()
    return {
        "id": tenant.id,
        "name": tenant.name,
        "slug": tenant.slug,
        "timezone": tenant.timezone,
        "logo_url": tenant.logo_url,
        "role": member.role,
    }


@router.put("/workspaces/{workspace_id}/icon")
async def upload_workspace_icon(
    workspace_id: str,
    request: Request,
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    _, tenant = await _require_workspace_settings(
        db,
        user_id=auth.user.id,
        workspace_id=workspace_id,
    )
    data = await request.body()
    try:
        stored = store_workspace_icon(
            tenant_id=tenant.id,
            data=data,
            declared_content_type=request.headers.get("content-type", ""),
        )
    except OverflowError as error:
        raise HTTPException(413, str(error)) from error
    except TypeError as error:
        raise HTTPException(415, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error

    old_key = _workspace_icon_key(tenant.id, tenant.logo_url)
    tenant.logo_url = _workspace_icon_url(tenant.id, stored.key)
    try:
        await db.commit()
    except Exception:
        remove_workspace_icon(tenant_id=tenant.id, key=stored.key)
        raise
    if old_key and old_key != stored.key:
        remove_workspace_icon(tenant_id=tenant.id, key=old_key)
    return {
        "ok": True,
        "workspace_id": tenant.id,
        "logo_url": tenant.logo_url,
        "content_type": stored.content_type,
        "max_bytes": 2 * 1024 * 1024,
    }


@router.get("/workspaces/{workspace_id}/icon/{key}")
async def workspace_icon(
    workspace_id: str,
    key: str,
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    _, tenant = await _workspace_membership(
        db,
        user_id=auth.user.id,
        workspace_id=workspace_id,
    )
    if tenant.logo_url != _workspace_icon_url(tenant.id, key):
        raise HTTPException(404, "Workspace icon not found")
    try:
        path = workspace_icon_path(tenant_id=tenant.id, key=key)
    except LookupError as error:
        raise HTTPException(404, "Workspace icon not found") from error
    suffix = Path(path).suffix.lower()
    content_type = {
        ".jpg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(suffix, "application/octet-stream")
    return FileResponse(
        path,
        media_type=content_type,
        headers={
            "Cache-Control": "private, max-age=86400, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/workspaces/{workspace_id}/icon")
async def delete_workspace_icon(
    workspace_id: str,
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    _, tenant = await _require_workspace_settings(
        db,
        user_id=auth.user.id,
        workspace_id=workspace_id,
    )
    old_key = _workspace_icon_key(tenant.id, tenant.logo_url)
    tenant.logo_url = None
    await db.commit()
    remove_workspace_icon(tenant_id=tenant.id, key=old_key)
    return {"ok": True, "workspace_id": tenant.id, "logo_url": None}


@router.post("/chat")
async def chat(
    payload: PersonalChatInput,
    auth: AccountAuthContext = Depends(get_account_auth_context),
):
    try:
        result = await get_personal_agent_service().run(
            user_id=auth.user.id,
            display_name=auth.user.display_name,
            message=payload.message,
            conversation_id=payload.conversation_id,
            selected_workspace_id=payload.selected_workspace_id,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return await _project_personal_result(auth, result)


@router.post("/chat-with-attachments")
async def chat_with_attachments(
    message: str = Form(default="", max_length=12_000),
    conversation_id: str | None = Form(default=None, max_length=255),
    files: list[UploadFile] = File(default=[]),
    auth: AccountAuthContext = Depends(get_account_auth_context),
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
        scope = _personal_artifact_scope(auth)
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
                source="personal_web_chat_upload",
                created_by=auth.user.id,
                metadata={
                    "ingress": "personal_web_chat_v2",
                    "conversation_id": effective_conversation_id,
                },
            )
            uploaded_artifacts.append(artifact_json(row))

    handles = [
        {
            "artifact_id": item["artifact_id"],
            "filename": item["filename"],
            "content_type": item["content_type"],
            "size_bytes": item["size_bytes"],
        }
        for item in uploaded_artifacts
    ]
    handle_context = (
        "OPERLY UPLOAD ARTIFACT REFERENCES (application-assigned trusted handles):\n"
        + json.dumps(handles, ensure_ascii=False)
        + "\nUse files.convert for format-only conversion (for example image to PDF). "
        + "Use files.process only when the file contents must be understood."
    )
    try:
        result = await get_personal_agent_service().run(
            user_id=auth.user.id,
            display_name=auth.user.display_name,
            message=message.strip() or "Work with the uploaded attachment(s).",
            conversation_id=effective_conversation_id,
            selected_workspace_id=None,
            attachment_context=handle_context,
            attachment_names=[item["filename"] for item in uploaded_artifacts],
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    result = await _project_personal_result(auth, result)
    result["attachment_processing"] = {
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
    auth: AccountAuthContext = Depends(get_account_auth_context),
):
    return await get_personal_agent_service().list_conversations(
        user_id=auth.user.id,
        display_name=auth.user.display_name,
    )


@router.get("/conversations/{conversation_id}/messages")
async def messages(
    conversation_id: str,
    auth: AccountAuthContext = Depends(get_account_auth_context),
):
    try:
        rows = await get_personal_agent_service().messages(
            user_id=auth.user.id,
            conversation_id=conversation_id,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

    message_objects = [
        SimpleNamespace(
            id=row.get("id"),
            role=row.get("role"),
            created_at=datetime.fromisoformat(str(row.get("created_at"))),
        )
        for row in rows
        if row.get("created_at")
    ]
    async with session_scope() as db:
        artifacts_by_message = await artifacts_by_assistant_message(
            db,
            _personal_artifact_scope(auth),
            conversation_id=conversation_id,
            messages=message_objects,
        )
    return [
        {**row, "artifacts": artifacts_by_message.get(str(row.get("id")), [])}
        for row in rows
    ]