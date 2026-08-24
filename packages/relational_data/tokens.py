"""Short-lived signed grants for generated-app capability sidecars.

Grants authorize semantic Operly capabilities; they are not database/provider
credentials and must never be written into generated source or binding files.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass

from packages.relational_data.contracts import RELATIONAL_CAPABILITY_ID


class BindingGrantError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class BindingGrantClaims:
    workspace_id: str
    application_id: str
    capability_id: str
    scopes: tuple[str, ...]
    resources: tuple[str, ...]
    expires_at: int


def _secret(explicit: str | None = None) -> bytes:
    value = explicit if explicit is not None else os.getenv("OPERLY_RUNTIME_BINDING_SECRET", "")
    if len(value.encode("utf-8")) < 32:
        raise BindingGrantError("OPERLY_RUNTIME_BINDING_SECRET must contain at least 32 bytes")
    return value.encode("utf-8")


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * ((4 - len(value) % 4) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding)
    except Exception as error:
        raise BindingGrantError("Binding grant encoding is invalid") from error


def issue_capability_grant(
    workspace_id: str,
    application_id: str,
    *,
    capability_id: str,
    scopes: tuple[str, ...],
    allowed_scopes: frozenset[str],
    resources: tuple[str, ...] = (),
    ttl_seconds: int = 3600,
    secret: str | None = None,
) -> str:
    if not workspace_id or not application_id or not capability_id:
        raise BindingGrantError("Binding grants require workspace, application and capability scope")
    normalized = tuple(sorted(set(scopes)))
    normalized_resources = tuple(sorted(set(str(item) for item in resources if str(item))))
    if not normalized or any(scope not in allowed_scopes for scope in normalized):
        raise BindingGrantError("Binding grant scopes are invalid")
    now = int(time.time())
    payload = {
        "v": 1,
        "workspaceId": workspace_id,
        "applicationId": application_id,
        "capabilityId": capability_id,
        "scopes": list(normalized),
        "resources": list(normalized_resources),
        "iat": now,
        "exp": now + max(60, min(int(ttl_seconds), 14400)),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    encoded = _b64encode(raw)
    signature = hmac.new(_secret(secret), encoded.encode("ascii"), hashlib.sha256).digest()
    return encoded + "." + _b64encode(signature)


def verify_capability_grant(
    token: str,
    *,
    capability_id: str,
    required_scope: str,
    allowed_scopes: frozenset[str],
    required_resource: str | None = None,
    secret: str | None = None,
    now: int | None = None,
) -> BindingGrantClaims:
    try:
        encoded, supplied = token.split(".", 1)
    except ValueError as error:
        raise BindingGrantError("Binding grant is malformed") from error
    expected = hmac.new(_secret(secret), encoded.encode("ascii"), hashlib.sha256).digest()
    actual = _b64decode(supplied)
    if not hmac.compare_digest(expected, actual):
        raise BindingGrantError("Binding grant signature is invalid")
    try:
        payload = json.loads(_b64decode(encoded))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BindingGrantError("Binding grant payload is invalid") from error
    if not isinstance(payload, dict) or payload.get("v") != 1:
        raise BindingGrantError("Binding grant version is unsupported")
    if payload.get("capabilityId") != capability_id:
        raise BindingGrantError("Binding grant capability is invalid")
    workspace_id = str(payload.get("workspaceId") or "")
    application_id = str(payload.get("applicationId") or "")
    scopes = tuple(str(item) for item in (payload.get("scopes") or ()))
    resources = tuple(str(item) for item in (payload.get("resources") or ()))
    expires_at = int(payload.get("exp") or 0)
    if not workspace_id or not application_id:
        raise BindingGrantError("Binding grant scope is incomplete")
    if any(scope not in allowed_scopes for scope in scopes) or required_scope not in scopes:
        raise BindingGrantError("Binding grant does not authorize this operation")
    if required_resource is not None and required_resource not in resources:
        raise BindingGrantError("Binding grant does not authorize this resource")
    current = int(time.time()) if now is None else int(now)
    if expires_at <= current:
        raise BindingGrantError("Binding grant has expired")
    return BindingGrantClaims(
        workspace_id=workspace_id,
        application_id=application_id,
        capability_id=capability_id,
        scopes=scopes,
        resources=resources,
        expires_at=expires_at,
    )


def issue_binding_grant(
    workspace_id: str,
    application_id: str,
    *,
    scopes: tuple[str, ...],
    ttl_seconds: int = 3600,
    secret: str | None = None,
) -> str:
    return issue_capability_grant(
        workspace_id,
        application_id,
        capability_id=RELATIONAL_CAPABILITY_ID,
        scopes=scopes,
        allowed_scopes=frozenset({"read", "write", "migrate"}),
        ttl_seconds=ttl_seconds,
        secret=secret,
    )


def verify_binding_grant(
    token: str,
    *,
    required_scope: str,
    secret: str | None = None,
    now: int | None = None,
) -> BindingGrantClaims:
    return verify_capability_grant(
        token,
        capability_id=RELATIONAL_CAPABILITY_ID,
        required_scope=required_scope,
        allowed_scopes=frozenset({"read", "write", "migrate"}),
        secret=secret,
        now=now,
    )


__all__ = [
    "BindingGrantClaims",
    "BindingGrantError",
    "issue_capability_grant",
    "verify_capability_grant",
    "issue_binding_grant",
    "verify_binding_grant",
]
