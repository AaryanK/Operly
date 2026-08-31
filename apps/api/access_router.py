from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext, get_auth_context, get_db
from packages.database.principal_models import ClientGrant, WorkspaceToolExposure
from packages.mcp.gateway import McpGateway, McpRequestContext
from packages.security.permissions import resolve_workspace_permissions
from packages.security.principals import PrincipalService

router = APIRouter(prefix="/api/access", tags=["access"])
gateway = McpGateway()

SUPPORTED_EXTERNAL_CLIENTS = {
    "chatgpt": {
        "id": "chatgpt",
        "name": "ChatGPT",
        "surface": "mcp",
        "owner_managed": True,
    }
}

_SCOPE_CAPABILITY = re.compile(r"^[a-z][a-z0-9_-]*(?:\.[a-z0-9_-]+)*(?:\.\*)?$")
_SCOPE_PERMISSION = re.compile(r"^[a-z][a-z0-9_-]*(?::[a-z][a-z0-9_-]*)+$")


class ClientGrantInput(BaseModel):
    client_id: str = Field(min_length=1, max_length=120)
    scopes: list[str] = Field(default_factory=list, max_length=200)
    workspace_only: bool = True


class ToolExposureInput(BaseModel):
    tool_id: str = Field(min_length=3, max_length=160)
    exposed: bool = False
    access_mode: Literal["authenticated"] = "authenticated"
    surface: Literal["mcp"] = "mcp"


async def _require_manage(db: AsyncSession, auth: AuthContext, permission: str) -> None:
    permissions = await resolve_workspace_permissions(db, tenant_id=auth.tenant.id, role=auth.role)
    if auth.role != "owner" and permission not in permissions:
        raise HTTPException(403, "Workspace permission denied")


def _require_owner(auth: AuthContext) -> None:
    if auth.role != "owner":
        raise HTTPException(403, "Only the workspace owner can manage external AI access")


def _normalize_scopes(values: list[str]) -> list[str]:
    cleaned = sorted({str(item or "").strip().lower() for item in values if str(item or "").strip()})
    if not cleaned:
        return ["workspace:*"]
    for scope in cleaned:
        if scope == "workspace:*":
            continue
        if _SCOPE_CAPABILITY.fullmatch(scope) or _SCOPE_PERMISSION.fullmatch(scope):
            continue
        raise HTTPException(
            422,
            f"Invalid MCP capability scope: {scope}. Use workspace:*, a capability ID, a namespace wildcard such as computer.*, or a Workspace permission such as crm:read.",
        )
    return cleaned


def _scopes(row: ClientGrant) -> list[str]:
    try:
        return [str(item) for item in json.loads(row.scopes_json or "[]")]
    except (TypeError, json.JSONDecodeError):
        return []


@router.get("/external-clients")
async def external_clients(auth: AuthContext = Depends(get_auth_context)):
    del auth
    return list(SUPPORTED_EXTERNAL_CLIENTS.values())


@router.get("/me")
async def current_principal(auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
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


@router.get("/mcp-catalog")
async def mcp_catalog(auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    """Preview every currently authorized Workspace tool before client narrowing.

    Explicit MCP hide policies are returned separately by /tool-exposure so owners can
    still find and unhide a tool. This endpoint never executes anything.
    """

    await _require_manage(db, auth, "workspace:read")
    preview = McpRequestContext(
        tenant_id=auth.tenant.id,
        user_id=auth.user.id,
        client_id="operly-owner-preview",
        token_scopes=frozenset({"workspace:*"}),
        objective="Preview Operly MCP capability catalog",
        enforce_exposure=False,
    )
    tools = await gateway.list_tools(db, preview)
    return {
        "protocol_version": "2026-07-28",
        "endpoint": "/mcp",
        "default_policy": "authorized tools are exposed unless explicitly hidden",
        "tool_count": len(tools),
        "tools": tools,
    }


@router.get("/client-grants")
async def list_client_grants(auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    _require_owner(auth)
    principal = await PrincipalService.user_principal(db, auth.user.id)
    rows = (
        await db.scalars(
            select(ClientGrant).where(
                ClientGrant.principal_id == principal.id,
                ClientGrant.tenant_id == auth.tenant.id,
            )
        )
    ).all()
    return [
        {
            "id": row.id,
            "client_id": row.client_id,
            "workspace_id": row.tenant_id,
            "scopes": _scopes(row),
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
    _require_owner(auth)
    client_id = payload.client_id.strip().lower()
    if client_id not in SUPPORTED_EXTERNAL_CLIENTS:
        raise HTTPException(400, "Unsupported external AI client")
    if not payload.workspace_only:
        raise HTTPException(400, "External AI grants must be scoped to the active workspace")

    principal = await PrincipalService.user_principal(db, auth.user.id)
    tenant_id = auth.tenant.id
    existing = await db.scalar(
        select(ClientGrant).where(
            ClientGrant.principal_id == principal.id,
            ClientGrant.tenant_id == tenant_id,
            ClientGrant.client_id == client_id,
        )
    )
    scopes = _normalize_scopes(payload.scopes)
    if existing:
        existing.scopes_json = json.dumps(scopes, separators=(",", ":"))
        existing.status = "active"
        existing.updated_at = datetime.utcnow()
        row = existing
    else:
        row = ClientGrant(
            principal_id=principal.id,
            tenant_id=tenant_id,
            client_id=client_id,
            scopes_json=json.dumps(scopes, separators=(",", ":")),
            status="active",
        )
        db.add(row)
    await db.commit()
    return {
        "id": row.id,
        "client_id": row.client_id,
        "workspace_id": row.tenant_id,
        "scopes": scopes,
        "authority_note": "This grant only narrows the user's live Workspace authority; it never grants a permission by itself.",
    }


@router.delete("/client-grants/{grant_id}")
async def revoke_client_grant(
    grant_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    _require_owner(auth)
    principal = await PrincipalService.user_principal(db, auth.user.id)
    row = await db.scalar(
        select(ClientGrant).where(
            ClientGrant.id == grant_id,
            ClientGrant.principal_id == principal.id,
            ClientGrant.tenant_id == auth.tenant.id,
        )
    )
    if row is None:
        raise HTTPException(404, "Client grant not found")
    row.status = "revoked"
    row.updated_at = datetime.utcnow()
    await db.commit()
    return {"ok": True}


@router.get("/tool-exposure")
async def list_tool_exposure(auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    await _require_manage(db, auth, "workspace:read")
    rows = (
        await db.scalars(
            select(WorkspaceToolExposure).where(
                WorkspaceToolExposure.tenant_id == auth.tenant.id,
                WorkspaceToolExposure.surface == "mcp",
            )
        )
    ).all()
    return [
        {
            "tool_id": row.tool_id,
            "surface": row.surface,
            "exposed": row.exposed,
            "access_mode": "authenticated",
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
    tool_id = payload.tool_id.strip().lower()
    row = await db.scalar(
        select(WorkspaceToolExposure).where(
            WorkspaceToolExposure.tenant_id == auth.tenant.id,
            WorkspaceToolExposure.tool_id == tool_id,
            WorkspaceToolExposure.surface == "mcp",
        )
    )
    if row is None:
        row = WorkspaceToolExposure(
            tenant_id=auth.tenant.id,
            tool_id=tool_id,
            surface="mcp",
        )
        db.add(row)
    row.exposed = payload.exposed
    row.access_mode = "authenticated"
    await db.commit()
    return {
        "tool_id": row.tool_id,
        "surface": "mcp",
        "exposed": row.exposed,
        "access_mode": "authenticated",
    }
