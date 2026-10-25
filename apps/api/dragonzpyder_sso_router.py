"""Opt-in DragonZpyder SSO relying party.

Disabled until the DragonZpyder identity issuer and existing-account mappings
are migrated and tested. No email-based auto-linking or auto-provisioning.
"""
from __future__ import annotations

import hmac
import os
import secrets
from urllib.parse import urlencode, quote

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from itsdangerous import URLSafeTimedSerializer, BadSignature
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.auth_cookies import production, set_session_cookies
from apps.api.dependencies import get_db
from apps.api.session import _audit, _create_session, _first_membership
from packages.database.models import AppUser, AuthIdentity
from packages.security.dragonzpyder_oidc import (
    ISSUER, AUTHORIZE_URL, TOKEN_URL, JWKS_URL,
    DragonZpyderIdentityError, create_pkce, verified_id_token,
)

router = APIRouter(prefix="/api/identities/dragonzpyder", tags=["authentication"])
STATE_COOKIE = "operly_dragonzpyder_sso"


def enabled() -> bool:
    return os.getenv("DRAGONZPYDER_SSO_ENABLED", "false").lower() == "true"


def conf() -> tuple[str, str, str, URLSafeTimedSerializer]:
    if not enabled():
        raise DragonZpyderIdentityError("DragonZpyder single sign-on is not yet enabled")
    client = os.getenv("DRAGONZPYDER_SSO_CLIENT_ID", "").strip()
    secret = os.getenv("DRAGONZPYDER_SSO_CLIENT_SECRET", "").strip()
    signing = os.getenv("DRAGONZPYDER_SSO_STATE_SECRET", "").strip()
    public = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if not client or not secret or len(signing) < 32:
        raise DragonZpyderIdentityError("DragonZpyder SSO credentials are not configured")
    if production() and not public.startswith("https://"):
        raise DragonZpyderIdentityError("An HTTPS callback address is required")
    if not public.startswith(("https://", "http://")):
        raise DragonZpyderIdentityError("Invalid Operly callback origin")
    redirect_uri = public + "/api/identities/dragonzpyder/callback"
    return client, secret, redirect_uri, URLSafeTimedSerializer(signing, salt="operly-dz-oidc-v1")


def error_redirect() -> RedirectResponse:
    response = RedirectResponse("/login?dragonzpyder_error=sign_in_unavailable", status_code=303)
    response.delete_cookie(STATE_COOKIE, path="/")
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/start")
async def start():
    try:
        client, _, redirect_uri, serializer = conf()
    except DragonZpyderIdentityError:
        return error_redirect()
    verifier, challenge = create_pkce()
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    payload = serializer.dumps({"state": state, "nonce": nonce, "verifier": verifier})
    url = AUTHORIZE_URL + "?" + urlencode({
        "response_type": "code", "client_id": client, "redirect_uri": redirect_uri,
        "scope": "openid email profile", "state": state,
        "nonce": nonce, "code_challenge": challenge, "code_challenge_method": "S256",
    })
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(STATE_COOKIE, payload, max_age=300, httponly=True, secure=production(),
                        samesite="lax", path="/")
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/callback")
async def callback(request: Request, db: AsyncSession = Depends(get_db)):
    # This GET callback is protected by a one-time IdP code and signed, time-bound state.
    # The cookie is host-only so no other DragonZpyder subdomain can set Operly's state.
    try:
        client, client_secret, redirect_uri, signer = conf()
        cookie = request.cookies.get(STATE_COOKIE, "")
        payload = signer.loads(cookie, max_age=300)
        state, nonce, verifier = payload["state"], payload["nonce"], payload["verifier"]
        incoming = request.query_params.get("state", "")
        code = request.query_params.get("code", "")
        if request.query_params.get("error") or not incoming or not code or not hmac.compare_digest(state, incoming):
            raise DragonZpyderIdentityError("Invalid SSO state or code")
        if not (32 <= len(verifier) <= 128 and 16 <= len(nonce) <= 128):
            raise DragonZpyderIdentityError("Invalid SSO verifier")
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=False) as http:
            response = await http.post(TOKEN_URL, data={
                "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
                "code_verifier": verifier,
            }, auth=(client, client_secret), headers={"Accept": "application/json"})
            response.raise_for_status()
            token = response.json().get("id_token")
            if not token:
                raise DragonZpyderIdentityError("Identity issuer returned no ID token")
            jwks_response = await http.get(JWKS_URL, headers={"Accept": "application/json"})
            jwks_response.raise_for_status()
            claims = verified_id_token(token, jwks_response.json(), audience=client, nonce=nonce)
        identity = await db.scalar(select(AuthIdentity).where(
            AuthIdentity.provider == "dragonzpyder", AuthIdentity.provider_subject == claims["sub"]
        ))
        # Intentionally reject unknown identities. Migration must link verified
        # DragonZpyder subjects to existing accounts before SSO is enabled.
        if not identity:
            raise DragonZpyderIdentityError("The DragonZpyder account has not been linked")
        user = await db.get(AppUser, identity.user_id)
        if not user or not user.active:
            raise DragonZpyderIdentityError("Account unavailable")
        membership = await _first_membership(db, user.id)
        tenant_id = membership.tenant_id if membership else None
        _, session_secret, csrf_secret = await _create_session(db, request, user.id, tenant_id)
        _audit(db, "dragonzpyder_sso_success", "succeeded", request,
               user_id=user.id, tenant_id=tenant_id, metadata={"provider": "dragonzpyder"})
        await db.commit()
        success = RedirectResponse(url="/channels/@me" if tenant_id else "/account",
                                   status_code=303)
        set_session_cookies(success, session_secret, csrf_secret)
        success.delete_cookie(STATE_COOKIE, path="/")
        success.headers["Cache-Control"] = "no-store"
        return success
    except (DragonZpyderIdentityError, BadSignature, httpx.HTTPError,
            KeyError, TypeError, ValueError):
        await db.rollback()
        return error_redirect()
