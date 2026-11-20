"""Private staging-only Operly MCP acceptance host with genuine Kernel tools.

This is intentionally NOT Operly's production application entrypoint. It
starts the existing MCP router, seeds a synthetic owner and workspace in an
ephemeral SQLite DB, and can issue a scoped staging token to a private test
client. Never deploy with public networking or production data.
"""
from __future__ import annotations

import json
import os
import secrets
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Request

from apps.api.mcp_router import router as actual_mcp_router
from packages.database.db import SessionFactory, init_db
from packages.database.models import AppUser, Tenant, TenantMember
from packages.database.principal_models import ClientGrant
from packages.mcp.oauth import grant_refresh_generation, issue_access_token
from packages.security.principals import PrincipalService
from sqlalchemy import select

WORKSPACE_NAME = "DragonZpyder Isolated MCP Evaluation"
app_state: dict[str, str] = {}


def _require_fixture_gate():
    if (os.getenv("OPERLY_MCP_FIXTURE_STAGE") != "staging"
            or os.getenv("OPERLY_ENV") != "development"
            or os.getenv("OPERLY_MCP_FIXTURE_ENABLED") != "1"):
        raise RuntimeError("Private MCP test fixture cannot start outside staging")
    secret = os.getenv("OPERLY_MCP_FIXTURE_BOOTSTRAP_SECRET", "")
    if len(secret) < 40:
        raise RuntimeError("Private MCP test fixture requires a separate secret")
    origin = os.getenv("PUBLIC_BASE_URL", "")
    if (not origin.startswith("http://") or
            not origin.endswith(".railway.internal:8080")):
        raise RuntimeError("Private MCP fixture must use a Railway private origin")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _require_fixture_gate()
    await init_db()
    user_id, workspace_id = str(uuid4()), str(uuid4())
    async with SessionFactory() as db:
        db.add(AppUser(
            id=user_id,
            email="mcp-acceptance-" + user_id + "@example.invalid",
            display_name="Synthetic MCP acceptance principal",
            active=True,
        ))
        db.add(Tenant(
            id=workspace_id, name=WORKSPACE_NAME, timezone="UTC",
        ))
        db.add(TenantMember(
            tenant_id=workspace_id, user_id=user_id, role="owner",
        ))
        await db.flush()
        principal = await PrincipalService.user_principal(db, user_id)
        grant = ClientGrant(
            principal_id=principal.id,
            tenant_id=workspace_id,
            client_id="dragonzpyder",
            scopes_json=json.dumps(["workspace:read"]),
            status="active",
        )
        db.add(grant)
        await db.commit()
        app_state["grant_id"] = grant.id
        app_state["principal_id"] = principal.id
        app_state["workspace_id"] = workspace_id
    yield
    app_state.clear()


app = FastAPI(
    title="Private Operly MCP staging acceptance fixture",
    docs_url=None, redoc_url=None, openapi_url=None,
    lifespan=lifespan,
)
app.include_router(actual_mcp_router)


def _fixture_auth(request: Request, token: str):
    _require_fixture_gate()
    hostname = request.headers.get("host", "")
    expected = os.getenv("PUBLIC_BASE_URL", "").removeprefix("http://")
    if hostname != expected:
        raise HTTPException(403, "Staging-only host required")
    expected_token = os.getenv("OPERLY_MCP_FIXTURE_BOOTSTRAP_SECRET", "")
    if not token or not secrets.compare_digest(token, expected_token):
        raise HTTPException(403, "Invalid staging fixture authority")


@app.get("/healthz")
async def healthz():
    return {"ok": bool(app_state.get("grant_id")), "stage": "synthetic_mcp"}


@app.post("/_fixture/auth")
async def fixture_access_token(
    request: Request,
    x_fixture_authority: str = Header(default=""),
):
    """Private-only test adapter. No user credentials or OAuth bypass in production."""
    _fixture_auth(request, x_fixture_authority)
    async with SessionFactory() as db:
        grant = await db.scalar(select(ClientGrant).where(
            ClientGrant.id == app_state.get("grant_id"),
            ClientGrant.client_id == "dragonzpyder",
            ClientGrant.status == "active",
        ))
        if grant is None:
            raise HTTPException(403, "Staging grant is not active")
        payload = {
            "grant_id": grant.id,
            "principal_id": grant.principal_id,
            "tenant_id": grant.tenant_id,
            "client_id": "dragonzpyder",
            "resource": os.environ["PUBLIC_BASE_URL"] + "/mcp",
            "scopes": ["workspace:read"],
            "refresh_generation": grant_refresh_generation(grant),
        }
        return {"access_token": issue_access_token(payload),
                "token_type": "Bearer", "expires_in": 3600}


@app.post("/_fixture/revoke")
async def fixture_revoke(
    request: Request,
    x_fixture_authority: str = Header(default=""),
):
    """Revocation test only; operates exclusively on synthetic fixture records."""
    _fixture_auth(request, x_fixture_authority)
    async with SessionFactory() as db:
        grant = await db.get(ClientGrant, app_state["grant_id"])
        if grant is None:
            raise HTTPException(404)
        grant.status = "revoked"
        await db.commit()
    return {"ok": True}
