from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.plugin_platform_models import (
    DigitalEventOutboxRecord,
    PluginInstallationRecord,
    PluginPackageRecord,
    PluginStorageNamespaceRecord,
    PluginVersionRecord,
)
from packages.plugins.contracts import PluginLifecycleState, PluginManifest


class PluginPlatformError(RuntimeError):
    pass


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _manifest_digest(manifest: Mapping[str, Any]) -> str:
    return hashlib.sha256(_json(manifest).encode("utf-8")).hexdigest()


class PluginPlatformService:
    """Durable package/version/install lifecycle without executing untrusted code.

    Publishing and installation only establish metadata, policy and storage identity.
    A runtime adapter must later validate/build/start the plugin before an installation
    can become ACTIVE and before its capabilities are composed into the live Kernel.
    """

    async def publish_workspace_version(
        self,
        db: AsyncSession,
        *,
        tenant_id: str,
        user_id: str,
        manifest_payload: Mapping[str, Any],
        package_artifact_id: str | None = None,
        sbom_artifact_id: str | None = None,
        source_digest: str | None = None,
    ) -> tuple[PluginPackageRecord, PluginVersionRecord, PluginManifest]:
        manifest = PluginManifest.from_dict(manifest_payload)
        namespace = f"workspace:{tenant_id}"
        package = await db.scalar(
            select(PluginPackageRecord).where(
                PluginPackageRecord.namespace == namespace,
                PluginPackageRecord.plugin_id == manifest.plugin_id,
            )
        )
        if package is None:
            package = PluginPackageRecord(
                namespace=namespace,
                plugin_id=manifest.plugin_id,
                display_name=manifest.display_name,
                description=manifest.description,
                visibility="private",
                owner_tenant_id=tenant_id,
                publisher_user_id=user_id,
            )
            db.add(package)
            await db.flush()
        else:
            package.display_name = manifest.display_name
            package.description = manifest.description

        existing = await db.scalar(
            select(PluginVersionRecord).where(
                PluginVersionRecord.package_id == package.id,
                PluginVersionRecord.version == manifest.version,
            )
        )
        if existing is not None:
            raise PluginPlatformError(f"Plugin version already exists: {manifest.plugin_id}@{manifest.version}")

        normalized_manifest = manifest.to_dict()
        version = PluginVersionRecord(
            package_id=package.id,
            version=manifest.version,
            manifest_json=_json(normalized_manifest),
            manifest_digest=_manifest_digest(normalized_manifest),
            package_artifact_id=package_artifact_id,
            sbom_artifact_id=sbom_artifact_id,
            source_digest=source_digest,
            trust_level="workspace_generated",
            validation_status="pending",
            validation_report_json=_json({
                "manifest_valid": True,
                "runtime_validation_pending": manifest.execution_mode.value != "platform_native",
                "supply_chain_scan_pending": bool(package_artifact_id),
            }),
            created_by=user_id,
        )
        db.add(version)
        await db.flush()
        return package, version, manifest

    async def install_version(
        self,
        db: AsyncSession,
        *,
        tenant_id: str,
        user_id: str,
        version_id: str,
        granted_permissions: list[str] | None = None,
        configuration: Mapping[str, Any] | None = None,
    ) -> PluginInstallationRecord:
        version = await db.get(PluginVersionRecord, version_id)
        if version is None:
            raise LookupError("Plugin version not found")
        package = await db.get(PluginPackageRecord, version.package_id)
        if package is None:
            raise LookupError("Plugin package not found")
        if package.visibility == "private" and package.owner_tenant_id != tenant_id:
            raise PermissionError("Private plugin belongs to another Workspace")

        manifest = PluginManifest.from_dict(json.loads(version.manifest_json))
        requested = set(manifest.permissions)
        granted = set(granted_permissions or [])
        if not granted.issubset(requested):
            raise PluginPlatformError("Granted permissions must be a subset of the plugin manifest request")

        existing = await db.scalar(
            select(PluginInstallationRecord).where(
                PluginInstallationRecord.tenant_id == tenant_id,
                PluginInstallationRecord.package_id == package.id,
            )
        )
        if existing is not None:
            raise PluginPlatformError("Plugin is already installed in this Workspace")

        installation = PluginInstallationRecord(
            tenant_id=tenant_id,
            package_id=package.id,
            version_id=version.id,
            status=PluginLifecycleState.INSTALLED.value,
            enabled=False,
            configuration_json=_json(dict(configuration or {})),
            granted_permissions_json=_json(sorted(granted)),
            approved_network_json=_json({
                "mode": manifest.runtime.network.mode if manifest.runtime else "off",
                "allowed_hosts": list(manifest.runtime.network.allowed_hosts) if manifest.runtime else [],
            }),
            installed_by=user_id,
        )
        db.add(installation)
        await db.flush()

        for storage in manifest.storage:
            db.add(
                PluginStorageNamespaceRecord(
                    tenant_id=tenant_id,
                    installation_id=installation.id,
                    name=storage.name,
                    storage_kind=storage.kind,
                    quota_bytes=storage.quota_bytes,
                    retention_policy_json="{}",
                )
            )

        db.add(
            DigitalEventOutboxRecord(
                tenant_id=tenant_id,
                event_type="plugin.installed",
                source_kind="plugin_installation",
                source_id=installation.id,
                subject_type="plugin",
                subject_id=package.plugin_id,
                payload_json=_json({"plugin_id": package.plugin_id, "version": version.version}),
            )
        )
        await db.flush()
        return installation

    async def set_installation_state(
        self,
        db: AsyncSession,
        *,
        tenant_id: str,
        installation_id: str,
        status: PluginLifecycleState,
        enabled: bool | None = None,
        configuration: Mapping[str, Any] | None = None,
    ) -> PluginInstallationRecord:
        row = await db.scalar(
            select(PluginInstallationRecord).where(
                PluginInstallationRecord.id == installation_id,
                PluginInstallationRecord.tenant_id == tenant_id,
            )
        )
        if row is None:
            raise LookupError("Plugin installation not found")
        if status is PluginLifecycleState.ACTIVE and enabled is False:
            raise PluginPlatformError("An active plugin cannot be explicitly disabled")
        row.status = status.value
        if enabled is not None:
            row.enabled = bool(enabled)
        if configuration is not None:
            row.configuration_json = _json(dict(configuration))
        row.updated_at = datetime.utcnow()
        db.add(
            DigitalEventOutboxRecord(
                tenant_id=tenant_id,
                event_type=f"plugin.{status.value}",
                source_kind="plugin_installation",
                source_id=row.id,
                subject_type="plugin",
                subject_id=row.package_id,
                payload_json=_json({"installation_id": row.id, "status": row.status, "enabled": row.enabled}),
            )
        )
        await db.flush()
        return row

    async def list_installations(self, db: AsyncSession, *, tenant_id: str) -> list[dict[str, Any]]:
        rows = (
            await db.scalars(
                select(PluginInstallationRecord)
                .where(PluginInstallationRecord.tenant_id == tenant_id)
                .order_by(PluginInstallationRecord.installed_at.desc())
            )
        ).all()
        result: list[dict[str, Any]] = []
        for row in rows:
            package = await db.get(PluginPackageRecord, row.package_id)
            version = await db.get(PluginVersionRecord, row.version_id)
            if package is None or version is None:
                continue
            manifest = PluginManifest.from_dict(json.loads(version.manifest_json))
            result.append(
                {
                    "id": row.id,
                    "plugin_id": package.plugin_id,
                    "namespace": package.namespace,
                    "display_name": package.display_name,
                    "description": package.description,
                    "version": version.version,
                    "version_id": version.id,
                    "manifest_digest": version.manifest_digest,
                    "trust_level": version.trust_level,
                    "validation_status": version.validation_status,
                    "status": row.status,
                    "enabled": row.enabled,
                    "execution_mode": manifest.execution_mode.value,
                    "runtime": manifest.runtime.profile if manifest.runtime else None,
                    "capabilities": [spec.public_dict() for spec in manifest.capability_specs()],
                    "permissions_requested": list(manifest.permissions),
                    "permissions_granted": json.loads(row.granted_permissions_json or "[]"),
                    "configuration": json.loads(row.configuration_json or "{}"),
                    "storage": [
                        {"name": item.name, "kind": item.kind, "quota_bytes": item.quota_bytes}
                        for item in manifest.storage
                    ],
                    "events_produced": [item.name for item in manifest.produces_events],
                    "events_consumed": list(manifest.consumes_events),
                    "requested_bindings": [
                        {"semantic_name": item.semantic_name, "capability_query": item.capability_query, "required": item.required}
                        for item in manifest.requested_bindings
                    ],
                }
            )
        return result


plugin_platform = PluginPlatformService()
