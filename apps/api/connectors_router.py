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
from packages.company.events import append_event
from packages.connectors.google_provider import (
    CALENDAR,
    CALENDAR_FREEBUSY,
    CALENDAR_LIST_READONLY,
    CALENDAR_SETTINGS_READONLY,
    GMAIL_MODIFY,
    GMAIL_READONLY,
    GMAIL_SEND,
    access_token,
    request_json,
)
from packages.connectors.secrets import read_secret, store_secret
from packages.database.connector_models import ConnectorSecret, TenantConnector
from packages.database.db import session_scope


router = APIRouter(prefix="/api/connectors", tags=["connectors"])


GOOGLE_BASIC_SCOPES = ["openid", "email", GMAIL_SEND, CALENDAR]
GOOGLE_ASSISTANT_SCOPES = [
    "openid",
    "email",
    GMAIL_MODIFY,
    CALENDAR,
    CALENDAR_FREEBUSY,
    CALENDAR_LIST_READONLY,
    CALENDAR_SETTINGS_READONLY,
]


def serializer():
    return URLSafeTimedSerializer(
        os.environ["SESSION_SECRET"],
        salt="operly-google-oauth-v1",
    )


def redirect_uri():
    return os.getenv(
        "GOOGLE_OAUTH_REDIRECT_URI",
        os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
        + "/api/connectors/google/callback",
    )


def owner(auth):
    if auth.role != "owner":
        raise HTTPException(403, "Only owners can manage connectors")


def google_capabilities(scopes: set[str]) -> list[str]:
    capabilities: list[str] = []
    if scopes & {GMAIL_SEND, GMAIL_MODIFY}:
        capabilities.extend(["messaging.send", "gmail.send_email"])
    if scopes & {GMAIL_READONLY, GMAIL_MODIFY}:
        capabilities.extend(["gmail.search", "gmail.read_message"])
    if GMAIL_MODIFY in scopes:
        capabilities.extend(["gmail.modify_labels", "gmail.create_draft"])
    if CALENDAR in scopes:
        capabilities.extend(
            [
                "calendar.create_event",
                "calendar.list_events",
                "calendar.update_event",
                "calendar.delete_event",
            ]
        )
    if CALENDAR_FREEBUSY in scopes:
        capabilities.append("calendar.freebusy")
    if CALENDAR_LIST_READONLY in scopes:
        capabilities.append("calendar.list_calendars")
    return capabilities


async def upsert_google_connector(db, tenant_id, profile, tokens, scopes):
    account = profile.get("email") or profile.get("sub")
    row = await db.scalar(
        select(TenantConnector).where(
            TenantConnector.tenant_id == tenant_id,
            TenantConnector.provider == "google",
            TenantConnector.provider_account_id == account,
        )
    )

    # Incremental Google OAuth does not always return a refresh token. Keep the
    # previously issued one instead of silently breaking offline access.
    old_ref = row.credential_reference if row else None
    if row and old_ref and not tokens.get("refresh_token"):
        try:
            previous = await read_secret(db, tenant_id, old_ref)
        except Exception:
            previous = {}
        if previous.get("refresh_token"):
            tokens["refresh_token"] = previous["refresh_token"]

    ref = await store_secret(db, tenant_id, tokens)
    if row:
        row.connector_type = "google_workspace"
        row.display_name = "Google Workspace"
        row.status = "connected"
        row.enabled = True
        row.credential_reference = ref
        row.granted_scopes_json = json.dumps(scopes)
        row.configuration_json = row.configuration_json or json.dumps(
            {"calendar_id": "primary"}
        )
        row.health_status = "healthy"
        row.last_health_check = datetime.utcnow()
        row.last_error = None
        await db.flush()
        if old_ref and old_ref != ref:
            old_secret = await db.get(ConnectorSecret, old_ref)
            if old_secret:
                await db.delete(old_secret)
        return row

    row = TenantConnector(
        tenant_id=tenant_id,
        connector_type="google_workspace",
        provider="google",
        display_name="Google Workspace",
        status="connected",
        enabled=True,
        credential_reference=ref,
        provider_account_id=account,
        granted_scopes_json=json.dumps(scopes),
        configuration_json=json.dumps({"calendar_id": "primary"}),
        health_status="healthy",
        last_health_check=datetime.utcnow(),
    )
    db.add(row)
    await db.flush()
    return row


@router.get("")
async def connectors(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.scalars(
            select(TenantConnector)
            .where(TenantConnector.tenant_id == auth.tenant.id)
            .order_by(TenantConnector.created_at)
        )
    ).all()
    result = []
    for row in rows:
        scopes = set(json.loads(row.granted_scopes_json or "[]"))
        result.append(
            {
                "id": row.id,
                "provider": row.provider,
                "connector_type": row.connector_type,
                "display_name": row.display_name,
                "status": row.status,
                "enabled": row.enabled,
                "account": row.provider_account_id,
                "scopes": sorted(scopes),
                "capabilities": (
                    google_capabilities(scopes)
                    if row.provider == "google"
                    else []
                ),
                "permission_tier": (
                    "assistant"
                    if row.provider == "google"
                    and (
                        GMAIL_MODIFY in scopes
                        or CALENDAR_FREEBUSY in scopes
                        or CALENDAR_LIST_READONLY in scopes
                    )
                    else "basic"
                    if row.provider == "google"
                    else None
                ),
                "health_status": row.health_status,
                "last_health_check": (
                    row.last_health_check.isoformat()
                    if row.last_health_check
                    else None
                ),
                "last_error": row.last_error,
            }
        )
    return result


@router.get("/google/permissions")
async def google_permissions(auth: AuthContext = Depends(get_auth_context)):
    owner(auth)
    return {
        "tiers": [
            {
                "id": "basic",
                "label": "Send + Calendar",
                "description": (
                    "Send approved email and create/list/update calendar events. "
                    "Does not read mailbox contents."
                ),
                "scopes": GOOGLE_BASIC_SCOPES,
                "restricted_gmail_access": False,
            },
            {
                "id": "assistant",
                "label": "Full Google assistant",
                "description": (
                    "Adds mailbox search/read/drafts/labels plus calendar "
                    "free-busy and calendar discovery."
                ),
                "scopes": GOOGLE_ASSISTANT_SCOPES,
                "restricted_gmail_access": True,
            },
        ]
    }


@router.post("/google/connect")
async def google_connect(
    tier: str = Query("basic", pattern="^(basic|assistant)$"),
    auth: AuthContext = Depends(get_auth_context),
):
    owner(auth)
    scopes = GOOGLE_ASSISTANT_SCOPES if tier == "assistant" else GOOGLE_BASIC_SCOPES
    state = serializer().dumps(
        {
            "tenant_id": auth.tenant.id,
            "user_id": auth.user.id,
            "tier": tier,
        }
    )
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(
        {
            "client_id": os.environ.get("GOOGLE_OAUTH_CLIENT_ID", ""),
            "redirect_uri": redirect_uri(),
            "response_type": "code",
            "scope": " ".join(scopes),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
            "include_granted_scopes": "true",
        }
    )
    return {"authorization_url": url, "permission_tier": tier}


@router.get("/google/callback")
async def google_callback(code: str = Query(...), state: str = Query(...)):
    try:
        data = serializer().loads(state, max_age=600)
    except (BadSignature, SignatureExpired) as error:
        raise HTTPException(400, "OAuth state is invalid or expired") from error

    form = {
        "code": code,
        "client_id": os.environ["GOOGLE_OAUTH_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
        "redirect_uri": redirect_uri(),
        "grant_type": "authorization_code",
    }
    async with aiohttp.ClientSession() as session:
        async with session.post("https://oauth2.googleapis.com/token", data=form) as response:
            tokens = await response.json()
    if response.status != 200:
        raise HTTPException(400, "Google authorization failed")

    async with aiohttp.ClientSession() as session:
        async with session.get(
            "https://openidconnect.googleapis.com/v1/userinfo",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        ) as response:
            profile = await response.json()

    tokens["expires_at"] = (
        datetime.now(timezone.utc).timestamp()
        + int(tokens.get("expires_in", 3600))
    )
    scopes = str(tokens.get("scope", "")).split()
    async with session_scope() as db:
        row = await upsert_google_connector(
            db,
            data["tenant_id"],
            profile,
            tokens,
            scopes,
        )
        await append_event(
            db,
            tenant_id=data["tenant_id"],
            event_type="connector.connected",
            payload={
                "connector_id": row.id,
                "provider": "google",
                "account": row.provider_account_id,
                "permission_tier": data.get("tier", "basic"),
                "capabilities": google_capabilities(set(scopes)),
            },
            actor_type="user",
            actor_id=data["user_id"],
            source="connectors",
        )
    return RedirectResponse(
        f"/dashboard?connector=connected&tier={data.get('tier', 'basic')}",
        303,
    )


@router.post("/{connector_id}/disable")
async def disable(
    connector_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    owner(auth)
    row = await db.scalar(
        select(TenantConnector).where(
            TenantConnector.id == connector_id,
            TenantConnector.tenant_id == auth.tenant.id,
        )
    )
    if not row:
        raise HTTPException(404, "Connector not found")
    row.enabled = False
    row.status = "disabled"
    await append_event(
        db,
        tenant_id=auth.tenant.id,
        event_type="connector.disabled",
        payload={"connector_id": row.id, "provider": row.provider},
        actor_type="user",
        actor_id=auth.user.id,
    )
    await db.commit()
    return {"ok": True}


@router.delete("/{connector_id}")
async def disconnect(
    connector_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    owner(auth)
    row = await db.scalar(
        select(TenantConnector).where(
            TenantConnector.id == connector_id,
            TenantConnector.tenant_id == auth.tenant.id,
        )
    )
    if not row:
        raise HTTPException(404, "Connector not found")
    secret = (
        await db.get(ConnectorSecret, row.credential_reference)
        if row.credential_reference
        else None
    )
    await db.delete(row)
    await db.flush()
    if secret:
        await db.delete(secret)
    await append_event(
        db,
        tenant_id=auth.tenant.id,
        event_type="connector.disconnected",
        payload={"provider": row.provider},
        actor_type="user",
        actor_id=auth.user.id,
    )
    await db.commit()
    return {"ok": True}


@router.post("/{connector_id}/test")
async def test_connector(
    connector_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    owner(auth)
    row = await db.scalar(
        select(TenantConnector).where(
            TenantConnector.id == connector_id,
            TenantConnector.tenant_id == auth.tenant.id,
        )
    )
    if not row:
        raise HTTPException(404, "Connector not found")
    try:
        token = await access_token(db, row)
        await request_json(
            "GET",
            "https://www.googleapis.com/oauth2/v3/userinfo",
            token,
        )
        row.health_status = "healthy"
        row.last_error = None
    except Exception as error:
        row.health_status = "failed"
        row.last_error = str(error)[:500]
        await append_event(
            db,
            tenant_id=auth.tenant.id,
            event_type="connector.health_failed",
            payload={"connector_id": row.id, "error": row.last_error},
        )
    row.last_health_check = datetime.utcnow()
    await db.commit()
    return {
        "ok": row.health_status == "healthy",
        "health_status": row.health_status,
        "error": row.last_error,
    }
