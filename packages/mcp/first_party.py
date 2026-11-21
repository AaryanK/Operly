"""First-party DragonZpyder MCP authority bound to an existing Operly Personal session.

Only Operly itself can mint a token for an authenticated, live Personal session.
The bearer is a short-lived execution delegation, NOT an OAuth client grant or
a reusable user credential. The MCP server requires independent service proof
and revalidates the live session + capability registry on every call.
"""
from __future__ import annotations

import os
import secrets
import time
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.models import AppUser, AuthSession
from packages.kernel.contracts import CapabilityRisk
from packages.mcp.oauth import issue_access_token
from packages.personal_modules.runtime import build_personal_runtime
from packages.security.execution_context import resolve_personal_execution_context
from packages.security.surfaces import SurfaceKind

CLIENT_ID = "dragonzpyder_first_party"
MAX_TOKEN_AGE_SECONDS = 240
MAX_TOOLS = 40


class PersonalDelegationDenied(PermissionError):
    pass


def enabled() -> bool:
    return os.getenv("OPERLY_DRAGONZPYDER_PERSONAL_MCP_ENABLED") == "1"


def require_service_proof(proof: str | None) -> None:
    expected = os.getenv("OPERLY_DRAGONZPYDER_MCP_SERVICE_SECRET", "")
    if (not enabled() or len(expected) < 40 or not proof
            or not secrets.compare_digest(proof, expected)):
        raise PersonalDelegationDenied("First-party MCP service authorization denied")


async def validate_personal_session(
    db: AsyncSession, *, session_id: str, user_id: str,
) -> AuthSession:
    if not session_id or not user_id:
        raise PersonalDelegationDenied("Authenticated Personal session required")
    session = await db.get(AuthSession, session_id)
    user = await db.get(AppUser, user_id)
    if (session is None or session.user_id != user_id
            or session.revoked_at is not None
            or session.expires_at <= datetime.utcnow()
            or session.tenant_id is not None
            or user is None or not user.active):
        raise PersonalDelegationDenied("Personal session expired or revoked")
    return session


async def issue_personal_delegation(
    db: AsyncSession, *, session_id: str, user_id: str,
    conversation_id: str,
    resource: str,
) -> tuple[str, frozenset[str]]:
    if not enabled():
        raise PersonalDelegationDenied("Personal MCP delegation is disabled")
    session = await validate_personal_session(
        db, session_id=session_id, user_id=user_id,
    )
    if (not conversation_id or len(conversation_id) > 120 or
            resource != (os.getenv("PUBLIC_BASE_URL", "").rstrip("/") + "/mcp") or
            not resource.startswith("https://")):
        raise PersonalDelegationDenied("Invalid MCP audience or conversation")
    context = await resolve_personal_execution_context(
        db, user_id=user_id, channel="web",
        surface=SurfaceKind.PERSONAL_PRIVATE,
        conversation_id=conversation_id,
    )
    runtime = build_personal_runtime()
    specs = await runtime.available_capabilities(
        db, context=context, limit=50,
    )
    mutations = os.getenv("OPERLY_DRAGONZPYDER_PERSONAL_MCP_MUTATIONS_ENABLED") == "1"
    approved = frozenset(
        spec.id for spec in specs
        if "personal" in spec.scopes
        and (mutations or spec.risk is CapabilityRisk.READ_ONLY)
    )
    if not approved:
        raise PersonalDelegationDenied("No authorized Personal tools are available")
    if len(approved) > MAX_TOOLS:
        # Never silently drop a random subset of an authorized catalog.
        raise PersonalDelegationDenied("Personal capability catalog exceeds configured budget")
    now = int(time.time())
    # A browser session must not be extended by an agent token.
    remaining = int((session.expires_at - datetime.utcnow()).total_seconds())
    expires = now + min(MAX_TOKEN_AGE_SECONDS, remaining)
    if expires <= now:
        raise PersonalDelegationDenied("Personal session expiring")
    payload: dict[str, Any] = {
        "client_id": CLIENT_ID,
        "scope_kind": "personal",
        "session_id": session_id,
        "user_id": user_id,
        "tenant_id": None,
        "conversation_id": conversation_id,
        "resource": resource,
        "scopes": sorted(approved),
        "iat": now,
        "exp": expires,
    }
    return issue_access_token(payload), approved


async def validate_personal_delegation(
    db: AsyncSession, *, claims: dict[str, Any], resource: str,
    service_proof: str | None,
) -> tuple[str, frozenset[str], str]:
    require_service_proof(service_proof)
    now = int(time.time())
    if (claims.get("client_id") != CLIENT_ID or
            claims.get("scope_kind") != "personal" or
            claims.get("tenant_id") is not None or
            claims.get("resource") != resource or
            not isinstance(claims.get("iat"), int) or
            not isinstance(claims.get("exp"), int) or
            not claims["iat"] - 30 <= now < claims["exp"] or
            claims["exp"] - claims["iat"] > MAX_TOKEN_AGE_SECONDS):
        raise PersonalDelegationDenied("Invalid Personal MCP delegation")
    user_id = claims.get("user_id")
    session_id = claims.get("session_id")
    conversation_id = claims.get("conversation_id")
    if (not isinstance(user_id, str) or not isinstance(session_id, str)
            or not isinstance(conversation_id, str)
            or not 1 <= len(conversation_id) <= 120):
        raise PersonalDelegationDenied("Invalid Personal MCP identity")
    await validate_personal_session(db, session_id=session_id, user_id=user_id)
    scopes = claims.get("scopes")
    if (not isinstance(scopes, list) or len(scopes) > MAX_TOOLS
            or any(not isinstance(s, str) or not s for s in scopes)):
        raise PersonalDelegationDenied("Invalid Personal MCP capability ceiling")
    return user_id, frozenset(scopes), conversation_id
