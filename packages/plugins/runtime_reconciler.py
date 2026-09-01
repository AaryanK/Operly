from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.plugin_platform_models import (
    DigitalEventOutboxRecord,
    PluginInstallationRecord,
    PluginRuntimeInstanceRecord,
    PluginVersionRecord,
)
from packages.plugins.contracts import PluginExecutionMode, PluginManifest
from packages.plugins.runtime_provider import (
    PluginRuntimeTransportError,
    assert_public_runtime_host,
    validate_remote_base_url,
)


class RuntimeReconciliationError(RuntimeError):
    def __init__(self, message: str, *, permanent: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent


@dataclass(frozen=True, slots=True)
class RuntimeReconciliationResult:
    runtime_instance_id: str
    state: str
    health_state: str
    provider: str
    endpoint: str
    evidence: dict[str, Any]


class PluginRuntimeReconciler:
    """Trusted control-plane reconciliation for concrete plugin runtime transports."""

    async def _context(
        self,
        db: AsyncSession,
        *,
        tenant_id: str,
        installation_id: str,
    ) -> tuple[PluginInstallationRecord, PluginVersionRecord, PluginManifest]:
        installation = await db.scalar(
            select(PluginInstallationRecord).where(
                PluginInstallationRecord.id == installation_id,
                PluginInstallationRecord.tenant_id == tenant_id,
            )
        )
        if installation is None:
            raise RuntimeReconciliationError(
                "Plugin installation not found", permanent=True
            )
        version = await db.get(PluginVersionRecord, installation.version_id)
        if version is None:
            raise RuntimeReconciliationError("Plugin version not found", permanent=True)
        if version.validation_status != "passed":
            raise RuntimeReconciliationError(
                "Plugin runtime cannot reconcile until package validation passes"
            )
        try:
            payload = json.loads(version.manifest_json)
            if not isinstance(payload, dict):
                raise ValueError("manifest must be an object")
            manifest = PluginManifest.from_dict(payload)
        except Exception as error:
            raise RuntimeReconciliationError(
                "Installed plugin manifest is invalid", permanent=True
            ) from error
        return installation, version, manifest

    async def _instance(
        self,
        db: AsyncSession,
        *,
        installation: PluginInstallationRecord,
        version: PluginVersionRecord,
        manifest: PluginManifest,
        endpoint: str,
        provider_reference: str,
    ) -> PluginRuntimeInstanceRecord:
        instance = await db.scalar(
            select(PluginRuntimeInstanceRecord)
            .where(
                PluginRuntimeInstanceRecord.tenant_id == installation.tenant_id,
                PluginRuntimeInstanceRecord.installation_id == installation.id,
                PluginRuntimeInstanceRecord.version_id == version.id,
                PluginRuntimeInstanceRecord.provider == "remote-http",
            )
            .order_by(PluginRuntimeInstanceRecord.updated_at.desc())
        )
        if instance is None:
            instance = PluginRuntimeInstanceRecord(
                tenant_id=installation.tenant_id,
                installation_id=installation.id,
                version_id=version.id,
                runtime_profile=manifest.runtime.profile if manifest.runtime else "remote-http",
                runtime_kind=manifest.runtime.kind if manifest.runtime else "remote",
                state="provisioning",
                provider="remote-http",
                provider_reference=provider_reference,
                endpoint_reference=endpoint,
                health_state="warming",
                health_evidence_json="{}",
            )
            db.add(instance)
            await db.flush()
        else:
            instance.runtime_profile = manifest.runtime.profile if manifest.runtime else instance.runtime_profile
            instance.runtime_kind = manifest.runtime.kind if manifest.runtime else instance.runtime_kind
            instance.provider_reference = provider_reference
            instance.endpoint_reference = endpoint
            instance.state = "provisioning"
            instance.health_state = "warming"
        return instance

    async def reconcile_remote_http(
        self,
        db: AsyncSession,
        *,
        tenant_id: str,
        installation_id: str,
        endpoint: str,
    ) -> RuntimeReconciliationResult:
        installation, version, manifest = await self._context(
            db, tenant_id=tenant_id, installation_id=installation_id
        )
        if manifest.execution_mode is not PluginExecutionMode.REMOTE_HTTP:
            raise RuntimeReconciliationError(
                "This runtime reconciler only handles remote_http installations",
                permanent=True,
            )
        if manifest.runtime is None or manifest.runtime.kind != "remote":
            raise RuntimeReconciliationError(
                "Remote HTTP plugin runtime contract is invalid", permanent=True
            )
        try:
            base, host, port = validate_remote_base_url(endpoint)
        except ValueError as error:
            raise RuntimeReconciliationError(str(error), permanent=True) from error
        declared_hosts = {
            value.lower().rstrip(".") for value in manifest.runtime.network.allowed_hosts
        }
        if host not in declared_hosts:
            raise RuntimeReconciliationError(
                "Remote runtime host must be declared in runtime.network.allowed_hosts",
                permanent=True,
            )
        try:
            addresses = await assert_public_runtime_host(host, port)
        except PermissionError as error:
            raise RuntimeReconciliationError(str(error), permanent=True) from error
        except PluginRuntimeTransportError as error:
            raise RuntimeReconciliationError(str(error)) from error

        instance = await self._instance(
            db,
            installation=installation,
            version=version,
            manifest=manifest,
            endpoint=base,
            provider_reference=host,
        )
        await db.flush()

        health_path = manifest.runtime.health_path or "/health"
        health_url = f"{base}{health_path}"
        digest = hashlib.sha256()
        response_bytes = 0
        status_code: int | None = None
        checked_at = datetime.utcnow()
        try:
            async with httpx.AsyncClient(
                timeout=15.0,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                async with client.stream(
                    "GET",
                    health_url,
                    headers={"User-Agent": "Operly-Plugin-Health/1"},
                ) as response:
                    status_code = int(response.status_code)
                    async for chunk in response.aiter_bytes():
                        response_bytes += len(chunk)
                        if response_bytes > 64 * 1024:
                            raise RuntimeReconciliationError(
                                "Remote runtime health response exceeds 64 KiB",
                                permanent=True,
                            )
                        digest.update(chunk)
        except httpx.TimeoutException as error:
            instance.state = "failed"
            instance.health_state = "unhealthy"
            instance.health_evidence_json = json.dumps(
                {
                    "checked_at": checked_at.isoformat(),
                    "health_url": health_url,
                    "error": "timeout",
                },
                separators=(",", ":"),
                sort_keys=True,
            )
            await db.flush()
            raise RuntimeReconciliationError("Remote runtime health check timed out") from error
        except httpx.HTTPError as error:
            instance.state = "failed"
            instance.health_state = "unhealthy"
            instance.health_evidence_json = json.dumps(
                {
                    "checked_at": checked_at.isoformat(),
                    "health_url": health_url,
                    "error": "network_error",
                },
                separators=(",", ":"),
                sort_keys=True,
            )
            await db.flush()
            raise RuntimeReconciliationError("Remote runtime health check failed") from error

        evidence = {
            "checked_at": checked_at.isoformat(),
            "health_url": health_url,
            "status_code": status_code,
            "response_bytes": response_bytes,
            "response_sha256": digest.hexdigest(),
            "resolved_address_count": len(addresses),
            "redirects_followed": False,
        }
        healthy = status_code is not None and 200 <= status_code < 300
        instance.state = "ready" if healthy else "failed"
        instance.health_state = "healthy" if healthy else "unhealthy"
        instance.health_evidence_json = json.dumps(
            evidence, separators=(",", ":"), sort_keys=True
        )
        instance.last_heartbeat_at = checked_at if healthy else None
        instance.updated_at = checked_at
        db.add(
            DigitalEventOutboxRecord(
                tenant_id=installation.tenant_id,
                event_type=(
                    "plugin.runtime.healthy" if healthy else "plugin.runtime.unhealthy"
                ),
                source_kind="plugin_runtime_instance",
                source_id=instance.id,
                subject_type="plugin_installation",
                subject_id=installation.id,
                payload_json=json.dumps(
                    {
                        "installation_id": installation.id,
                        "runtime_instance_id": instance.id,
                        "provider": "remote-http",
                        "health_state": instance.health_state,
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            )
        )
        await db.flush()
        if not healthy:
            raise RuntimeReconciliationError(
                f"Remote runtime health endpoint returned HTTP {status_code}"
            )
        return RuntimeReconciliationResult(
            runtime_instance_id=instance.id,
            state=instance.state,
            health_state=instance.health_state,
            provider="remote-http",
            endpoint=base,
            evidence=evidence,
        )

    async def reconcile(
        self,
        db: AsyncSession,
        *,
        tenant_id: str,
        installation_id: str,
        endpoint: str | None = None,
    ) -> RuntimeReconciliationResult:
        _, _, manifest = await self._context(
            db, tenant_id=tenant_id, installation_id=installation_id
        )
        if manifest.execution_mode is PluginExecutionMode.REMOTE_HTTP:
            if not endpoint:
                existing = await db.scalar(
                    select(PluginRuntimeInstanceRecord)
                    .where(
                        PluginRuntimeInstanceRecord.tenant_id == tenant_id,
                        PluginRuntimeInstanceRecord.installation_id == installation_id,
                        PluginRuntimeInstanceRecord.provider == "remote-http",
                        PluginRuntimeInstanceRecord.endpoint_reference.is_not(None),
                    )
                    .order_by(PluginRuntimeInstanceRecord.updated_at.desc())
                )
                endpoint = existing.endpoint_reference if existing else None
            if not endpoint:
                raise RuntimeReconciliationError(
                    "Remote HTTP runtime reconciliation requires an endpoint",
                    permanent=True,
                )
            return await self.reconcile_remote_http(
                db,
                tenant_id=tenant_id,
                installation_id=installation_id,
                endpoint=endpoint,
            )
        raise RuntimeReconciliationError(
            f"Hosted runtime adapter for {manifest.execution_mode.value} is not implemented yet",
            permanent=True,
        )


plugin_runtime_reconciler = PluginRuntimeReconciler()

__all__ = [
    "PluginRuntimeReconciler",
    "RuntimeReconciliationError",
    "RuntimeReconciliationResult",
    "plugin_runtime_reconciler",
]
