"""Opt-in OIDC browser client for Operly, preserving existing local sessions.

Only a verified DragonZpyder ID token and an audience-scoped access token may
resolve a pre-activated subject mapping. No email-based linking, agent/runtime
credentials or broad cookies. Operly still owns TenantMember authorization.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import re
import secrets
from datetime import datetime, timedelta
from urllib.parse import urlencode, urlsplit

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.auth_cookies import set_session_cookies
from apps.api.dependencies import get_db
from apps.api.security import hash_token
from apps.api.session import _create_session, _first_membership
from packages.database.federated_login_models import FederatedLoginAttempt
from packages.security.federated_identity import FederationDenied, resolve_federated_user
from packages.security.federated_token import FederatedTokenDenied, verify_federated_access_token

router = APIRouter(tags=["authentication"])
_LOG = logging.getLogger("operly.federated_login")
COOKIE = "__Host-operly_oidc"
SCOPES = "openid operly:access"
MAX_AGE = 300


class SSORejected(PermissionError):
    pass


def _settings():
    if os.getenv("OPERLY_DRAGONZPYDER_SSO_ENABLED") != "1":
        raise HTTPException(404, "Not found")
    if (os.getenv("OPERLY_ENV", os.getenv("APP_ENV", "")).lower() in ("production", "prod")
            and os.getenv("OPERLY_DRAGONZPYDER_SSO_PRODUCTION") != "1"):
        raise HTTPException(503, "SSO is not approved for production")
    issuer = os.getenv("OPERLY_FEDERATED_ISSUER", "").rstrip("/")
    base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
    client = os.getenv("OPERLY_FEDERATED_CLIENT_ID", "")
    resource = os.getenv("OPERLY_FEDERATED_RESOURCE_AUDIENCE", "")
    kid = os.getenv("OPERLY_FEDERATED_SIGNING_KID", "")
    key_text = os.getenv("OPERLY_FEDERATED_PUBLIC_KEY_PEM", "").replace("\\n", "\n")
    try:
        p = urlsplit(issuer)
        b = urlsplit(base)
        valid = (p.scheme == "https" and p.hostname and not p.username
                 and not p.password and p.path in ("", "/") and not p.query and not p.fragment
                 and b.scheme == "https" and b.hostname and b.path in ("", "/")
                 and not b.query and not b.fragment and not b.username and not b.password
                 and p.port in (None, 443) and b.port in (None, 443)
                 and re.fullmatch(r"[A-Za-z0-9_-]{3,100}", client) is not None
                 and 1 <= len(resource) <= 150 and bool(kid))
        public = serialization.load_pem_public_key(key_text.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise HTTPException(503, "SSO configuration unavailable") from exc
    if not valid or not isinstance(public, rsa.RSAPublicKey) or public.key_size < 3072:
        raise HTTPException(503, "SSO configuration unavailable")
    return issuer, base + "/api/auth/dragonzpyder/callback", client, resource, kid, public


def federated_login_available() -> bool:
    """Expose only the local readiness gate, never keys, user data or token material."""
    try:
        _settings()
    except HTTPException:
        return False
    return True


def _web_settings():
    # DragonZpyder Web is a distinct OIDC relying party, not a shared cookie.
    # Only the existing reviewed first-party https origin may be used.
    issuer, _, existing_client, audience, kid, public = _settings()
    if os.getenv("OPERLY_DRAGONZPYDER_WEB_SSO_ENABLED") != "1":
        raise HTTPException(404, "Not found")
    client_id = os.getenv("OPERLY_FEDERATED_DRAGONZPYDER_WEB_CLIENT_ID", "")
    if (re.fullmatch(r"[A-Za-z0-9_-]{3,100}", client_id) is None
            or client_id == existing_client):
        raise HTTPException(503, "Web relying party not configured")
    return (issuer,
            "https://dragonzpyder.xyz/api/auth/dragonzpyder/web/callback",
            client_id, audience, kid, public)


def dragonzpyder_web_login_available() -> bool:
    try:
        _web_settings()
    except HTTPException:
        return False
    return True


def _oidc_verifier() -> str:
    return secrets.token_urlsafe(48)


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")


def verify_callback_tokens(
    *, access_token: str, id_token: str, nonce: str,
    issuer: str, client_id: str, audience: str, key_id: str, public_key,
) -> str:
    if (not isinstance(id_token, str) or not 10 < len(id_token) <= 8192
            or not isinstance(access_token, str) or not 10 < len(access_token) <= 8192):
        raise SSORejected("Missing signed tokens")
    try:
        header = jwt.get_unverified_header(id_token)
        if (header.get("alg") != "RS256" or header.get("typ") != "JWT"
                or header.get("kid") != key_id):
            raise SSORejected("Unsupported identity signing key")
        claims = jwt.decode(
            id_token, public_key, algorithms=["RS256"],
            issuer=issuer, audience=client_id, leeway=10,
            options={"require": ["iss", "sub", "aud", "exp", "iat", "nonce", "at_hash"]},
        )
        if (claims.get("token_use") != "id"
                or not isinstance(claims.get("sub"), str)
                or not 1 <= len(claims["sub"]) <= 160
                or not isinstance(claims.get("nonce"), str)
                or not hmac.compare_digest(claims["nonce"], nonce)
                or type(claims.get("iat")) is not int
                or type(claims.get("exp")) is not int
                or not 0 < claims["exp"] - claims["iat"] <= 300
                or claims.get("aud") != client_id):
            raise SSORejected("OIDC identity validation failed")
        expected_at_hash = base64.urlsafe_b64encode(
            hashlib.sha256(access_token.encode("ascii")).digest()[:16]
        ).rstrip(b"=").decode("ascii")
        if not isinstance(claims.get("at_hash"), str) or not hmac.compare_digest(
            expected_at_hash, claims["at_hash"]
        ):
            raise SSORejected("Access token binding failed")
        access_header = jwt.get_unverified_header(access_token)
        if access_header.get("kid") != key_id:
            raise SSORejected("Resource access key mismatch")
        verified_issuer, verified_subject = verify_federated_access_token(
            access_token, public_key=public_key, issuer=issuer,
            audience=audience, required_scope="operly:access",
        )
    except (jwt.PyJWTError, UnicodeError, ValueError, TypeError, FederatedTokenDenied) as exc:
        raise SSORejected("Invalid OIDC proof") from exc
    if verified_issuer != issuer or verified_subject != claims["sub"]:
        raise SSORejected("Access and ID token subject mismatch")
    return verified_subject


@router.get("/api/auth/dragonzpyder/start", include_in_schema=False)
async def start_federated_login(db: AsyncSession = Depends(get_db)):
    return await _start_login(_settings(), db)


@router.get("/api/auth/dragonzpyder/web/start", include_in_schema=False)
async def start_dragonzpyder_web_login(db: AsyncSession = Depends(get_db)):
    return await _start_login(_web_settings(), db)


async def _start_login(settings, db: AsyncSession):
    issuer, redirect, client_id, _, _, _ = settings
    state = secrets.token_urlsafe(48)
    binding = secrets.token_urlsafe(48)
    nonce = secrets.token_urlsafe(40)
    verifier = _oidc_verifier()
    db.add(FederatedLoginAttempt(
        state_hash=hash_token(state, purpose="oidc:state:" + client_id),
        binding_hash=hash_token(binding, purpose="oidc:binding"),
        verifier=verifier, nonce=nonce, expires_at=datetime.utcnow() + timedelta(seconds=MAX_AGE),
    ))
    await db.commit()
    query = urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect,
        "scope": SCOPES,
        "state": state,
        "nonce": nonce,
        "code_challenge_method": "S256",
        "code_challenge": _challenge(verifier),
    })
    response = RedirectResponse(issuer + "/identity/v1/authorize?" + query, status_code=302, headers={
        "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
    })
    response.set_cookie(COOKIE, binding, max_age=MAX_AGE, secure=True, httponly=True,
                        samesite="lax", path="/")
    return response


@router.get("/api/auth/dragonzpyder/callback", include_in_schema=False)
async def complete_federated_login(request: Request, db: AsyncSession = Depends(get_db)):
    return await _complete_login(_settings(), "/", request, db)


@router.get("/api/auth/dragonzpyder/web/callback", include_in_schema=False)
async def complete_dragonzpyder_web_login(
    request: Request, db: AsyncSession = Depends(get_db),
):
    return await _complete_login(_web_settings(), "/app", request, db)


async def _complete_login(settings, success_path: str, request: Request, db: AsyncSession):
    issuer, redirect, client_id, audience, key_id, public = settings
    params = request.query_params
    if (len(params.multi_items()) != len(set(params.keys()))
            or set(params.keys()) != {"code", "state"}):
        raise HTTPException(400, "Invalid callback")
    state = params.get("state", "")
    code = params.get("code", "")
    binding = request.cookies.get(COOKIE, "")
    if not (32 <= len(state) <= 128 and 20 <= len(code) <= 128
            and 32 <= len(binding) <= 128):
        raise HTTPException(400, "Invalid callback")
    row = await db.scalar(
        select(FederatedLoginAttempt).where(
            FederatedLoginAttempt.state_hash == hash_token(state, purpose="oidc:state:" + client_id),
        ).with_for_update()
    )
    if (row is None or row.consumed_at is not None
            or row.expires_at <= datetime.utcnow()
            or not hmac.compare_digest(
                row.binding_hash, hash_token(binding, purpose="oidc:binding")
            )):
        raise HTTPException(403, "SSO state expired or invalid")
    row.consumed_at = datetime.utcnow()
    verifier, nonce = row.verifier, row.nonce
    # Burn state before any outgoing network request; failures are retried by
    # starting a NEW login, never by replaying the callback.
    await db.commit()
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=False, trust_env=False) as client:
            result = await client.post(
                issuer + "/identity/v1/token",
                data={"grant_type": "authorization_code", "code": code,
                      "client_id": client_id, "redirect_uri": redirect,
                      "code_verifier": verifier},
                headers={"Accept": "application/json"},
            )
        if result.status_code != 200 or len(result.content) > 16384:
            raise SSORejected("SSO token exchange rejected")
        body = result.json()
        if not isinstance(body, dict):
            raise SSORejected("Invalid token response")
        subject = verify_callback_tokens(
            access_token=body.get("access_token"), id_token=body.get("id_token"),
            nonce=nonce, issuer=issuer, client_id=client_id,
            audience=audience, key_id=key_id, public_key=public,
        )
    except (httpx.HTTPError, ValueError, TypeError, SSORejected):
        raise HTTPException(403, "SSO authentication rejected") from None
    try:
        user = await resolve_federated_user(
            db, verified_issuer=issuer, verified_subject=subject,
        )
    except FederationDenied:
        raise HTTPException(403, "SSO identity not linked") from None
    membership = await _first_membership(db, user.id)
    _, secret, csrf = await _create_session(
        db, request, user.id, membership.tenant_id if membership else None,
    )
    await db.commit()
    response = RedirectResponse(success_path, status_code=303, headers={
        "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
    })
    set_session_cookies(response, secret, csrf)
    response.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="lax")
    # Only fixed public audit labels; never log identity, tokens, state or credentials.
    _LOG.info("oidc_login_success relying_party=%s", "dragonzpyder_web" if success_path == "/app" else "operly")
    return response
