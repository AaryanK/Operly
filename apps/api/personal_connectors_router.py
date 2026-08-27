import json
import os
from datetime import datetime, timezone
from urllib.parse import urlencode

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AccountAuthContext, get_account_auth_context, get_db
from packages.connectors.account_google import account_access_token
from packages.connectors.account_secrets import read_account_secret, store_account_secret
from packages.connectors.google_provider import (
    CALENDAR,
    CALENDAR_FREEBUSY,
    CALENDAR_LIST_READONLY,
    CALENDAR_SETTINGS_READONLY,
    GMAIL_MODIFY,
    GMAIL_READONLY,
    GMAIL_SEND,
    request_json,
)
from packages.database.account_connector_models import AccountConnector, AccountConnectorSecret
from packages.database.scope_models import PersonalWorkspaceDelegation
from packages.security.personal_delegation import (
    DelegationError,
    grant_delegation,
    list_delegations,
    revoke_delegation,
)


router = APIRouter(tags=["personal-connectors"])

GOOGLE_BASIC_SCOPES = ["openid", "email", GMAIL_SEND, CALENDAR]
GOOGLE_ASSISTANT_SCOPES = [
    "openid", "email", GMAIL_MODIFY, CALENDAR, CALENDAR_FREEBUSY,
    CALENDAR_LIST_READONLY, CALENDAR_SETTINGS_READONLY,
]


def serializer():
    return URLSafeTimedSerializer(os.environ["SESSION_SECRET"], salt="operly-personal-google-oauth-v1")


def redirect_uri():
    return os.getenv(
        "GOOGLE_OAUTH_REDIRECT_URI",
        os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
        + "/api/personal-connectors/google/callback",
    )


def google_capabilities(scopes: set[str]) -> list[str]:
    capabilities: list[str] = []
    if scopes & {GMAIL_SEND, GMAIL_MODIFY}:
        capabilities.extend(["messaging.send", "gmail.send_email"])
    if scopes & {GMAIL_READONLY, GMAIL_MODIFY}:
        capabilities.extend(["gmail.search", "gmail.read_message"])
    if GMAIL_MODIFY in scopes:
        capabilities.extend(["gmail.modify_labels", "gmail.create_draft"])
    if CALENDAR in scopes:
        capabilities.extend(["calendar.create_event", "calendar.list_events", "calendar.update_event", "calendar.delete_event"])
    if CALENDAR_FREEBUSY in scopes:
        capabilities.append("calendar.freebusy")
    if CALENDAR_LIST_READONLY in scopes:
        capabilities.append("calendar.list_calendars")
    return capabilities


def connector_configuration(value: str | None) -> dict:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def google_configuration(profile: dict, current: str | None = None) -> str:
    config = connector_configuration(current)
    config.setdefault("calendar_id", "primary")
    picture = str(profile.get("picture") or "").strip()
    if picture:
        config["avatar_url"] = picture
    return json.dumps(config)


async def upsert_google_connector(db, user_id: str, profile: dict, tokens: dict, scopes: list[str]) -> AccountConnector:
    account = str(profile.get("email") or profile.get("sub") or "")[:320]
    if not account:
        raise ValueError("Google account identity was not returned")
    row = await db.scalar(
        select(AccountConnector).where(
            AccountConnector.user_id == user_id,
            AccountConnector.provider == "google",
            AccountConnector.provider_account_id == account,
        )
    )
    old_ref = row.credential_reference if row else None
    if row and old_ref and not tokens.get("refresh_token"):
        try:
            previous = await read_account_secret(db, user_id, old_ref)
        except Exception:
            previous = {}
        if previous.get("refresh_token"):
            tokens["refresh_token"] = previous["refresh_token"]
    ref = await store_account_secret(db, user_id, tokens)
    if row:
        row.connector_type = "google_account"
        row.display_name = "Personal Google"
        row.status = "connected"
        row.enabled = True
        row.credential_reference = ref
        row.granted_scopes_json = json.dumps(scopes)
        row.configuration_json = google_configuration(profile, row.configuration_json)
        row.health_status = "healthy"
        row.last_health_check = datetime.utcnow()
        row.last_error = None
        await db.flush()
        if old_ref and old_ref != ref:
            old = await db.get(AccountConnectorSecret, old_ref)
            if old:
                await db.delete(old)
        return row
    row = AccountConnector(
        user_id=user_id,
        connector_type="google_account",
        provider="google",
        display_name="Personal Google",
        status="connected",
        enabled=True,
        credential_reference=ref,
        provider_account_id=account,
        granted_scopes_json=json.dumps(scopes),
        configuration_json=google_configuration(profile),
        health_status="healthy",
        last_health_check=datetime.utcnow(),
    )
    db.add(row)
    await db.flush()
    return row


@router.get("/api/personal-connectors")
async def personal_connectors(
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    rows = list((await db.scalars(select(AccountConnector).where(AccountConnector.user_id == auth.user.id).order_by(AccountConnector.created_at))).all())
    return [
        {
            "id": row.id,
            "provider": row.provider,
            "connectorType": row.connector_type,
            "displayName": row.display_name,
            "status": row.status,
            "enabled": row.enabled,
            "account": row.provider_account_id,
            "avatarUrl": connector_configuration(row.configuration_json).get("avatar_url"),
            "scopes": sorted(set(json.loads(row.granted_scopes_json or "[]"))),
            "capabilities": google_capabilities(set(json.loads(row.granted_scopes_json or "[]"))) if row.provider == "google" else [],
            "healthStatus": row.health_status,
            "lastHealthCheck": row.last_health_check.isoformat() if row.last_health_check else None,
            "lastError": row.last_error,
            "ownership": "personal",
        }
        for row in rows
    ]


@router.get("/api/personal-connectors/google/permissions")
async def personal_google_permissions(auth: AccountAuthContext = Depends(get_account_auth_context)):
    return {
        "ownership": "personal",
        "tiers": [
            {"id": "basic", "label": "Send + Calendar", "scopes": GOOGLE_BASIC_SCOPES},
            {"id": "assistant", "label": "Full Google assistant", "scopes": GOOGLE_ASSISTANT_SCOPES},
        ],
    }


@router.post("/api/personal-connectors/google/connect")
async def personal_google_connect(
    tier: str = Query("basic", pattern="^(basic|assistant)$"),
    auth: AccountAuthContext = Depends(get_account_auth_context),
):
    scopes = GOOGLE_ASSISTANT_SCOPES if tier == "assistant" else GOOGLE_BASIC_SCOPES
    state = serializer().dumps({"user_id": auth.user.id, "tier": tier, "ownership": "personal"})
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
    return {"authorization_url": url, "permission_tier": tier, "ownership": "personal"}


@router.get("/api/personal-connectors/google/callback")
async def personal_google_callback(code: str = Query(...), state: str = Query(...), db: AsyncSession = Depends(get_db)):
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
        async with session.get("https://openidconnect.googleapis.com/v1/userinfo", headers={"Authorization": f"Bearer {tokens['access_token']}"}) as response:
            profile = await response.json()
    tokens["expires_at"] = datetime.now(timezone.utc).timestamp() + int(tokens.get("expires_in", 3600))
    scopes = str(tokens.get("scope", "")).split()
    await upsert_google_connector(db, data["user_id"], profile, tokens, scopes)
    await db.commit()
    return RedirectResponse("/personal?connector=connected", 303)


@router.post("/api/personal-connectors/{connector_id}/disable")
async def disable_personal_connector(
    connector_id: str,
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    row = await db.scalar(select(AccountConnector).where(AccountConnector.id == connector_id, AccountConnector.user_id == auth.user.id))
    if not row:
        raise HTTPException(404, "Personal connector not found")
    row.enabled = False
    row.status = "disabled"
    await db.commit()
    return {"ok": True}


@router.delete("/api/personal-connectors/{connector_id}")
async def disconnect_personal_connector(
    connector_id: str,
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    row = await db.scalar(select(AccountConnector).where(AccountConnector.id == connector_id, AccountConnector.user_id == auth.user.id))
    if not row:
        raise HTTPException(404, "Personal connector not found")
    secret = await db.get(AccountConnectorSecret, row.credential_reference) if row.credential_reference else None
    # Revoke grants tied to this personal connector before deleting credentials.
    grants = list((await db.scalars(select(PersonalWorkspaceDelegation).where(PersonalWorkspaceDelegation.user_id == auth.user.id, PersonalWorkspaceDelegation.connector_reference == row.id, PersonalWorkspaceDelegation.status == "active"))).all())
    for grant in grants:
        await revoke_delegation(db, user_id=auth.user.id, delegation_id=grant.id)
    await db.delete(row)
    await db.flush()
    if secret:
        await db.delete(secret)
    await db.commit()
    return {"ok": True, "revokedDelegations": len(grants)}


@router.post("/api/personal-connectors/{connector_id}/test")
async def test_personal_connector(
    connector_id: str,
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    row = await db.scalar(select(AccountConnector).where(AccountConnector.id == connector_id, AccountConnector.user_id == auth.user.id))
    if not row:
        raise HTTPException(404, "Personal connector not found")
    try:
        token = await account_access_token(db, row)
        profile = await request_json("GET", "https://www.googleapis.com/oauth2/v3/userinfo", token)
        if row.provider == "google" and isinstance(profile, dict):
            row.configuration_json = google_configuration(profile, row.configuration_json)
        row.health_status = "healthy"
        row.last_error = None
    except Exception as error:
        row.health_status = "failed"
        row.last_error = str(error)[:500]
    row.last_health_check = datetime.utcnow()
    await db.commit()
    return {"ok": row.health_status == "healthy", "healthStatus": row.health_status, "error": row.last_error}


class DelegationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str = Field(min_length=1, max_length=36)
    capability_id: str = Field(min_length=1, max_length=160)
    connector_reference: str | None = Field(default=None, max_length=36)
    grant_type: str = Field(default="persistent", pattern="^(persistent|one_time)$")
    action_id: str | None = Field(default=None, max_length=120)
    expires_at: datetime | None = None
    scope: dict = Field(default_factory=dict)


@router.get("/api/personal-delegations")
async def delegations(
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    rows = await list_delegations(db, user_id=auth.user.id)
    return [
        {
            "id": row.id,
            "workspaceId": row.tenant_id,
            "capabilityId": row.capability_id,
            "connectorReference": row.connector_reference,
            "grantType": row.grant_type,
            "actionId": row.action_id,
            "status": row.status,
            "scope": json.loads(row.scope_json or "{}"),
            "createdAt": row.created_at.isoformat(),
            "expiresAt": row.expires_at.isoformat() if row.expires_at else None,
            "revokedAt": row.revoked_at.isoformat() if row.revoked_at else None,
            "lastUsedAt": row.last_used_at.isoformat() if row.last_used_at else None,
        }
        for row in rows
    ]


@router.post("/api/personal-delegations", status_code=201)
async def create_delegation(
    payload: DelegationInput,
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    try:
        row = await grant_delegation(
            db,
            user_id=auth.user.id,
            tenant_id=payload.workspace_id,
            capability_id=payload.capability_id,
            connector_reference=payload.connector_reference,
            scope=payload.scope,
            grant_type=payload.grant_type,
            action_id=payload.action_id,
            expires_at=payload.expires_at,
        )
    except DelegationError as error:
        raise HTTPException(403, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    await db.commit()
    return {"id": row.id, "status": row.status, "grantType": row.grant_type}


@router.delete("/api/personal-delegations/{delegation_id}")
async def delete_delegation(
    delegation_id: str,
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    try:
        row = await revoke_delegation(db, user_id=auth.user.id, delegation_id=delegation_id)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    await db.commit()
    return {"ok": True, "id": row.id, "status": row.status}
