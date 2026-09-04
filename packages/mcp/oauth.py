from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timedelta
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.principal_models import ClientGrant, McpAuthorizationCode


AUTHORIZATION_CODE_TTL_SECONDS = 600
ACCESS_TOKEN_TTL_SECONDS = 3600
REFRESH_TOKEN_TTL_SECONDS = 30 * 24 * 3600


class McpOAuthError(ValueError):
    pass


def _production() -> bool:
    return os.getenv("OPERLY_ENV", os.getenv("APP_ENV", "development")).lower() in {
        "production",
        "prod",
    }


def _secret() -> str:
    # MCP tokens get their own signing domain in production. MCP_OAUTH_SECRET is
    # retained as the documented compatibility name; OPERLY_MCP_TOKEN_SECRET is
    # the canonical explicit name going forward.
    dedicated = (
        os.getenv("OPERLY_MCP_TOKEN_SECRET", "").strip()
        or os.getenv("MCP_OAUTH_SECRET", "").strip()
    )
    if dedicated:
        if len(dedicated.encode("utf-8")) < 32:
            raise McpOAuthError("MCP token signing secret must contain at least 32 bytes")
        return dedicated
    if _production():
        raise McpOAuthError("A dedicated MCP token signing secret is required in production")
    value = (
        os.getenv("AUTH_TOKEN_PEPPER", "").strip()
        or os.getenv("SESSION_SECRET", "").strip()
    )
    if not value:
        raise McpOAuthError("MCP token signing secret is not configured")
    return value


def _serializer(kind: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_secret(), salt=f"operly:mcp:{kind}:v1")


def _issue_token(kind: str, payload: dict[str, Any]) -> str:
    body = dict(payload)
    body["token_kind"] = kind
    body["nonce"] = secrets.token_urlsafe(18)
    return _serializer(kind).dumps(body)


def _decode_token(kind: str, token: str, max_age: int) -> dict[str, Any]:
    try:
        value = _serializer(kind).loads(str(token or ""), max_age=max_age)
    except SignatureExpired as error:
        raise McpOAuthError(f"MCP {kind} token expired") from error
    except BadSignature as error:
        raise McpOAuthError(f"Invalid MCP {kind} token") from error
    if not isinstance(value, dict) or value.get("token_kind") != kind:
        raise McpOAuthError(f"Invalid MCP {kind} token payload")
    return value


def issue_access_token(payload: dict[str, Any]) -> str:
    return _issue_token("access", payload)


def decode_access_token(token: str) -> dict[str, Any]:
    return _decode_token("access", token, ACCESS_TOKEN_TTL_SECONDS)


def issue_refresh_token(payload: dict[str, Any]) -> str:
    return _issue_token("refresh", payload)


def decode_refresh_token(token: str) -> dict[str, Any]:
    return _decode_token("refresh", token, REFRESH_TOKEN_TTL_SECONDS)


def grant_refresh_generation(grant: ClientGrant) -> str:
    stamp = grant.updated_at or grant.created_at
    return stamp.isoformat(timespec="microseconds")


async def consume_refresh_token(
    db: AsyncSession,
    token: str,
    *,
    client_id: str,
    resource: str,
) -> tuple[dict[str, Any], ClientGrant]:
    """Atomically redeem and rotate one refresh-token generation.

    The ClientGrant row is the durable refresh-token family record, avoiding a new
    token table/migration while still providing one-time redemption across replicas.
    Any replay after successful rotation observes a different generation and fails.
    """

    payload = decode_refresh_token(token)
    if payload.get("client_id") != client_id or payload.get("resource") != resource:
        raise McpOAuthError("Refresh token client or resource mismatch")
    grant_id = str(payload.get("grant_id") or "")
    grant = await db.scalar(
        select(ClientGrant)
        .where(ClientGrant.id == grant_id, ClientGrant.client_id == client_id)
        .with_for_update()
    )
    now = datetime.utcnow()
    if grant is None or grant.status != "active" or grant.tenant_id is None:
        raise McpOAuthError("MCP grant is no longer active")
    if grant.expires_at is not None and grant.expires_at <= now:
        raise McpOAuthError("MCP grant has expired")
    if grant.principal_id != payload.get("principal_id") or grant.tenant_id != payload.get("tenant_id"):
        raise McpOAuthError("MCP refresh token identity mismatch")

    supplied_generation = str(payload.get("refresh_generation") or "")
    current_generation = grant_refresh_generation(grant)
    if not supplied_generation or not hmac.compare_digest(supplied_generation, current_generation):
        raise McpOAuthError("MCP refresh token was already used or superseded")

    # Rotate before issuing the replacement token. The row lock makes two
    # concurrent redeemers serialize; only the first can match this generation.
    grant.updated_at = now
    await db.flush()
    return payload, grant


def pkce_s256(code_verifier: str) -> str:
    digest = hashlib.sha256(str(code_verifier or "").encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _code_hash(code: str) -> str:
    return hashlib.sha256(str(code or "").encode("utf-8")).hexdigest()


async def issue_authorization_code(
    db: AsyncSession,
    *,
    grant_id: str,
    principal_id: str,
    tenant_id: str,
    client_id: str,
    redirect_uri: str,
    scopes: list[str],
    code_challenge: str,
    resource: str,
) -> str:
    raw = secrets.token_urlsafe(48)
    now = datetime.utcnow()
    db.add(
        McpAuthorizationCode(
            code_hash=_code_hash(raw),
            grant_id=grant_id,
            principal_id=principal_id,
            tenant_id=tenant_id,
            client_id=client_id,
            redirect_uri=redirect_uri,
            scopes_json=json.dumps(sorted(set(scopes)), separators=(",", ":")),
            code_challenge=code_challenge,
            resource=resource,
            expires_at=now + timedelta(seconds=AUTHORIZATION_CODE_TTL_SECONDS),
            created_at=now,
        )
    )
    await db.flush()
    return raw


async def consume_authorization_code(
    db: AsyncSession,
    code: str,
    *,
    client_id: str,
    redirect_uri: str,
    code_verifier: str,
    resource: str,
) -> dict[str, Any]:
    # Lock the one-time code row so two concurrent token exchanges cannot both
    # redeem the same authorization code on production PostgreSQL.
    row = await db.scalar(
        select(McpAuthorizationCode)
        .where(McpAuthorizationCode.code_hash == _code_hash(code))
        .with_for_update()
    )
    now = datetime.utcnow()
    if row is None:
        raise McpOAuthError("Invalid MCP authorization code")
    if row.consumed_at is not None:
        raise McpOAuthError("MCP authorization code was already used")
    if row.expires_at <= now:
        raise McpOAuthError("MCP authorization code expired")
    if row.client_id != client_id or row.redirect_uri != redirect_uri or row.resource != resource:
        raise McpOAuthError("MCP authorization code binding mismatch")
    if not 43 <= len(str(code_verifier or "")) <= 128:
        raise McpOAuthError("Invalid MCP PKCE verifier length")
    if not hmac.compare_digest(pkce_s256(code_verifier), row.code_challenge):
        raise McpOAuthError("MCP PKCE verification failed")

    row.consumed_at = now
    await db.flush()
    try:
        scopes = [str(item) for item in json.loads(row.scopes_json or "[]")]
    except (TypeError, json.JSONDecodeError):
        scopes = []
    return {
        "grant_id": row.grant_id,
        "principal_id": row.principal_id,
        "tenant_id": row.tenant_id,
        "client_id": row.client_id,
        "resource": row.resource,
        "scopes": scopes,
    }
