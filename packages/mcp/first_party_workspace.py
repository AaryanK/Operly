"""Single-session, single-workspace MCP delegation for DragonZpyder Workspace.

The bearer narrows exposure; it does not grant any new business permission.
Every MCP request reloads Operly's session, membership and Kernel authority.
Never allow a Personal delegation in this Workspace path.
"""
from __future__ import annotations

import os
import secrets
import time
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.models import AppUser, AuthSession, TenantMember
from packages.kernel.contracts import CapabilityRisk
from packages.mcp.oauth import issue_access_token
from packages.security.execution_context import resolve_execution_context
from packages.security.surfaces import SurfaceKind
from packages.workspace_modules.tools.runtime import build_workspace_runtime

WORKSPACE_CLIENT_ID = "dragonzpyder_first_party_workspace"
MAX_WORKSPACE_TOKEN_SECONDS = 180
MAX_WORKSPACE_TOOLS = 20


class WorkspaceDelegationDenied(PermissionError):
    pass


def _require_service_proof(proof: str | None) -> None:
    expected = os.getenv("OPERLY_DRAGONZPYDER_MCP_SERVICE_SECRET", "")
    if (os.getenv("OPERLY_DRAGONZPYDER_WORKSPACE_MCP_ENABLED") != "1"
            or not isinstance(proof, str) or len(expected) < 40
            or not secrets.compare_digest(proof, expected)):
        raise WorkspaceDelegationDenied("Workspace service proof rejected")


async def _current_scope(
    db: AsyncSession, *, user_id: str, workspace_id: str, session_id: str,
):
    session = await db.get(AuthSession, session_id)
    user = await db.get(AppUser, user_id)
    if (session is None or user is None or not user.active
            or session.user_id != user_id or session.tenant_id != workspace_id
            or session.revoked_at is not None or session.expires_at <= datetime.utcnow()):
        raise WorkspaceDelegationDenied("Workspace session expired or invalid")
    member = await db.scalar(
        select(TenantMember).where(
            TenantMember.user_id == user_id, TenantMember.tenant_id == workspace_id,
        )
    )
    if member is None:
        raise WorkspaceDelegationDenied("Workspace membership removed")
    return session


async def issue_workspace_delegation(
    db: AsyncSession, *, session_id: str, user_id: str, workspace_id: str,
    conversation_id: str, resource: str, query: str,
) -> tuple[str, frozenset[str]]:
    if os.getenv("OPERLY_DRAGONZPYDER_WORKSPACE_MCP_ENABLED") != "1":
        raise WorkspaceDelegationDenied("Workspace delegation disabled")
    if (not user_id or not workspace_id or not session_id
            or not 8 <= len(conversation_id) <= 120
            or resource != os.getenv("PUBLIC_BASE_URL", "").rstrip("/") + "/mcp"
            or not resource.startswith("https://")):
        raise WorkspaceDelegationDenied("Invalid Workspace delegation audience")
    session = await _current_scope(
        db, user_id=user_id, workspace_id=workspace_id, session_id=session_id,
    )
    context = await resolve_execution_context(
        db, workspace_id=workspace_id, user_id=user_id,
        channel="mcp", surface=SurfaceKind.MCP_CLIENT,
        conversation_id=conversation_id, require_membership=True,
    )
    runtime = build_workspace_runtime()
    specs = await runtime.available_capabilities(
        db, context=context, query=query[:200] or None, limit=MAX_WORKSPACE_TOOLS,
    )
    if not specs and query:
        specs = await runtime.available_capabilities(
            db, context=context, query=None, limit=MAX_WORKSPACE_TOOLS,
        )
    allow_mutations = os.getenv("OPERLY_DRAGONZPYDER_WORKSPACE_MCP_MUTATIONS_ENABLED") == "1"
    tools = frozenset(
        spec.id for spec in specs
        if "workspace" in spec.scopes and spec.resource_scope == "workspace"
        and (allow_mutations or spec.risk is CapabilityRisk.READ_ONLY)
    )
    if not tools or len(tools) > MAX_WORKSPACE_TOOLS:
        raise WorkspaceDelegationDenied("No valid Workspace MCP capability selection")
    now = int(time.time())
    remaining = int((session.expires_at - datetime.utcnow()).total_seconds())
    expires = now + min(MAX_WORKSPACE_TOKEN_SECONDS, remaining)
    if expires <= now:
        raise WorkspaceDelegationDenied("Workspace session expiring")
    return issue_access_token({
        "client_id": WORKSPACE_CLIENT_ID,
        "scope_kind": "workspace",
        "user_id": user_id,
        "session_id": session_id,
        "tenant_id": workspace_id,
        "conversation_id": conversation_id,
        "resource": resource,
        "scopes": sorted(tools),
        "iat": now,
        "exp": expires,
    }), tools


async def validate_workspace_delegation(
    db: AsyncSession, *, claims: dict, resource: str,
    service_proof: str | None,
) -> tuple[str, str, frozenset[str], str]:
    _require_service_proof(service_proof)
    now = int(time.time())
    if (claims.get("client_id") != WORKSPACE_CLIENT_ID
            or claims.get("scope_kind") != "workspace"
            or claims.get("resource") != resource
            or type(claims.get("iat")) is not int
            or type(claims.get("exp")) is not int
            or not claims["iat"] - 30 <= now < claims["exp"]
            or not 0 < claims["exp"] - claims["iat"] <= MAX_WORKSPACE_TOKEN_SECONDS):
        raise WorkspaceDelegationDenied("Workspace MCP token invalid")
    user_id = claims.get("user_id")
    tenant_id = claims.get("tenant_id")
    session_id = claims.get("session_id")
    conversation_id = claims.get("conversation_id")
    scopes = claims.get("scopes")
    if (not all(isinstance(v, str) and 8 <= len(v) <= 160
                for v in (user_id, tenant_id, session_id, conversation_id))
            or not isinstance(scopes, list)
            or not 1 <= len(scopes) <= MAX_WORKSPACE_TOOLS
            or any(not isinstance(v, str) or not 1 <= len(v) <= 160 for v in scopes)
            or len(set(scopes)) != len(scopes)):
        raise WorkspaceDelegationDenied("Workspace MCP scope invalid")
    await _current_scope(
        db, user_id=user_id, workspace_id=tenant_id, session_id=session_id,
    )
    return user_id, tenant_id, frozenset(scopes), conversation_id
