"""Short-lived signed grants for generated-app capability sidecars.

These grants authorize an Operly capability scope; they are not database/provider
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


def issue_binding_grant(
    workspace_id: str,
    application_id: str,
    *,
    scopes: tuple[str, ...],
    ttl_seconds: int = 3600,
    secret: str | None = None,
) -> str:
    if not workspace_id or not application_id:
        raise BindingGrantError("Binding grants require workspace and application scope")
    allowed = {"read", "write", "migrate"}
    normalized = tuple(sorted(set(scopes)))
    if not normalized or any(scope not in allowed for scope in normalized):
        raise BindingGrantError("Binding grant scopes are invalid")
    now = int(time.time())
    payload = {
        "v": 1,
        "workspaceId": workspace_id,
        "applicationId": application_id,
        "capabilityId": RELATIONAL_CAPABILITY_ID,
        "scopes": list(normalized),
        "iat": now,
        "exp": now + max(60, min(int(ttl_seconds), 14400)),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    encoded = _b64encode(raw)
    signature = hmac.new(_secret(secret), encoded.encode("ascii"), hashlib.sha256).digest()
    return encoded + "." + _b64encode(signature)


def verify_binding_grant(
    token: str,
    *,
    required_scope: str,
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
    if payload.get("capabilityId") != RELATIONAL_CAPABILITY_ID:
        raise BindingGrantError("Binding grant capability is invalid")
    workspace_id = str(payload.get("workspaceId") or "")
    application_id = str(payload.get("applicationId") or "")
    scopes = tuple(str(item) for item in (payload.get("scopes") or ()))
    expires_at = int(payload.get("exp") or 0)
    if not workspace_id or not application_id:
        raise BindingGrantError("Binding grant scope is incomplete")
    if required_scope not in scopes:
        raise BindingGrantError("Binding grant does not authorize this operation")
    current = int(time.time()) if now is None else int(now)
    if expires_at <= current:
        raise BindingGrantError("Binding grant has expired")
    return BindingGrantClaims(
        workspace_id=workspace_id,
        application_id=application_id,
        capability_id=RELATIONAL_CAPABILITY_ID,
        scopes=scopes,
        expires_at=expires_at,
    )


__all__ = [
    "BindingGrantClaims",
    "BindingGrantError",
    "issue_binding_grant",
    "verify_binding_grant",
]
