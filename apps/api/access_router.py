import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext, get_auth_context, get_db
from packages.database.principal_models import ClientGrant, WorkspaceToolExposure
from packages.security.permissions import resolve_workspace_permissions
from packages.security.principals import PrincipalService

router = APIRouter(prefix="/api/access", tags=["access"])


class ClientGrantInput(BaseModel):
    client_id: str = Field(min_length=1, max_length=120)
    scopes: list[str] = Field(default_factory=list, max_length=200)
    workspace_only: bool = True


class ToolExposureInput(BaseModel):
    tool_id: str = Field(min_length=3, max_length=160)
    exposed: bool = False
    access_mode: str = Field(default="authenticated", pattern="^(public|authenticated)$")
    surface: str = Field(default="mcp", min_length=1, max_length=40)


async def _require_manage(db: AsyncSession, auth: AuthContext, permission: str) -> None:
    permissions = await resolve_workspace_permissions(
        db, tenant_id=auth.tenant.id, role=auth.role
    )
    if auth.role != "owner" and permission not in permissions:
        raise HTTPException(403, "Workspace permission denied")


@router.get("/me")
async def current_principal(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    principal = await PrincipalService.user_principal(db, auth.user.id)
    await db.commit()
    return {
        "principal_id": principal.id,
        "kind": principal.kind,
        "user_id": auth.user.id,
        "workspace_id": auth.tenant.id,
        "workspace": auth.tenant.name,
        "role": auth.role,
    }


@router.get("/client-grants")
async def list_client_grants(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    principal = await PrincipalService.user_principal(db, auth.user.id)
    rows = (
        await db.scalars(
            select(ClientGrant).where(ClientGrant.principal_id == principal.id)
        )
    ).all()
    return [
        {
            "id": row.id,
            "client_id": row.client_id,
            "workspace_id": row.tenant_id,
            "scopes": json.loads(row.scopes_json or "[]"),
            "status": row.status,
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        }
        for row in rows
    ]


@router.post("/client-grants", status_code=201)
async def create_client_grant(
    payload: ClientGrantInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    principal = await PrincipalService.user_principal(db, auth.user.id)
    tenant_id = auth.tenant.id if payload.workspace_only else None
    existing = await db.scalar(
        select(ClientGrant).where(
            ClientGrant.principal_id == principal.id,
            ClientGrant.tenant_id == tenant_id,
            ClientGrant.client_id == payload.client_id,
        )
    )
    scopes = sorted({str(item).strip() for item in payload.scopes if str(item).strip()})
    if existing:
        existing.scopes_json = json.dumps(scopes, separators=(",", ":"))
        existing.status = "active"
        existing.updated_at = datetime.utcnow()
        row = existing
    else:
        row = ClientGrant(
            principal_id=principal.id,
            tenant_id=tenant_id,
            client_id=payload.client_id,
            scopes_json=json.dumps(scopes, separators=(",", ":")),
            status="active",
        )
        db.add(row)
    await db.commit()
    return {"id": row.id, "client_id": row.client_id, "workspace_id": row.tenant_id, "scopes": scopes}


@router.get("/tool-exposure")
async def list_tool_exposure(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await _require_manage(db, auth, "workspace:read")
    rows = (
        await db.scalars(
            select(WorkspaceToolExposure).where(
                WorkspaceToolExposure.tenant_id == auth.tenant.id
            )
        )
    ).all()
    return [
        {
            "tool_id": row.tool_id,
            "surface": row.surface,
            "exposed": row.exposed,
            "access_mode": row.access_mode,
        }
        for row in rows
    ]


@router.put("/tool-exposure")
async def set_tool_exposure(
    payload: ToolExposureInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await _require_manage(db, auth, "workspace:tools:expose")
    row = await db.scalar(
        select(WorkspaceToolExposure).where(
            WorkspaceToolExposure.tenant_id == auth.tenant.id,
            WorkspaceToolExposure.tool_id == payload.tool_id,
            WorkspaceToolExposure.surface == payload.surface,
        )
    )
    if row is None:
        row = WorkspaceToolExposure(
            tenant_id=auth.tenant.id,
            tool_id=payload.tool_id,
            surface=payload.surface,
        )
        db.add(row)
    row.exposed = payload.exposed
    row.access_mode = payload.access_mode
    await db.commit()
    return {
        "tool_id": row.tool_id,
        "surface": row.surface,
        "exposed": row.exposed,
        "access_mode": row.access_mode,
    }
