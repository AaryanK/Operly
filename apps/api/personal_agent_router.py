from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AccountAuthContext, get_account_auth_context, get_db
from packages.business_brain.personal_agent import get_personal_agent_service
from packages.database.models import AppUser, Tenant, TenantMember
from packages.security.permissions import resolve_workspace_permissions


router = APIRouter(prefix="/api/personal-agent", tags=["personal-agent"])


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
    logo_url: str | None = Field(default=None, max_length=1000)


def _clean(value: str, limit: int) -> str:
    return " ".join(str(value or "").replace("\x00", "").split()).strip()[:limit]


def _logo_url(value: str | None) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if not raw.lower().startswith("https://"):
        raise ValueError("Workspace logo must use an HTTPS image URL")
    return raw[:1000]


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
    row = (
        await db.execute(
            select(TenantMember, Tenant)
            .join(Tenant, Tenant.id == TenantMember.tenant_id)
            .where(
                TenantMember.user_id == auth.user.id,
                TenantMember.tenant_id == workspace_id,
            )
        )
    ).first()
    if row is None:
        raise HTTPException(404, "Workspace not found")
    member, tenant = row
    permissions = await resolve_workspace_permissions(db, tenant_id=tenant.id, role=member.role)
    if member.role != "owner" and "workspace:settings:manage" not in permissions:
        raise HTTPException(403, "Workspace settings permission denied")

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
        try:
            tenant.logo_url = _logo_url(payload.logo_url)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
    await db.commit()
    return {
        "id": tenant.id,
        "name": tenant.name,
        "slug": tenant.slug,
        "timezone": tenant.timezone,
        "logo_url": tenant.logo_url,
        "role": member.role,
    }


@router.post("/chat")
async def chat(
    payload: PersonalChatInput,
    auth: AccountAuthContext = Depends(get_account_auth_context),
):
    try:
        return await get_personal_agent_service().run(
            user_id=auth.user.id,
            display_name=auth.user.display_name,
            message=payload.message,
            conversation_id=payload.conversation_id,
            selected_workspace_id=payload.selected_workspace_id,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


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
        return await get_personal_agent_service().messages(
            user_id=auth.user.id,
            conversation_id=conversation_id,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
