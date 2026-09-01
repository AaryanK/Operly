from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import re
import socket
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import get_db
from packages.connectors.secrets import read_secret
from packages.database.plugin_credential_models import (
    PluginCredentialBindingRecord,
    PluginEgressGrantRecord,
)
from packages.plugins.bindings import RuntimeBindingService
from packages.plugins.budgets import ResourceBudgetExceeded, resource_budgets


router = APIRouter(prefix="/api/runtime-egress", tags=["runtime-egress"])

_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,100}$")
_DENIED_REQUEST_HEADERS = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "host",
    "connection",
    "proxy-connection",
    "keep-alive",
    "transfer-encoding",
    "upgrade",
    "te",
    "trailer",
    "content-length",
}
_SAFE_RESPONSE_HEADERS = {
    "content-type",
    "content-language",
    "etag",
    "last-modified",
    "retry-after",
    "x-request-id",
    "x-ratelimit-limit",
    "x-ratelimit-remaining",
    "x-ratelimit-reset",
    "ratelimit-limit",
    "ratelimit-remaining",
    "ratelimit-reset",
}


class RuntimeEgressInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: str = Field(default="GET", min_length=3, max_length=10)
    path: str = Field(default="/", min_length=1, max_length=2048)
    query: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    body_base64: str | None = Field(default=None, max_length=1_500_000)
    timeout_seconds: float = Field(default=20.0, ge=1.0, le=60.0)
    max_response_bytes: int = Field(default=1024 * 1024, ge=1, le=2 * 1024 * 1024)


def _runtime_token(authorization: str | None) -> str:
    value = str(authorization or "").strip()
    if not value.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Runtime bearer identity required")
    token = value[7:].strip()
    if not token.startswith("opr_") or len(token) < 32:
        raise HTTPException(status_code=401, detail="Runtime bearer identity is invalid")
    return token


def _json_list(raw: str | None) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    return [str(item) for item in value] if isinstance(value, list) else []


def _public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


async def _assert_public_host(host: str, port: int = 443) -> None:
    clean = str(host or "").strip().lower().rstrip(".")
    if not clean or clean in {"localhost", "localhost.localdomain"} or clean.endswith(
        (".localhost", ".local", ".internal", ".home.arpa")
    ):
        raise PermissionError("Egress host is local or invalid")
    try:
        rows = await asyncio.to_thread(
            socket.getaddrinfo, clean, port, type=socket.SOCK_STREAM
        )
    except socket.gaierror as error:
        raise RuntimeError("Egress host DNS could not be resolved") from error
    addresses = {str(row[4][0]) for row in rows if row and row[4]}
    if not addresses:
        raise RuntimeError("Egress host DNS returned no addresses")
    if any(not _public_address(address) for address in addresses):
        raise PermissionError("Egress host resolves to a private, local, or reserved address")


def _safe_headers(headers: dict[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    if len(headers) > 50:
        raise ValueError("Too many egress request headers")
    for raw_name, raw_value in headers.items():
        name = str(raw_name or "").strip()
        lower = name.lower()
        if not _HEADER_NAME.fullmatch(name):
            raise ValueError("Egress request contains an invalid header name")
        if lower in _DENIED_REQUEST_HEADERS or lower.startswith("x-operly-"):
            raise PermissionError(f"Runtime may not set egress header {name}")
        value = str(raw_value or "")
        if "\r" in value or "\n" in value or len(value.encode("utf-8")) > 8192:
            raise ValueError(f"Egress request header {name} is invalid")
        result[name] = value
    return result


def _credential_headers(
    binding: PluginCredentialBindingRecord,
    payload: dict[str, Any],
) -> dict[str, str]:
    envelope = payload.get("value")
    value = envelope if isinstance(envelope, dict) else payload
    credential_type = binding.credential_type
    if credential_type in {"bearer", "oauth2"}:
        token = str(
            value.get("access_token")
            or value.get("token")
            or value.get("bearer_token")
            or ""
        )
        if not token:
            raise PermissionError("Credential token is unavailable")
        return {"Authorization": f"Bearer {token}"}
    if credential_type == "basic":
        username = str(value.get("username") or "")
        password = str(value.get("password") or "")
        if not username or not password:
            raise PermissionError("Basic credential username/password are unavailable")
        encoded = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        return {"Authorization": f"Basic {encoded}"}
    if credential_type == "api_key":
        secret = str(value.get("api_key") or value.get("key") or value.get("token") or "")
        header_name = str(value.get("header_name") or "X-API-Key").strip()
        prefix = str(value.get("prefix") or "")
        if not secret or not _HEADER_NAME.fullmatch(header_name):
            raise PermissionError("API-key credential value/header are unavailable")
        if header_name.lower() in _DENIED_REQUEST_HEADERS - {"authorization"}:
            raise PermissionError("API-key credential header is unsafe")
        return {header_name: f"{prefix}{secret}"}
    if credential_type == "custom":
        custom = value.get("headers")
        if not isinstance(custom, dict) or not custom:
            raise PermissionError("Custom credential requires owner-supplied headers")
        result: dict[str, str] = {}
        for raw_name, raw_value in custom.items():
            name = str(raw_name or "").strip()
            if not _HEADER_NAME.fullmatch(name):
                raise PermissionError("Custom credential contains an invalid header")
            if name.lower() in {
                "host",
                "connection",
                "content-length",
                "transfer-encoding",
            }:
                raise PermissionError("Custom credential contains an unsafe header")
            value_text = str(raw_value or "")
            if "\r" in value_text or "\n" in value_text:
                raise PermissionError("Custom credential contains an invalid header value")
            result[name] = value_text
        return result
    raise PermissionError(f"Unsupported credential type: {credential_type}")


@router.post("/{grant_id}")
async def runtime_egress(
    grant_id: str,
    payload: RuntimeEgressInput,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    token = _runtime_token(authorization)
    runtime_bindings = RuntimeBindingService()
    try:
        identity = await runtime_bindings.authenticate(db, token=token)
        grant = await db.scalar(
            select(PluginEgressGrantRecord).where(
                PluginEgressGrantRecord.id == grant_id,
                PluginEgressGrantRecord.tenant_id == identity.tenant_id,
                PluginEgressGrantRecord.installation_id == identity.installation_id,
                PluginEgressGrantRecord.enabled.is_(True),
            )
        )
        if grant is None:
            raise LookupError("Egress grant not found")

        method = payload.method.upper().strip()
        allowed_methods = {item.upper() for item in _json_list(grant.methods_json)}
        if method not in allowed_methods:
            raise PermissionError("Egress grant does not allow this HTTP method")
        path = payload.path.strip()
        if not path.startswith("/") or path.startswith("//") or "\r" in path or "\n" in path:
            raise ValueError("Egress path must be a relative absolute-path reference")
        prefixes = _json_list(grant.path_prefixes_json)
        if not any(path.startswith(prefix) for prefix in prefixes):
            raise PermissionError("Egress grant does not allow this path")

        host = grant.host.strip().lower()
        await _assert_public_host(host)
        headers = _safe_headers(payload.headers)
        credential_binding: PluginCredentialBindingRecord | None = None
        if grant.credential_binding_id:
            credential_binding = await db.scalar(
                select(PluginCredentialBindingRecord).where(
                    PluginCredentialBindingRecord.id == grant.credential_binding_id,
                    PluginCredentialBindingRecord.tenant_id == identity.tenant_id,
                    PluginCredentialBindingRecord.installation_id == identity.installation_id,
                    PluginCredentialBindingRecord.status == "active",
                )
            )
            if credential_binding is None:
                raise PermissionError("Egress credential binding is unavailable")
            if host not in set(_json_list(credential_binding.allowed_hosts_json)):
                raise PermissionError("Credential binding no longer authorizes this host")
            secret_payload = await read_secret(
                db, identity.tenant_id, credential_binding.secret_reference
            )
            injected = _credential_headers(credential_binding, secret_payload)
            for name in injected:
                headers.pop(name, None)
                for existing in list(headers):
                    if existing.lower() == name.lower():
                        headers.pop(existing, None)
            headers.update(injected)

        try:
            body = base64.b64decode(payload.body_base64 or "", validate=True)
        except Exception as error:
            raise ValueError("body_base64 is invalid") from error
        if len(body) > 1024 * 1024:
            raise ValueError("Egress request body exceeds 1 MiB")
        query = urlencode(payload.query)
        url = f"https://{host}{path}" + (f"?{query}" if query else "")

        await resource_budgets.consume(
            db,
            tenant_id=identity.tenant_id,
            subject_kind="plugin_installation",
            subject_id=identity.installation_id,
            metric="egress_requests",
            quantity=1,
            reference_kind="egress_grant",
            reference_id=grant.id,
        )
        if body:
            await resource_budgets.consume(
                db,
                tenant_id=identity.tenant_id,
                subject_kind="plugin_installation",
                subject_id=identity.installation_id,
                metric="egress_request_bytes",
                quantity=len(body),
                reference_kind="egress_grant",
                reference_id=grant.id,
            )

        response_chunks: list[bytes] = []
        response_size = 0
        digest = hashlib.sha256()
        async with httpx.AsyncClient(
            timeout=payload.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            async with client.stream(
                method,
                url,
                headers=headers,
                content=body if body else None,
            ) as response:
                async for chunk in response.aiter_bytes():
                    response_size += len(chunk)
                    if response_size > payload.max_response_bytes:
                        raise ValueError("Egress response exceeds requested byte limit")
                    digest.update(chunk)
                    response_chunks.append(chunk)
                status_code = int(response.status_code)
                response_headers = {
                    key: value[:4000]
                    for key, value in response.headers.items()
                    if key.lower() in _SAFE_RESPONSE_HEADERS
                }

        if response_size:
            await resource_budgets.consume(
                db,
                tenant_id=identity.tenant_id,
                subject_kind="plugin_installation",
                subject_id=identity.installation_id,
                metric="egress_response_bytes",
                quantity=response_size,
                reference_kind="egress_grant",
                reference_id=grant.id,
            )
        await db.commit()
        raw_response = b"".join(response_chunks)
        return {
            "status_code": status_code,
            "headers": response_headers,
            "body_base64": base64.b64encode(raw_response).decode("ascii"),
            "size_bytes": response_size,
            "sha256": digest.hexdigest(),
            "host": host,
            "grant_id": grant.id,
            "credential_injected": credential_binding is not None,
            "credential_exposed": False,
            "redirect_followed": False,
        }
    except ResourceBudgetExceeded as error:
        await db.rollback()
        raise HTTPException(status_code=429, detail=str(error)) from error
    except LookupError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except PermissionError as error:
        await db.rollback()
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    except httpx.TimeoutException as error:
        await db.rollback()
        raise HTTPException(status_code=504, detail="Egress request timed out") from error
    except httpx.HTTPError as error:
        await db.rollback()
        raise HTTPException(status_code=502, detail="Egress target request failed") from error
    except RuntimeError as error:
        await db.rollback()
        raise HTTPException(status_code=503, detail=str(error)) from error
