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

from apps.api.dependencies import AccountAuthContext, get_account_auth_context, get_db
from packages.connectors.account_secrets import read_account_secret, store_account_secret
from packages.database.account_connector_models import AccountConnector, AccountConnectorSecret
from packages.personal_modules.google_provider import (
    CALENDAR,
    CALENDAR_FREEBUSY,
    CALENDAR_LIST_READONLY,
    GMAIL_MODIFY,
    GMAIL_READONLY,
    access_token,
    connector_scopes,
    request_json,
    supported_capability_ids,
)


router = APIRouter(prefix="/api/personal-connectors", tags=["personal-connectors"])

GOOGLE_BASIC_SCOPES = [
    "openid",
    "email",
    "profile",
    GMAIL_READONLY,
    CALENDAR,
]
GOOGLE_ASSISTANT_SCOPES = [
    "openid",
    "email",
    "profile",
    GMAIL_MODIFY,
    CALENDAR,
    CALENDAR_FREEBUSY,
    CALENDAR_LIST_READONLY,
]


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        os.environ["SESSION_SECRET"],
        salt="operly-personal-google-oauth-v2",
    )


def _redirect_uri() -> str:
    configured = os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "").strip()
    if configured:
        return configured
    return (
        os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
        + "/api/personal-connectors/google/callback"
    )


def _configuration(value: str | None) -> dict:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _google_configuration(profile: dict, current: str | None = None) -> str:
    config = _configuration(current)
    config.setdefault("calendar_id", "primary")
    picture = str(profile.get("picture") or "").strip()
    if picture:
        config["avatar_url"] = picture
    name = str(profile.get("name") or "").strip()
    if name:
        config["display_name"] = name
    return json.dumps(config, separators=(",", ":"), sort_keys=True)


async def _upsert_google_connector(
    db: AsyncSession,
    *,
    user_id: str,
    profile: dict,
    tokens: dict,
    scopes: list[str],
) -> AccountConnector:
    account = str(profile.get("email") or profile.get("sub") or "")[:320].strip()
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
    if old_ref and not tokens.get("refresh_token"):
        try:
            previous = await read_account_secret(db, user_id, old_ref)
        except Exception:
            previous = {}
        if previous.get("refresh_token"):
            tokens["refresh_token"] = previous["refresh_token"]
    ref = await store_account_secret(db, user_id, tokens)
    if row is None:
        row = AccountConnector(
            user_id=user_id,
            connector_type="google_account",
            provider="google",
            display_name="Personal Google",
            status="connected",
            enabled=True,
            provider_account_id=account,
        )
        db.add(row)
    row.connector_type = "google_account"
    row.display_name = "Personal Google"
    row.status = "connected"
    row.enabled = True
    row.credential_reference = ref
    row.granted_scopes_json = json.dumps(sorted(set(scopes)))
    row.configuration_json = _google_configuration(
        profile,
        row.configuration_json if row.id else None,
    )
    row.health_status = "healthy"
    row.last_health_check = datetime.utcnow()
    row.last_error = None
    await db.flush()
    if old_ref and old_ref != ref:
        old = await db.get(AccountConnectorSecret, old_ref)
        if old and old.user_id == user_id:
            await db.delete(old)
    return row


def _connector_json(row: AccountConnector) -> dict:
    scopes = connector_scopes(row)
    config = _configuration(row.configuration_json)
    return {
        "id": row.id,
        "provider": row.provider,
        "connectorType": row.connector_type,
        "displayName": row.display_name,
        "status": row.status,
        "enabled": row.enabled,
        "account": row.provider_account_id,
        "avatarUrl": config.get("avatar_url"),
        "scopes": sorted(scopes),
        "capabilities": supported_capability_ids(scopes),
        "healthStatus": row.health_status,
        "lastHealthCheck": (
            row.last_health_check.isoformat() if row.last_health_check else None
        ),
        "lastError": row.last_error,
        "ownership": "personal",
    }


@router.get("")
async def personal_connectors(
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    rows = list(
        (
            await db.scalars(
                select(AccountConnector)
                .where(AccountConnector.user_id == auth.user.id)
                .order_by(AccountConnector.created_at)
            )
        ).all()
    )
    return [_connector_json(row) for row in rows]


@router.get("/google/permissions")
async def personal_google_permissions(
    auth: AccountAuthContext = Depends(get_account_auth_context),
):
    del auth
    return {
        "ownership": "personal",
        "tiers": [
            {
                "id": "basic",
                "label": "Gmail + Calendar reads",
                "scopes": GOOGLE_BASIC_SCOPES,
            },
            {
                "id": "assistant",
                "label": "Full Google assistant",
                "scopes": GOOGLE_ASSISTANT_SCOPES,
            },
        ],
    }


@router.post("/google/connect")
async def personal_google_connect(
    tier: str = Query("assistant", pattern="^(basic|assistant)$"),
    auth: AccountAuthContext = Depends(get_account_auth_context),
):
    client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "").strip()
    if not client_id:
        raise HTTPException(503, "Personal Google OAuth is not configured")
    scopes = GOOGLE_ASSISTANT_SCOPES if tier == "assistant" else GOOGLE_BASIC_SCOPES
    state = _serializer().dumps(
        {
            "user_id": auth.user.id,
            "tier": tier,
            "ownership": "personal",
        }
    )
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(
        {
            "client_id": client_id,
            "redirect_uri": _redirect_uri(),
            "response_type": "code",
            "scope": " ".join(scopes),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
            "include_granted_scopes": "true",
        }
    )
    return {
        "authorization_url": url,
        "permission_tier": tier,
        "ownership": "personal",
    }


@router.get("/google/callback")
async def personal_google_callback(
    code: str = Query(..., min_length=1, max_length=4096),
    state: str = Query(..., min_length=20, max_length=4096),
    db: AsyncSession = Depends(get_db),
):
    try:
        data = _serializer().loads(state, max_age=600)
    except (BadSignature, SignatureExpired) as error:
        raise HTTPException(400, "OAuth state is invalid or expired") from error
    if data.get("ownership") != "personal" or not data.get("user_id"):
        raise HTTPException(400, "OAuth state has the wrong ownership scope")
    client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "").strip()
    client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise HTTPException(503, "Personal Google OAuth is not configured")
    form = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": _redirect_uri(),
        "grant_type": "authorization_code",
    }
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
        async with session.post("https://oauth2.googleapis.com/token", data=form) as response:
            tokens = await response.json(content_type=None)
    if response.status != 200 or not isinstance(tokens, dict) or not tokens.get("access_token"):
        raise HTTPException(400, "Google authorization failed")
    profile = await request_json(
        "GET",
        "https://openidconnect.googleapis.com/v1/userinfo",
        str(tokens["access_token"]),
    )
    tokens["expires_at"] = (
        datetime.now(timezone.utc).timestamp() + int(tokens.get("expires_in", 3600))
    )
    scopes = str(tokens.get("scope", "")).split()
    await _upsert_google_connector(
        db,
        user_id=str(data["user_id"]),
        profile=profile,
        tokens=tokens,
        scopes=scopes,
    )
    await db.commit()
    return RedirectResponse("/personal?connector=connected", 303)


@router.post("/{connector_id}/test")
async def test_personal_connector(
    connector_id: str,
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    row = await db.scalar(
        select(AccountConnector).where(
            AccountConnector.id == connector_id,
            AccountConnector.user_id == auth.user.id,
        )
    )
    if not row:
        raise HTTPException(404, "Personal connector not found")
    if row.provider != "google":
        raise HTTPException(422, "This connector test is not implemented yet")
    try:
        token = await access_token(db, row)
        profile = await request_json(
            "GET",
            "https://openidconnect.googleapis.com/v1/userinfo",
            token,
        )
        row.configuration_json = _google_configuration(profile, row.configuration_json)
        row.health_status = "healthy"
        row.last_error = None
    except Exception as error:
        row.health_status = "failed"
        row.last_error = str(error)[:500]
    row.last_health_check = datetime.utcnow()
    await db.commit()
    return {
        "ok": row.health_status == "healthy",
        "healthStatus": row.health_status,
        "error": row.last_error,
    }


@router.post("/{connector_id}/disable")
async def disable_personal_connector(
    connector_id: str,
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    row = await db.scalar(
        select(AccountConnector).where(
            AccountConnector.id == connector_id,
            AccountConnector.user_id == auth.user.id,
        )
    )
    if not row:
        raise HTTPException(404, "Personal connector not found")
    row.enabled = False
    row.status = "disabled"
    await db.commit()
    return {"ok": True}


@router.delete("/{connector_id}")
async def disconnect_personal_connector(
    connector_id: str,
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    row = await db.scalar(
        select(AccountConnector).where(
            AccountConnector.id == connector_id,
            AccountConnector.user_id == auth.user.id,
        )
    )
    if not row:
        raise HTTPException(404, "Personal connector not found")
    secret = (
        await db.get(AccountConnectorSecret, row.credential_reference)
        if row.credential_reference
        else None
    )
    await db.delete(row)
    await db.flush()
    if secret and secret.user_id == auth.user.id:
        await db.delete(secret)
    await db.commit()
    return {"ok": True}
