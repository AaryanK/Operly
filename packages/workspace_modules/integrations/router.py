from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from urllib.parse import urlencode

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext, get_auth_context, get_db
from packages.connectors.canva_provider import (
    CANVA_SCOPES,
    CanvaProviderRejected,
    access_token as canva_access_token,
    authorization_url as canva_authorization_url,
    exchange_code as canva_exchange_code,
    finalize_tokens as finalize_canva_tokens,
    get_identity as canva_get_identity,
    pkce_pair as canva_pkce_pair,
)
from packages.connectors.secrets import read_secret, store_secret
from packages.database.channel_models import ChannelInstallation
from packages.database.connector_models import ConnectorSecret, TenantConnector
from packages.database.db import session_scope
from packages.workspace_modules.integrations.common import connector_configuration, connector_public_json
from packages.workspace_modules.integrations.discord.client import bot
from packages.workspace_modules.integrations.discord.provider import _invite_url
from packages.workspace_modules.integrations.google.provider import (
    CALENDAR,
    CALENDAR_FREEBUSY,
    CALENDAR_LIST_READONLY,
    GMAIL_MODIFY,
    GMAIL_SEND,
)

router = APIRouter(prefix="/api/connectors", tags=["workspace-integrations"])

GOOGLE_BASIC_SCOPES = ["openid", "email", GMAIL_SEND, CALENDAR]
GOOGLE_ASSISTANT_SCOPES = [
    "openid",
    "email",
    GMAIL_MODIFY,
    CALENDAR,
    CALENDAR_FREEBUSY,
    CALENDAR_LIST_READONLY,
]


def _owner(auth: AuthContext) -> None:
    if auth.role != "owner":
        raise HTTPException(403, "Only workspace owners can manage integrations")


def _serializer(provider: str) -> URLSafeTimedSerializer:
    secret = os.getenv("SESSION_SECRET", "")
    if not secret:
        raise HTTPException(503, "SESSION_SECRET is not configured")
    return URLSafeTimedSerializer(secret, salt=f"operly-workspace-{provider}-oauth-v2")


def _google_redirect_uri() -> str:
    return os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "").strip() or os.getenv(
        "PUBLIC_BASE_URL", "http://localhost:8000"
    ).rstrip("/") + "/api/connectors/google/callback"


def _load_state(provider: str, state: str) -> dict:
    try:
        data = _serializer(provider).loads(state, max_age=600)
    except (BadSignature, SignatureExpired) as error:
        raise HTTPException(400, "OAuth state is invalid or expired") from error
    if not isinstance(data, dict) or data.get("ownership") != "workspace" or not data.get("tenant_id") or not data.get("user_id"):
        raise HTTPException(400, "OAuth state is invalid or expired")
    return data


async def _upsert_google(db: AsyncSession, *, tenant_id: str, profile: dict, tokens: dict, scopes: list[str]) -> TenantConnector:
    account = str(profile.get("email") or profile.get("sub") or "").strip()
    if not account:
        raise ValueError("Google did not return an account identity")
    row = await db.scalar(select(TenantConnector).where(
        TenantConnector.tenant_id == tenant_id,
        TenantConnector.provider == "google",
        TenantConnector.provider_account_id == account,
    ))
    old_ref = row.credential_reference if row else None
    if old_ref and not tokens.get("refresh_token"):
        try:
            previous = await read_secret(db, tenant_id, old_ref)
        except Exception:
            previous = {}
        if previous.get("refresh_token"):
            tokens["refresh_token"] = previous["refresh_token"]
    new_ref = await store_secret(db, tenant_id, tokens)
    if row is None:
        row = TenantConnector(
            tenant_id=tenant_id,
            connector_type="google_workspace",
            provider="google",
            display_name="Google Workspace",
            status="connected",
            enabled=True,
            credential_reference=new_ref,
            provider_account_id=account[:320],
            granted_scopes_json=json.dumps(scopes),
            configuration_json=json.dumps({"calendar_id": "primary", "display_name": account}),
            health_status="healthy",
            last_health_check=datetime.utcnow(),
        )
        db.add(row)
    else:
        row.connector_type = "google_workspace"
        row.display_name = "Google Workspace"
        row.status = "connected"
        row.enabled = True
        row.credential_reference = new_ref
        row.granted_scopes_json = json.dumps(scopes)
        config = connector_configuration(row)
        config.update({"calendar_id": config.get("calendar_id", "primary"), "display_name": account})
        row.configuration_json = json.dumps(config)
        row.health_status = "healthy"
        row.last_health_check = datetime.utcnow()
        row.last_error = None
    await db.flush()
    if old_ref and old_ref != new_ref:
        old = await db.get(ConnectorSecret, old_ref)
        if old:
            await db.delete(old)
    return row


async def _upsert_canva(db: AsyncSession, *, tenant_id: str, identity: dict, tokens: dict, scopes: list[str]) -> TenantConnector:
    account = str(identity.get("user_id") or "").strip()
    if not account:
        raise ValueError("Canva did not return an account identity")
    row = await db.scalar(select(TenantConnector).where(
        TenantConnector.tenant_id == tenant_id,
        TenantConnector.provider == "canva",
        TenantConnector.provider_account_id == account,
    ))
    old_ref = row.credential_reference if row else None
    new_ref = await store_secret(db, tenant_id, tokens)
    config = connector_configuration(row) if row else {}
    config["display_name"] = str(identity.get("display_name") or "Canva account")[:200]
    if identity.get("team_id"):
        config["team_id"] = str(identity["team_id"])
    if row is None:
        row = TenantConnector(
            tenant_id=tenant_id,
            connector_type="canva_workspace",
            provider="canva",
            display_name="Canva",
            status="connected",
            enabled=True,
            credential_reference=new_ref,
            provider_account_id=account[:320],
            granted_scopes_json=json.dumps(scopes),
            configuration_json=json.dumps(config),
            health_status="healthy",
            last_health_check=datetime.utcnow(),
        )
        db.add(row)
    else:
        row.connector_type = "canva_workspace"
        row.display_name = "Canva"
        row.status = "connected"
        row.enabled = True
        row.credential_reference = new_ref
        row.granted_scopes_json = json.dumps(scopes)
        row.configuration_json = json.dumps(config)
        row.health_status = "healthy"
        row.last_health_check = datetime.utcnow()
        row.last_error = None
    await db.flush()
    if old_ref and old_ref != new_ref:
        old = await db.get(ConnectorSecret, old_ref)
        if old:
            await db.delete(old)
    return row


@router.get("")
async def connections(auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    rows = (await db.scalars(select(TenantConnector).where(TenantConnector.tenant_id == auth.tenant.id).order_by(TenantConnector.created_at))).all()
    result = [connector_public_json(row) for row in rows]
    installations = (await db.scalars(select(ChannelInstallation).where(
        ChannelInstallation.tenant_id == auth.tenant.id,
        ChannelInstallation.provider == "discord",
    ))).all()
    result.extend({
        "id": row.id,
        "provider": "discord",
        "connector_type": "discord_guild",
        "display_name": row.display_name,
        "account": row.display_name,
        "status": row.status,
        "enabled": row.status == "connected",
        "scopes": [],
        "capabilities": ["discord.channels.list", "discord.messages.list", "discord.message.send"],
        "health_status": "healthy" if bot.get_guild(int(row.external_space_id)) else "offline",
        "guild_id": row.external_space_id,
    } for row in installations)
    return result


@router.get("/google/permissions")
async def google_permissions(auth: AuthContext = Depends(get_auth_context)):
    _owner(auth)
    return {"tiers": [
        {"id": "basic", "label": "Send + Calendar", "scopes": GOOGLE_BASIC_SCOPES},
        {"id": "assistant", "label": "Gmail + Calendar", "scopes": GOOGLE_ASSISTANT_SCOPES},
    ]}


@router.get("/canva/permissions")
async def canva_permissions(auth: AuthContext = Depends(get_auth_context)):
    _owner(auth)
    return {"scopes": CANVA_SCOPES}


@router.get("/discord/status")
async def discord_status(auth: AuthContext = Depends(get_auth_context)):
    del auth
    return {"configured": bool(os.getenv("DISCORD_BOT_TOKEN", "").strip()), "ready": bool(bot.is_ready()), "bot_user": str(bot.user) if bot.user else None, "invite_url": _invite_url(), "ai_enabled": False}


@router.post("/google/connect")
async def google_connect(tier: str = Query("basic", pattern="^(basic|assistant)$"), auth: AuthContext = Depends(get_auth_context)):
    _owner(auth)
    client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "").strip()
    client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise HTTPException(503, "Google OAuth is not configured")
    scopes = GOOGLE_ASSISTANT_SCOPES if tier == "assistant" else GOOGLE_BASIC_SCOPES
    state = _serializer("google").dumps({"ownership": "workspace", "tenant_id": auth.tenant.id, "user_id": auth.user.id, "tier": tier})
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": client_id,
        "redirect_uri": _google_redirect_uri(),
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "include_granted_scopes": "true",
    })
    return {"authorization_url": url, "permission_tier": tier}


@router.post("/canva/connect")
async def canva_connect(auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    _owner(auth)
    verifier, challenge = canva_pkce_pair()
    pkce_ref = await store_secret(db, auth.tenant.id, {"provider": "canva", "purpose": "oauth_pkce", "code_verifier": verifier, "created_at": datetime.now(timezone.utc).isoformat()})
    state = _serializer("canva").dumps({"ownership": "workspace", "tenant_id": auth.tenant.id, "user_id": auth.user.id, "pkce_ref": pkce_ref})
    url = canva_authorization_url(state=state, code_challenge=challenge, scopes=CANVA_SCOPES)
    await db.commit()
    return {"authorization_url": url, "scopes": CANVA_SCOPES}


@router.get("/google/callback")
async def google_callback(code: str = Query(...), state: str = Query(...)):
    data = _load_state("google", state)
    form = {"code": code, "client_id": os.environ["GOOGLE_OAUTH_CLIENT_ID"], "client_secret": os.environ["GOOGLE_OAUTH_CLIENT_SECRET"], "redirect_uri": _google_redirect_uri(), "grant_type": "authorization_code"}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
        async with session.post("https://oauth2.googleapis.com/token", data=form) as response:
            tokens = await response.json(content_type=None)
        if response.status != 200 or not isinstance(tokens, dict):
            raise HTTPException(400, "Google authorization failed")
        async with session.get("https://openidconnect.googleapis.com/v1/userinfo", headers={"Authorization": f"Bearer {tokens['access_token']}"}) as profile_response:
            profile = await profile_response.json(content_type=None)
        if profile_response.status != 200 or not isinstance(profile, dict):
            raise HTTPException(400, "Google account identity could not be verified")
    tokens["expires_at"] = datetime.now(timezone.utc).timestamp() + int(tokens.get("expires_in", 3600))
    scopes = [item for item in str(tokens.get("scope") or "").split() if item]
    async with session_scope() as db:
        await _upsert_google(db, tenant_id=data["tenant_id"], profile=profile, tokens=tokens, scopes=scopes)
        await db.commit()
    return RedirectResponse(f"/channels/{data['tenant_id']}/connections?connector=google", status_code=303)


@router.get("/canva/callback")
async def canva_callback(code: str = Query(...), state: str = Query(...)):
    data = _load_state("canva", state)
    async with session_scope() as db:
        pkce_ref = str(data.get("pkce_ref") or "")
        try:
            secret = await read_secret(db, data["tenant_id"], pkce_ref)
            secret_row = await db.get(ConnectorSecret, pkce_ref)
        except Exception as error:
            raise HTTPException(400, "Canva OAuth verifier is unavailable") from error
        if not secret_row or secret.get("provider") != "canva" or secret.get("purpose") != "oauth_pkce" or not secret.get("code_verifier"):
            raise HTTPException(400, "Canva OAuth verifier is unavailable")
        verifier = str(secret["code_verifier"])
        await db.delete(secret_row)
        await db.commit()
    try:
        tokens = await canva_exchange_code(code=code, code_verifier=verifier)
        tokens, scopes = finalize_canva_tokens(tokens, fallback_scopes=CANVA_SCOPES)
        identity = await canva_get_identity(tokens["access_token"])
    except (CanvaProviderRejected, KeyError, ValueError) as error:
        raise HTTPException(400, "Canva authorization failed") from error
    async with session_scope() as db:
        await _upsert_canva(db, tenant_id=data["tenant_id"], identity=identity, tokens=tokens, scopes=scopes)
        await db.commit()
    return RedirectResponse(f"/channels/{data['tenant_id']}/connections?connector=canva", status_code=303)


@router.post("/{connector_id}/disable")
async def disable_connection(connector_id: str, auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    _owner(auth)
    row = await db.scalar(select(TenantConnector).where(TenantConnector.id == connector_id, TenantConnector.tenant_id == auth.tenant.id))
    if row is not None:
        row.enabled = False
        row.status = "disabled"
        await db.commit()
        return {"ok": True}
    installation = await db.scalar(select(ChannelInstallation).where(ChannelInstallation.id == connector_id, ChannelInstallation.tenant_id == auth.tenant.id, ChannelInstallation.provider == "discord"))
    if installation is None:
        raise HTTPException(404, "Connection not found")
    installation.status = "disabled"
    await db.commit()
    return {"ok": True}


@router.delete("/{connector_id}")
async def disconnect(connector_id: str, auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    _owner(auth)
    row = await db.scalar(select(TenantConnector).where(TenantConnector.id == connector_id, TenantConnector.tenant_id == auth.tenant.id))
    if row is not None:
        secret = await db.get(ConnectorSecret, row.credential_reference) if row.credential_reference else None
        await db.delete(row)
        await db.flush()
        if secret:
            await db.delete(secret)
        await db.commit()
        return {"ok": True}
    installation = await db.scalar(select(ChannelInstallation).where(ChannelInstallation.id == connector_id, ChannelInstallation.tenant_id == auth.tenant.id, ChannelInstallation.provider == "discord"))
    if installation is None:
        raise HTTPException(404, "Connection not found")
    await db.delete(installation)
    await db.commit()
    return {"ok": True}


@router.post("/{connector_id}/test")
async def test_connection(connector_id: str, auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    _owner(auth)
    row = await db.scalar(select(TenantConnector).where(TenantConnector.id == connector_id, TenantConnector.tenant_id == auth.tenant.id))
    if row is not None:
        try:
            if row.provider == "canva":
                token = await canva_access_token(db, row)
                identity = await canva_get_identity(token)
                config = connector_configuration(row)
                config["display_name"] = identity.get("display_name")
                if identity.get("team_id"):
                    config["team_id"] = identity["team_id"]
                row.configuration_json = json.dumps(config)
            elif row.provider == "google":
                from packages.workspace_modules.integrations.google.provider import _access_token
                token = await _access_token(db, row)
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
                    async with session.get("https://openidconnect.googleapis.com/v1/userinfo", headers={"Authorization": f"Bearer {token}"}) as response:
                        if response.status != 200:
                            raise RuntimeError("Google health check failed")
            else:
                raise RuntimeError("Unsupported provider")
            row.health_status = "healthy"
            row.last_error = None
        except Exception as error:
            row.health_status = "failed"
            row.last_error = type(error).__name__
        row.last_health_check = datetime.utcnow()
        await db.commit()
        return {"ok": row.health_status == "healthy", "health_status": row.health_status, "error": row.last_error}
    installation = await db.scalar(select(ChannelInstallation).where(ChannelInstallation.id == connector_id, ChannelInstallation.tenant_id == auth.tenant.id, ChannelInstallation.provider == "discord"))
    if installation is None:
        raise HTTPException(404, "Connection not found")
    guild = bot.get_guild(int(installation.external_space_id)) if bot.is_ready() else None
    return {"ok": guild is not None, "health_status": "healthy" if guild is not None else "offline", "error": None if guild is not None else "Discord bot is not connected to this server"}
