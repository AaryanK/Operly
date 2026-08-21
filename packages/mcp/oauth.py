import base64
import hashlib
import os
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer


AUTHORIZATION_CODE_TTL_SECONDS = 300
ACCESS_TOKEN_TTL_SECONDS = 3600
REFRESH_TOKEN_TTL_SECONDS = 60 * 60 * 24 * 30


class McpOAuthError(ValueError):
    pass


def _secret() -> str:
    value = os.getenv("MCP_OAUTH_SECRET", "").strip() or os.getenv("SESSION_SECRET", "").strip()
    if not value:
        raise McpOAuthError("MCP OAuth signing secret is not configured")
    return value


def _serializer(kind: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_secret(), salt=f"operly-mcp-{kind}-v1")


def pkce_s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def issue_authorization_code(
    *,
    grant_id: str,
    principal_id: str,
    tenant_id: str,
    client_id: str,
    redirect_uri: str,
    scopes: list[str],
    code_challenge: str,
) -> str:
    return _serializer("authorization-code").dumps(
        {
            "grant_id": grant_id,
            "principal_id": principal_id,
            "tenant_id": tenant_id,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scopes": sorted(set(scopes)),
            "code_challenge": code_challenge,
        }
    )


def consume_authorization_code(
    code: str,
    *,
    client_id: str,
    redirect_uri: str,
    code_verifier: str,
) -> dict[str, Any]:
    try:
        payload = _serializer("authorization-code").loads(
            code,
            max_age=AUTHORIZATION_CODE_TTL_SECONDS,
        )
    except SignatureExpired as exc:
        raise McpOAuthError("Authorization code expired") from exc
    except BadSignature as exc:
        raise McpOAuthError("Invalid authorization code") from exc
    if payload.get("client_id") != client_id or payload.get("redirect_uri") != redirect_uri:
        raise McpOAuthError("Authorization code client mismatch")
    if not code_verifier or pkce_s256(code_verifier) != payload.get("code_challenge"):
        raise McpOAuthError("PKCE verification failed")
    return payload


def issue_access_token(payload: dict[str, Any]) -> str:
    data = dict(payload)
    data["token_type"] = "access"
    return _serializer("access-token").dumps(data)


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        payload = _serializer("access-token").loads(token, max_age=ACCESS_TOKEN_TTL_SECONDS)
    except SignatureExpired as exc:
        raise McpOAuthError("Access token expired") from exc
    except BadSignature as exc:
        raise McpOAuthError("Invalid access token") from exc
    if payload.get("token_type") != "access":
        raise McpOAuthError("Invalid access token")
    return payload


def issue_refresh_token(payload: dict[str, Any]) -> str:
    data = dict(payload)
    data["token_type"] = "refresh"
    return _serializer("refresh-token").dumps(data)


def decode_refresh_token(token: str) -> dict[str, Any]:
    try:
        payload = _serializer("refresh-token").loads(token, max_age=REFRESH_TOKEN_TTL_SECONDS)
    except SignatureExpired as exc:
        raise McpOAuthError("Refresh token expired") from exc
    except BadSignature as exc:
        raise McpOAuthError("Invalid refresh token") from exc
    if payload.get("token_type") != "refresh":
        raise McpOAuthError("Invalid refresh token")
    return payload
