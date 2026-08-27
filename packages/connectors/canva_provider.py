from __future__ import annotations

import base64
import hashlib
import os
import secrets
from datetime import datetime, timezone
from urllib.parse import urlencode

import aiohttp

from packages.connectors.secrets import read_secret, update_secret


CANVA_AUTHORIZE_URL = "https://www.canva.com/api/oauth/authorize"
CANVA_TOKEN_URL = "https://api.canva.com/rest/v1/oauth/token"
CANVA_API_BASE = "https://api.canva.com/rest/v1"

CANVA_SCOPES = [
    "profile:read",
    "design:meta:read",
    "design:content:read",
    "design:content:write",
    "asset:read",
    "asset:write",
    "folder:read",
]


class CanvaProviderRejected(RuntimeError):
    pass


def redirect_uri() -> str:
    return (
        os.getenv("CANVA_OAUTH_REDIRECT_URI")
        or os.getenv("CANVA_REDIRECT_URI")
        or os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
        + "/api/connectors/canva/callback"
    )


def require_configuration() -> tuple[str, str]:
    client_id = os.getenv("CANVA_CLIENT_ID", "").strip()
    client_secret = os.getenv("CANVA_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise RuntimeError("Canva OAuth is not configured")
    return client_id, client_secret


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


def authorization_url(*, state: str, code_challenge: str, scopes: list[str] | None = None) -> str:
    client_id, _ = require_configuration()
    return CANVA_AUTHORIZE_URL + "?" + urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri(),
            "response_type": "code",
            "scope": " ".join(scopes or CANVA_SCOPES),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
    )


def normalize_scopes(value) -> list[str]:
    if isinstance(value, str):
        values = value.split()
    elif isinstance(value, (list, tuple, set)):
        values = [str(item) for item in value]
    else:
        values = []
    return list(dict.fromkeys(item.strip() for item in values if item and item.strip()))


def canva_capabilities(scopes: set[str]) -> list[str]:
    capabilities: list[str] = []
    if "profile:read" in scopes:
        capabilities.append("canva.get_profile")
    if "design:meta:read" in scopes:
        capabilities.extend(["canva.list_designs", "canva.get_design"])
    if "design:content:read" in scopes:
        capabilities.append("canva.export_design")
    if "design:content:write" in scopes:
        capabilities.append("canva.create_design")
    if "asset:read" in scopes:
        capabilities.append("canva.list_assets")
    if "asset:write" in scopes:
        capabilities.append("canva.upload_asset")
    if "folder:read" in scopes:
        capabilities.append("canva.list_folders")
    return list(dict.fromkeys(capabilities))


async def _token_request(data: dict) -> dict:
    client_id, client_secret = require_configuration()
    timeout = aiohttp.ClientTimeout(total=20)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                CANVA_TOKEN_URL,
                data=data,
                auth=aiohttp.BasicAuth(client_id, client_secret),
            ) as response:
                body = await response.json(content_type=None)
                if response.status != 200 or not isinstance(body, dict):
                    raise CanvaProviderRejected(
                        f"Canva rejected OAuth credentials ({response.status})"
                    )
                return body
    except CanvaProviderRejected:
        raise
    except (aiohttp.ClientError, TimeoutError) as error:
        raise CanvaProviderRejected("Canva OAuth request failed") from error


async def exchange_code(*, code: str, code_verifier: str) -> dict:
    return await _token_request(
        {
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri(),
        }
    )


async def refresh_tokens(refresh_token: str) -> dict:
    if not refresh_token:
        raise CanvaProviderRejected(
            "Canva authorization expired and no refresh token is available"
        )
    return await _token_request(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
    )


async def request_json(
    method: str,
    path: str,
    token: str,
    *,
    payload: dict | None = None,
    params: dict | None = None,
    expected_statuses: tuple[int, ...] = (200, 201),
) -> dict:
    url = path if path.startswith("https://") else f"{CANVA_API_BASE}/{path.lstrip('/')}"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.request(
                method,
                url,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=payload,
                params=params,
            ) as response:
                body = {} if response.status == 204 else await response.json(content_type=None)
                if response.status not in expected_statuses:
                    raise CanvaProviderRejected(
                        f"Canva rejected the request ({response.status})"
                    )
                return body if isinstance(body, dict) else {}
    except CanvaProviderRejected:
        raise
    except (aiohttp.ClientError, TimeoutError) as error:
        raise CanvaProviderRejected("Canva request failed") from error


async def get_identity(token: str) -> dict:
    user_body = await request_json("GET", "/users/me", token)
    profile_body = await request_json("GET", "/users/me/profile", token)

    team_user = user_body.get("team_user") or {}
    profile = profile_body.get("profile") or {}
    user_id = str(team_user.get("user_id") or user_body.get("user_id") or "").strip()
    if not user_id:
        raise CanvaProviderRejected("Canva did not return a user identity")
    return {
        "user_id": user_id,
        "team_id": str(team_user.get("team_id") or user_body.get("team_id") or "").strip() or None,
        "display_name": str(profile.get("display_name") or "Canva account").strip() or "Canva account",
    }


def finalize_tokens(tokens: dict, *, fallback_scopes: list[str] | None = None) -> tuple[dict, list[str]]:
    result = dict(tokens)
    result["expires_at"] = datetime.now(timezone.utc).timestamp() + int(
        result.get("expires_in", 14400)
    )
    scopes = normalize_scopes(result.get("scope")) or list(fallback_scopes or CANVA_SCOPES)
    result["scope"] = " ".join(scopes)
    return result, scopes


async def access_token(db, connector) -> str:
    secret = await read_secret(db, connector.tenant_id, connector.credential_reference)
    if float(secret.get("expires_at", 0)) > datetime.now(timezone.utc).timestamp() + 60:
        return secret["access_token"]

    refreshed = await refresh_tokens(str(secret.get("refresh_token") or ""))
    # Canva rotates refresh tokens. Preserve the old token only if a response
    # unexpectedly omits the new one.
    if not refreshed.get("refresh_token") and secret.get("refresh_token"):
        refreshed["refresh_token"] = secret["refresh_token"]
    refreshed, _ = finalize_tokens(
        refreshed,
        fallback_scopes=normalize_scopes(secret.get("scope")) or CANVA_SCOPES,
    )
    await update_secret(db, connector.tenant_id, connector.credential_reference, refreshed)
    return refreshed["access_token"]
