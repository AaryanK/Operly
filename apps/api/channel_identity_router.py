import hmac
import json
import os
import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.auth_cookies import production, set_session_cookies
from apps.api.dependencies import (
    AccountAuthContext,
    AuthContext,
    get_account_auth_context,
    get_auth_context,
    get_db,
)
from apps.api.security import normalize_email
from apps.api.session import _audit, _create_session, _first_membership, _rate_limit, auth_error
from packages.channels.identity import IdentityLinkConflict, IdentityService
from packages.channels.linking import IdentityLinkService
from packages.database.channel_models import ExternalIdentity
from packages.database.models import AppUser, AuthIdentity
from packages.security.human_identity import HumanIdentityService
from packages.security.temporal_context import set_user_timezone, user_timezone, validate_timezone

router = APIRouter(prefix="/api/identities", tags=["identities"])

DISCORD_OAUTH_STATE_COOKIE = "operly_discord_oauth_state"
DISCORD_AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
DISCORD_TOKEN_URL = "https://discord.com/api/v10/oauth2/token"
DISCORD_ME_URL = "https://discord.com/api/v10/users/@me"


class ClaimIdentityInput(BaseModel):
    token: str = Field(min_length=20, max_length=500)


class TimezoneInput(BaseModel):
    timezone: str = Field(min_length=1, max_length=100)


def _provider(value: str) -> str:
    provider = "".join(ch for ch in str(value or "").strip().lower() if ch.isalnum() or ch in {"-", "_"})
    if not provider or len(provider) > 40:
        raise HTTPException(422, "Invalid identity provider")
    return provider


def _discord_config() -> tuple[str, str, str]:
    client_id = os.getenv("DISCORD_AUTH_CLIENT_ID", "").strip()
    client_secret = os.getenv("DISCORD_AUTH_CLIENT_SECRET", "").strip()
    public_base_url = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").strip().rstrip("/")
    redirect_uri = os.getenv("DISCORD_AUTH_REDIRECT_URI", "").strip() or f"{public_base_url}/api/identities/discord/callback"
    return client_id, client_secret, redirect_uri


def _discord_error(message: str) -> RedirectResponse:
    return RedirectResponse(url=f"/login?discord_error={urlencode({'message': message})[8:]}", status_code=303)


def _set_discord_state_cookie(response: RedirectResponse, state: str) -> None:
    secure = production() or os.getenv("PUBLIC_BASE_URL", "").lower().startswith("https://")
    response.set_cookie(
        DISCORD_OAUTH_STATE_COOKIE,
        state,
        max_age=10 * 60,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


def _clear_discord_state_cookie(response: RedirectResponse) -> None:
    response.delete_cookie(DISCORD_OAUTH_STATE_COOKIE, path="/")


@router.get("")
async def identities(
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.scalars(
            select(ExternalIdentity)
            .where(ExternalIdentity.user_id == auth.user.id)
            .order_by(ExternalIdentity.created_at)
        )
    ).all()
    return [
        {
            "id": row.id,
            "provider": row.provider,
            "display_name": row.display_name,
            "verified_at": row.verified_at.isoformat() if row.verified_at else None,
        }
        for row in rows
    ]


@router.get("/graph")
async def identity_graph(
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Resolve all known authentication, channel and connector identities to one human."""
    try:
        snapshot = await HumanIdentityService.snapshot(db, user_id=auth.user.id)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    return snapshot.as_dict()


@router.get("/preferences/timezone")
async def get_timezone_preference(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    value = await user_timezone(db, auth.user.id)
    return {
        "timezone": value,
        "workspace_fallback": validate_timezone(auth.tenant.timezone),
    }


@router.put("/preferences/timezone")
async def put_timezone_preference(
    payload: TimezoneInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    try:
        value = await set_user_timezone(db, user_id=auth.user.id, timezone_name=payload.timezone)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    await db.commit()
    return {"ok": True, "timezone": value}


@router.get("/discord/sign-in")
async def discord_sign_in(request: Request, db: AsyncSession = Depends(get_db)):
    """Start personal-account Discord OAuth and bind the Discord human identity."""
    client_id, client_secret, redirect_uri = _discord_config()
    if not client_id or not client_secret:
        raise auth_error(503, "DISCORD_SIGN_IN_NOT_CONFIGURED", "Discord sign-in is not configured yet")
    await _rate_limit(db, "discord_oauth_start", request, combined_limit=30, ip_limit=30)
    state = secrets.token_urlsafe(32)
    query = urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": "identify email",
            "state": state,
            "prompt": "consent",
        }
    )
    response = RedirectResponse(url=f"{DISCORD_AUTHORIZE_URL}?{query}", status_code=302)
    _set_discord_state_cookie(response, state)
    return response


@router.get("/discord/callback")
async def discord_sign_in_callback(
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    client_id, client_secret, redirect_uri = _discord_config()
    expected_state = request.cookies.get(DISCORD_OAUTH_STATE_COOKIE, "")
    if error:
        response = _discord_error("Discord sign-in was cancelled or denied.")
        _clear_discord_state_cookie(response)
        return response
    if not code or not state or not expected_state or not hmac.compare_digest(state, expected_state):
        response = _discord_error("Discord sign-in expired or could not be verified. Please try again.")
        _clear_discord_state_cookie(response)
        return response
    if not client_id or not client_secret:
        response = _discord_error("Discord sign-in is not configured yet.")
        _clear_discord_state_cookie(response)
        return response

    await _rate_limit(db, "discord_oauth_callback", request, combined_limit=30, ip_limit=30)
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
            token_response = await client.post(
                DISCORD_TOKEN_URL,
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            token_response.raise_for_status()
            access_token = str(token_response.json().get("access_token") or "")
            if not access_token:
                raise ValueError("Discord did not return an access token")
            user_response = await client.get(
                DISCORD_ME_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            user_response.raise_for_status()
            discord_user = user_response.json()
    except (httpx.HTTPError, ValueError, json.JSONDecodeError):
        _audit(db, "discord_authentication_failure", "failed", request)
        await db.commit()
        response = _discord_error("Discord could not confirm this sign-in. Please try again.")
        _clear_discord_state_cookie(response)
        return response

    discord_id = str(discord_user.get("id") or "").strip()
    raw_email = str(discord_user.get("email") or "").strip()
    email_verified = bool(discord_user.get("verified"))
    if not discord_id or not raw_email or not email_verified:
        _audit(db, "discord_authentication_failure", "blocked", request, metadata={"reason": "verified_email_required"})
        await db.commit()
        response = _discord_error("A verified Discord email is required to sign in to Operly.")
        _clear_discord_state_cookie(response)
        return response
    try:
        email = normalize_email(raw_email)
    except ValueError:
        response = _discord_error("Discord returned an email address Operly could not verify.")
        _clear_discord_state_cookie(response)
        return response

    auth_identity = await db.scalar(
        select(AuthIdentity).where(
            AuthIdentity.provider == "discord",
            AuthIdentity.provider_subject == discord_id,
        )
    )
    new_account = False
    if auth_identity:
        user = await db.get(AppUser, auth_identity.user_id)
    else:
        user = await db.scalar(select(AppUser).where(AppUser.email == email))
        if user:
            db.add(
                AuthIdentity(
                    user_id=user.id,
                    provider="discord",
                    provider_subject=discord_id,
                    provider_email=email,
                )
            )
        else:
            new_account = True
            display_name = str(discord_user.get("global_name") or discord_user.get("username") or "Discord user")[:200]
            user = AppUser(
                email=email,
                display_name=display_name,
                password_hash=None,
                email_verified_at=__import__("datetime").datetime.utcnow(),
                active=True,
            )
            db.add(user)
            await db.flush()
            db.add(
                AuthIdentity(
                    user_id=user.id,
                    provider="discord",
                    provider_subject=discord_id,
                    provider_email=email,
                )
            )

    if not user or not user.active:
        await db.rollback()
        response = _discord_error("This Operly account is unavailable.")
        _clear_discord_state_cookie(response)
        return response

    display_name = str(discord_user.get("global_name") or discord_user.get("username") or user.display_name)[:200]
    try:
        await IdentityService.link_external_identity(
            db,
            user_id=user.id,
            provider="discord",
            external_user_id=discord_id,
            display_name=display_name,
            metadata={
                "username": discord_user.get("username"),
                "global_name": discord_user.get("global_name"),
                "avatar": discord_user.get("avatar"),
                "source": "oauth_sign_in",
            },
        )
    except IdentityLinkConflict:
        await db.rollback()
        response = _discord_error("That Discord account is already linked to another Operly user.")
        _clear_discord_state_cookie(response)
        return response

    # Discord sign-in always lands in Personal Operly. Workspace authority is
    # resolved later from the linked Discord identity plus the current server.
    _, session_secret, csrf_secret = await _create_session(db, request, user.id, None)
    _audit(
        db,
        "discord_authentication_success",
        "succeeded",
        request,
        user_id=user.id,
        metadata={"new_account": new_account, "scope": "personal"},
    )
    if new_account:
        _audit(db, "signup_completed", "succeeded", request, user_id=user.id, metadata={"provider": "discord", "scope": "personal"})
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        response = _discord_error("This Discord sign-in is already being completed. Please try again.")
        _clear_discord_state_cookie(response)
        return response

    response = RedirectResponse(url="/channels/@me", status_code=303)
    set_session_cookies(response, session_secret, csrf_secret)
    _clear_discord_state_cookie(response)
    return response


@router.post("/{provider}/link-code")
async def provider_link_code(
    provider: str,
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    name = _provider(provider)
    if name == "discord":
        raise HTTPException(410, "Discord pairing codes are retired. Use Sign in with Discord instead.")
    challenge = await IdentityLinkService.create_from_operly(
        db,
        user_id=auth.user.id,
        provider=name,
    )
    await db.commit()
    return {
        "provider": name,
        "code": challenge.code,
        "expires_at": challenge.expires_at.isoformat(),
        "instruction": f"Complete the {name} connector's Operly link flow with this one-time code.",
    }


@router.get("/{provider}/claim-info")
async def provider_claim_info(
    provider: str,
    token: str = Query(..., min_length=20, max_length=500),
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    name = _provider(provider)
    if name == "discord":
        raise HTTPException(410, "Discord web-claim links are retired. Use Sign in with Discord instead.")
    info = await IdentityLinkService.inspect_channel_token(db, provider=name, token=token)
    if not info:
        raise HTTPException(404, "Identity link is invalid or expired")
    return {
        "provider": name,
        "display_name": info["display_name"],
        "expires_at": info["expires_at"].isoformat(),
        "operly_user": auth.user.display_name,
        "confirmation_required": True,
    }


@router.post("/{provider}/claim")
async def provider_claim(
    provider: str,
    payload: ClaimIdentityInput,
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    name = _provider(provider)
    if name == "discord":
        raise HTTPException(410, "Discord web-claim links are retired. Use Sign in with Discord instead.")
    try:
        identity = await IdentityLinkService.claim_from_web(
            db,
            user_id=auth.user.id,
            provider=name,
            token=payload.token,
        )
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    await db.commit()
    return {
        "ok": True,
        "identity_id": identity.id,
        "provider": identity.provider,
        "display_name": identity.display_name,
    }


@router.delete("/{identity_id}")
async def unlink_identity(
    identity_id: str,
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    removed = await IdentityService.unlink_external_identity(
        db,
        user_id=auth.user.id,
        identity_id=identity_id,
    )
    if not removed:
        raise HTTPException(404, "External identity not found")
    await db.commit()
    return {"ok": True}
