from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import socket
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote, urlsplit
from uuid import uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.capability_binding_models import CapabilityBindingRecord
from packages.database.db import SessionFactory
from packages.database.plugin_platform_models import (
    PluginInstallationRecord,
    PluginRuntimeInstanceRecord,
    PluginVersionRecord,
)
from packages.kernel.contracts import CapabilityExecutionResult, CapabilitySpec
from packages.plugins.bindings import RuntimeBindingService
from packages.plugins.contracts import PluginExecutionMode, PluginManifest
from packages.security.execution_context import ExecutionContext


PROVIDER_ID = "operly.plugin_runtime"
MAX_REMOTE_RESPONSE_BYTES = 2 * 1024 * 1024


class PluginRuntimeTransportError(RuntimeError):
    pass


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


def validate_remote_base_url(value: str) -> tuple[str, str, int]:
    raw = str(value or "").strip().rstrip("/")
    if not raw or len(raw) > 1800:
        raise ValueError("Remote plugin runtime_endpoint is missing or too long")
    try:
        parsed = urlsplit(raw)
    except ValueError as error:
        raise ValueError("Remote plugin runtime_endpoint is invalid") from error
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("Remote plugin runtime_endpoint must use HTTPS")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(
            "Remote plugin runtime_endpoint may not contain credentials, query, or fragment"
        )
    host = parsed.hostname.lower().rstrip(".")
    if host in {"localhost", "localhost.localdomain"} or host.endswith(
        (".localhost", ".local", ".internal", ".home.arpa")
    ):
        raise ValueError("Remote plugin runtime_endpoint may not use a local hostname")
    try:
        port = int(parsed.port or 443)
    except ValueError as error:
        raise ValueError("Remote plugin runtime_endpoint port is invalid") from error
    if not 1 <= port <= 65535:
        raise ValueError("Remote plugin runtime_endpoint port is invalid")
    return raw, host, port


async def assert_public_runtime_host(host: str, port: int) -> tuple[str, ...]:
    try:
        rows = await asyncio.to_thread(
            socket.getaddrinfo, host, port, type=socket.SOCK_STREAM
        )
    except socket.gaierror as error:
        raise PluginRuntimeTransportError(
            "Remote plugin runtime DNS could not be resolved"
        ) from error
    addresses = sorted({str(row[4][0]) for row in rows if row and row[4]})
    if not addresses:
        raise PluginRuntimeTransportError(
            "Remote plugin runtime DNS returned no addresses"
        )
    if any(not _public_address(address) for address in addresses):
        raise PermissionError(
            "Remote plugin runtime resolves to a private, local, or reserved address"
        )
    return tuple(addresses)


def _manifest(raw: str) -> PluginManifest:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise PluginRuntimeTransportError("Installed plugin manifest is invalid") from error
    if not isinstance(payload, dict):
        raise PluginRuntimeTransportError("Installed plugin manifest is invalid")
    return PluginManifest.from_dict(payload)


def _health_ttl_seconds() -> int:
    return max(
        30,
        min(int(os.getenv("OPERLY_PLUGIN_RUNTIME_HEALTH_TTL_SECONDS", "300")), 3600),
    )


class PluginRuntimeProvider:
    """Kernel provider for active plugin capabilities backed by reconciled runtimes.

    The provider never imports plugin code and never forwards Workspace/provider secrets.
    A short-lived runtime identity is issued in a separately committed DB transaction so
    the remote workload can call governed Operly gateways during the invocation. That
    identity is revoked in a second transaction immediately after the request finishes.
    """

    async def _resolve(
        self,
        db: AsyncSession,
        *,
        context: ExecutionContext,
        capability: CapabilitySpec,
        require_fresh: bool,
    ) -> tuple[PluginInstallationRecord, PluginVersionRecord, PluginManifest, PluginRuntimeInstanceRecord]:
        if not context.workspace_id:
            raise PermissionError("Plugin capabilities require Workspace scope")
        installations = list(
            (
                await db.scalars(
                    select(PluginInstallationRecord).where(
                        PluginInstallationRecord.tenant_id == context.workspace_id,
                        PluginInstallationRecord.enabled.is_(True),
                        PluginInstallationRecord.status == "active",
                    )
                )
            ).all()
        )
        matches: list[tuple[PluginInstallationRecord, PluginVersionRecord, PluginManifest]] = []
        for installation in installations:
            version = await db.get(PluginVersionRecord, installation.version_id)
            if version is None or version.validation_status != "passed":
                continue
            manifest = _manifest(version.manifest_json)
            for spec in manifest.capability_specs():
                if spec.id == capability.id and spec.version == capability.version:
                    matches.append((installation, version, manifest))
                    break
        if not matches:
            raise LookupError("Active plugin capability installation is unavailable")
        if len(matches) > 1:
            raise RuntimeError(
                f"Multiple active plugin installations provide capability {capability.id}"
            )
        installation, version, manifest = matches[0]
        instance = await db.scalar(
            select(PluginRuntimeInstanceRecord)
            .where(
                PluginRuntimeInstanceRecord.tenant_id == context.workspace_id,
                PluginRuntimeInstanceRecord.installation_id == installation.id,
                PluginRuntimeInstanceRecord.version_id == version.id,
                PluginRuntimeInstanceRecord.state.in_(["ready", "running"]),
                PluginRuntimeInstanceRecord.health_state == "healthy",
            )
            .order_by(PluginRuntimeInstanceRecord.updated_at.desc())
        )
        if instance is None:
            raise LookupError("Plugin runtime is not healthy")
        if require_fresh:
            checked = instance.last_heartbeat_at
            if checked is None or checked < datetime.utcnow() - timedelta(
                seconds=_health_ttl_seconds()
            ):
                raise LookupError("Plugin runtime health is stale; reconcile it before use")
        return installation, version, manifest, instance

    async def is_available(
        self,
        db: AsyncSession,
        *,
        context: ExecutionContext,
        capability: CapabilitySpec,
    ) -> bool:
        try:
            await self._resolve(
                db,
                context=context,
                capability=capability,
                require_fresh=True,
            )
            return True
        except (LookupError, PermissionError, RuntimeError, ValueError):
            return False

    async def _issue_runtime_identity(
        self,
        *,
        tenant_id: str,
        installation_id: str,
        runtime_instance_id: str,
        ttl_seconds: int,
    ):
        async with SessionFactory() as identity_db:
            binding_ids = list(
                await identity_db.scalars(
                    select(CapabilityBindingRecord.id).where(
                        CapabilityBindingRecord.tenant_id == tenant_id,
                        CapabilityBindingRecord.subject_kind == "plugin_installation",
                        CapabilityBindingRecord.subject_id == installation_id,
                        CapabilityBindingRecord.status == "active",
                        CapabilityBindingRecord.enabled.is_(True),
                    )
                )
            )
            issued = await RuntimeBindingService().issue(
                identity_db,
                tenant_id=tenant_id,
                installation_id=installation_id,
                runtime_instance_id=runtime_instance_id,
                allowed_binding_ids=[str(value) for value in binding_ids],
                ttl_seconds=ttl_seconds,
            )
            await identity_db.commit()
            return issued

    async def _revoke_runtime_identity(self, *, tenant_id: str, identity_id: str) -> None:
        async with SessionFactory() as identity_db:
            try:
                await RuntimeBindingService().revoke(
                    identity_db,
                    identity_id=identity_id,
                    tenant_id=tenant_id,
                )
                await identity_db.commit()
            except Exception:
                await identity_db.rollback()

    async def execute(
        self,
        db: AsyncSession,
        *,
        context: ExecutionContext,
        capability: CapabilitySpec,
        arguments: dict[str, Any],
        minimum_context: dict[str, Any],
    ) -> CapabilityExecutionResult:
        installation, version, manifest, instance = await self._resolve(
            db,
            context=context,
            capability=capability,
            require_fresh=True,
        )
        if manifest.execution_mode is not PluginExecutionMode.REMOTE_HTTP:
            raise PluginRuntimeTransportError(
                f"Runtime transport for {manifest.execution_mode.value} is not provisioned yet"
            )
        endpoint, host, port = validate_remote_base_url(instance.endpoint_reference or "")
        if manifest.runtime is None:
            raise PluginRuntimeTransportError("Plugin runtime contract is missing")
        declared_hosts = {value.lower().rstrip(".") for value in manifest.runtime.network.allowed_hosts}
        if host not in declared_hosts:
            raise PermissionError(
                "Reconciled remote runtime host is no longer declared by the plugin manifest"
            )
        await assert_public_runtime_host(host, port)

        invocation_id = f"pinv_{uuid4().hex}"
        max_runtime = min(int(manifest.runtime.resources.max_runtime_seconds), 300)
        issued = await self._issue_runtime_identity(
            tenant_id=installation.tenant_id,
            installation_id=installation.id,
            runtime_instance_id=instance.id,
            ttl_seconds=max(60, min(max_runtime + 60, 900)),
        )
        public_base = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").strip().rstrip("/")
        request_body = {
            "invocation_id": invocation_id,
            "capability_id": capability.id,
            "capability_version": capability.version,
            "arguments": arguments,
            "context": minimum_context,
            "operly": {
                "runtime_token": issued.token,
                "runtime_token_expires_at": issued.expires_at.isoformat(),
                "capability_gateway": f"{public_base}/api/capability-gateway",
                "egress_gateway": f"{public_base}/api/runtime-egress",
            },
        }
        target = f"{endpoint}/v1/capabilities/{quote(capability.id, safe='._-')}"
        try:
            raw = json.dumps(
                request_body,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
            if len(raw) > 2 * 1024 * 1024:
                raise ValueError("Plugin capability invocation payload exceeds 2 MiB")
            response_bytes = bytearray()
            async with httpx.AsyncClient(
                timeout=max(1.0, float(max_runtime)),
                follow_redirects=False,
                trust_env=False,
            ) as client:
                async with client.stream(
                    "POST",
                    target,
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "Operly-Plugin-Runtime/1",
                        "X-Operly-Invocation-ID": invocation_id,
                    },
                    content=raw,
                ) as response:
                    async for chunk in response.aiter_bytes():
                        response_bytes.extend(chunk)
                        if len(response_bytes) > MAX_REMOTE_RESPONSE_BYTES:
                            raise PluginRuntimeTransportError(
                                "Plugin runtime response exceeds 2 MiB"
                            )
                    status_code = int(response.status_code)
            if status_code < 200 or status_code >= 300:
                raise PluginRuntimeTransportError(
                    f"Plugin runtime returned HTTP {status_code}"
                )
            try:
                packet = json.loads(bytes(response_bytes) or b"{}")
            except json.JSONDecodeError as error:
                raise PluginRuntimeTransportError(
                    "Plugin runtime returned invalid JSON"
                ) from error
            if not isinstance(packet, dict) or not isinstance(packet.get("result"), dict):
                raise PluginRuntimeTransportError(
                    "Plugin runtime response must contain an object result"
                )
            event_payload = packet.get("event_payload") or {}
            if not isinstance(event_payload, dict):
                raise PluginRuntimeTransportError(
                    "Plugin runtime event_payload must be an object"
                )
            return CapabilityExecutionResult(
                value=dict(packet["result"]),
                resource_type=(
                    str(packet["resource_type"])[:120]
                    if packet.get("resource_type") is not None
                    else None
                ),
                resource_id=(
                    str(packet["resource_id"])[:200]
                    if packet.get("resource_id") is not None
                    else None
                ),
                event_payload=dict(event_payload),
            )
        except httpx.TimeoutException as error:
            raise PluginRuntimeTransportError("Plugin runtime invocation timed out") from error
        except httpx.HTTPError as error:
            raise PluginRuntimeTransportError("Plugin runtime invocation failed") from error
        finally:
            await self._revoke_runtime_identity(
                tenant_id=installation.tenant_id,
                identity_id=issued.identity_id,
            )


plugin_runtime_provider = PluginRuntimeProvider()

__all__ = [
    "PROVIDER_ID",
    "PluginRuntimeProvider",
    "PluginRuntimeTransportError",
    "assert_public_runtime_host",
    "plugin_runtime_provider",
    "validate_remote_base_url",
]
