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
from apps.api.personal_connectors_router import (
    canva_serializer as personal_canva_serializer,
    serializer as personal_serializer,
    upsert_canva_connector as upsert_personal_canva_connector,
    upsert_google_connector as upsert_personal_google_connector,
)
from packages.company.events import append_event
from packages.connectors.account_secrets import read_account_secret
from packages.connectors.canva_provider import (
    CANVA_SCOPES,
    CanvaProviderRejected,
    access_token as canva_access_token,
    authorization_url as canva_authorization_url,
    canva_capabilities,
    exchange_code as canva_exchange_code,
    finalize_tokens as finalize_canva_tokens,
    get_identity as canva_get_identity,
    pkce_pair as canva_pkce_pair,
)
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
from packages.database.account_connector_models import AccountConnectorSecret
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


def canva_serializer():
    return URLSafeTimedSerializer(
        os.environ["SESSION_SECRET"],
        salt="operly-canva-oauth-v1",
    )


def redirect_uri():
    return os.getenv(
        "GOOGLE_OAUTH_REDIRECT_URI",
        os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
        + "/api/connectors/google/callback",
    )


def load_google_oauth_state(state: str, *, max_age: int = 600) -> tuple[str, dict]:
    """Validate Google OAuth state and identify its credential owner.

    Workspace and personal flows intentionally use different signing salts, but
    both return through the one redirect URI registered with Google.
    """
    try:
        data = serializer().loads(state, max_age=max_age)
    except (BadSignature, SignatureExpired):
        try:
            data = personal_serializer().loads(state, max_age=max_age)
        except (BadSignature, SignatureExpired) as error:
            raise HTTPException(400, "OAuth state is invalid or expired") from error
        if data.get("ownership") != "personal" or not data.get("user_id"):
            raise HTTPException(400, "OAuth state is invalid or expired")
        return "personal", data

    if not data.get("tenant_id") or not data.get("user_id"):
        raise HTTPException(400, "OAuth state is invalid or expired")
    return "workspace", data


def load_canva_oauth_state(state: str, *, max_age: int = 600) -> tuple[str, dict]:
    """Validate Canva OAuth state and identify the credential owner."""
    try:
        data = canva_serializer().loads(state, max_age=max_age)
    except (BadSignature, SignatureExpired):
        try:
            data = personal_canva_serializer().loads(state, max_age=max_age)
        except (BadSignature, SignatureExpired) as error:
            raise HTTPException(400, "OAuth state is invalid or expired") from error
        if (
            data.get("ownership") != "personal"
            or not data.get("user_id")
            or not data.get("pkce_ref")
        ):
            raise HTTPException(400, "OAuth state is invalid or expired")
        return "personal", data

    if (
        data.get("ownership") != "workspace"
        or not data.get("tenant_id")
        or not data.get("user_id")
        or not data.get("pkce_ref")
    ):
        raise HTTPException(400, "OAuth state is invalid or expired")
    return "workspace", data


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


def connector_capabilities(provider: str, scopes: set[str]) -> list[str]:
    if provider == "google":
        return google_capabilities(scopes)
    if provider == "canva":
        return canva_capabilities(scopes)
    return []


def connector_configuration(value: str | None) -> dict:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def canva_configuration(identity: dict, current: str | None = None) -> str:
    config = connector_configuration(current)
    display_name = str(identity.get("display_name") or "Canva account").strip()
    if display_name:
        config["display_name"] = display_name
    team_id = str(identity.get("team_id") or "").strip()
    if team_id:
        config["team_id"] = team_id
    else:
        config.pop("team_id", None)
    return json.dumps(config)


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


async def upsert_canva_connector(db, tenant_id: str, identity: dict, tokens: dict, scopes: list[str]) -> TenantConnector:
    account = str(identity.get("user_id") or "")[:320]
    if not account:
        raise ValueError("Canva account identity was not returned")
    row = await db.scalar(
        select(TenantConnector).where(
            TenantConnector.tenant_id == tenant_id,
            TenantConnector.provider == "canva",
            TenantConnector.provider_account_id == account,
        )
    )
    old_ref = row.credential_reference if row else None
    ref = await store_secret(db, tenant_id, tokens)
    if row:
        row.connector_type = "canva_workspace"
        row.display_name = "Canva"
        row.status = "connected"
        row.enabled = True
        row.credential_reference = ref
        row.granted_scopes_json = json.dumps(scopes)
        row.configuration_json = canva_configuration(identity, row.configuration_json)
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
        connector_type="canva_workspace",
        provider="canva",
        display_name="Canva",
        status="connected",
        enabled=True,
        credential_reference=ref,
        provider_account_id=account,
        granted_scopes_json=json.dumps(scopes),
        configuration_json=canva_configuration(identity),
        health_status="healthy",
        last_health_check=datetime.utcnow(),
    )
    db.add(row)
    await db.flush()
    return row


async def consume_canva_pkce(ownership: str, data: dict) -> str:
    """Consume the encrypted PKCE verifier once before token exchange."""
    reference = str(data.get("pkce_ref") or "")
    try:
        async with session_scope() as db:
            if ownership == "personal":
                secret = await read_account_secret(db, data["user_id"], reference)
                row = await db.get(AccountConnectorSecret, reference)
            else:
                secret = await read_secret(db, data["tenant_id"], reference)
                row = await db.get(ConnectorSecret, reference)
            if (
                secret.get("provider") != "canva"
                or secret.get("purpose") != "oauth_pkce"
                or not secret.get("code_verifier")
                or not row
            ):
                raise LookupError("PKCE verifier is unavailable")
            verifier = str(secret["code_verifier"])
            await db.delete(row)
            return verifier
    except (LookupError, RuntimeError, KeyError) as error:
        raise HTTPException(400, "OAuth state is invalid, expired, or already used") from error


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
        config = connector_configuration(row.configuration_json)
        result.append(
            {
                "id": row.id,
                "provider": row.provider,
                "connector_type": row.connector_type,
                "display_name": row.display_name,
                "status": row.status,
                "enabled": row.enabled,
                "account": config.get("display_name") if row.provider == "canva" else row.provider_account_id,
                "scopes": sorted(scopes),
                "capabilities": connector_capabilities(row.provider, scopes),
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


@router.get("/canva/permissions")
async def canva_permissions(auth: AuthContext = Depends(get_auth_context)):
    owner(auth)
    return {
        "scopes": CANVA_SCOPES,
        "capabilities": canva_capabilities(set(CANVA_SCOPES)),
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


@router.post("/canva/connect")
async def canva_connect(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    owner(auth)
    verifier, challenge = canva_pkce_pair()
    pkce_ref = await store_secret(
        db,
        auth.tenant.id,
        {
            "provider": "canva",
            "purpose": "oauth_pkce",
            "code_verifier": verifier,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    state = canva_serializer().dumps(
        {
            "ownership": "workspace",
            "tenant_id": auth.tenant.id,
            "user_id": auth.user.id,
            "pkce_ref": pkce_ref,
        }
    )
    url = canva_authorization_url(
        state=state,
        code_challenge=challenge,
        scopes=CANVA_SCOPES,
    )
    await db.commit()
    return {
        "authorization_url": url,
        "ownership": "workspace",
        "scopes": CANVA_SCOPES,
    }


@router.get("/google/callback")
async def google_callback(code: str = Query(...), state: str = Query(...)):
    ownership, data = load_google_oauth_state(state)

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

    if ownership == "personal":
        async with session_scope() as db:
            await upsert_personal_google_connector(
                db,
                data["user_id"],
                profile,
                tokens,
                scopes,
            )
        return RedirectResponse("/personal?connector=connected", 303)

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


@router.get("/canva/callback")
async def canva_callback(code: str = Query(...), state: str = Query(...)):
    ownership, data = load_canva_oauth_state(state)
    verifier = await consume_canva_pkce(ownership, data)
    try:
        tokens = await canva_exchange_code(code=code, code_verifier=verifier)
        tokens, scopes = finalize_canva_tokens(tokens, fallback_scopes=CANVA_SCOPES)
        identity = await canva_get_identity(tokens["access_token"])
    except (CanvaProviderRejected, KeyError, ValueError) as error:
        raise HTTPException(400, "Canva authorization failed") from error

    if ownership == "personal":
        async with session_scope() as db:
            await upsert_personal_canva_connector(
                db,
                data["user_id"],
                identity,
                tokens,
                scopes,
            )
        return RedirectResponse("/personal?connector=connected&provider=canva", 303)

    async with session_scope() as db:
        row = await upsert_canva_connector(
            db,
            data["tenant_id"],
            identity,
            tokens,
            scopes,
        )
        await append_event(
            db,
            tenant_id=data["tenant_id"],
            event_type="connector.connected",
            payload={
                "connector_id": row.id,
                "provider": "canva",
                "account": row.provider_account_id,
                "capabilities": canva_capabilities(set(scopes)),
            },
            actor_type="user",
            actor_id=data["user_id"],
            source="connectors",
        )
    return RedirectResponse("/dashboard?connector=connected&provider=canva", 303)


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
        if row.provider == "google":
            token = await access_token(db, row)
            await request_json(
                "GET",
                "https://www.googleapis.com/oauth2/v3/userinfo",
                token,
            )
        elif row.provider == "canva":
            token = await canva_access_token(db, row)
            identity = await canva_get_identity(token)
            row.configuration_json = canva_configuration(identity, row.configuration_json)
        else:
            raise RuntimeError(f"Connector health test is not implemented for {row.provider}")
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
