from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.artifacts import ArtifactService
from packages.database.plugin_platform_models import (
    DigitalEventOutboxRecord,
    PluginInstallationRecord,
    PluginPackageRecord,
    PluginRuntimeInstanceRecord,
    PluginStorageNamespaceRecord,
    PluginVersionRecord,
)
from packages.plugins.contracts import PluginExecutionMode, PluginLifecycleState, PluginManifest
from packages.plugins.jobs import digital_platform_jobs
from packages.plugins.runtime_profiles import default_runtime_profiles


class PluginPlatformError(RuntimeError):
    pass


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _manifest_digest(manifest: Mapping[str, Any]) -> str:
    return hashlib.sha256(_json(manifest).encode("utf-8")).hexdigest()


class PluginPlatformService:
    """Durable package/version/install lifecycle without executing untrusted code.

    Publishing and installation only establish metadata, policy and storage identity.
    A trusted validator/runtime controller must validate/build/start non-native source
    before an installation can become ACTIVE. Capability execution remains Kernel-owned.
    """

    async def _package_for_version(
        self,
        db: AsyncSession,
        *,
        version: PluginVersionRecord,
    ) -> PluginPackageRecord:
        package = await db.get(PluginPackageRecord, version.package_id)
        if package is None:
            raise LookupError("Plugin package not found")
        return package

    @staticmethod
    def _assert_workspace_may_use(package: PluginPackageRecord, tenant_id: str) -> None:
        if package.visibility == "private" and package.owner_tenant_id != tenant_id:
            raise PermissionError("Private plugin belongs to another Workspace")

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
        if manifest.execution_mode is PluginExecutionMode.PLATFORM_NATIVE:
            raise PluginPlatformError(
                "Workspace-published plugins cannot request platform_native execution; "
                "native providers ship with trusted Operly code"
            )
        if manifest.runtime is None:
            raise PluginPlatformError("Workspace plugins require an isolated or remote runtime")
        # Merely naming a runtime in a manifest never creates mechanics. It must exist
        # in Operly's trusted registry before the package can enter validation.
        default_runtime_profiles().get(manifest.runtime.profile)

        if manifest.execution_mode is not PluginExecutionMode.REMOTE_HTTP and not package_artifact_id:
            raise PluginPlatformError(
                "Executable plugin workloads require an immutable Workspace package artifact"
            )
        artifacts = ArtifactService(db)
        if package_artifact_id:
            await artifacts.assert_workspace_artifact(
                tenant_id=tenant_id,
                artifact_id=package_artifact_id,
            )
        if sbom_artifact_id:
            await artifacts.assert_workspace_artifact(
                tenant_id=tenant_id,
                artifact_id=sbom_artifact_id,
            )

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
            validation_report_json=_json(
                {
                    "manifest_valid": True,
                    "runtime_profile": manifest.runtime.profile,
                    "runtime_validation_pending": True,
                    "supply_chain_scan_pending": bool(package_artifact_id),
                }
            ),
            created_by=user_id,
        )
        db.add(version)
        await db.flush()
        await digital_platform_jobs.enqueue(
            db,
            tenant_id=tenant_id,
            job_type="plugin.validate",
            subject_kind="plugin_version",
            subject_id=version.id,
            idempotency_key=f"plugin.validate:{version.id}:{version.manifest_digest}",
            payload={
                "version_id": version.id,
                "package_id": package.id,
                "manifest_digest": version.manifest_digest,
                "package_artifact_id": package_artifact_id,
                "sbom_artifact_id": sbom_artifact_id,
                "runtime_profile": manifest.runtime.profile,
                "execution_mode": manifest.execution_mode.value,
            },
            priority=50,
            created_by=user_id,
        )
        return package, version, manifest

    async def record_validation(
        self,
        db: AsyncSession,
        *,
        tenant_id: str,
        version_id: str,
        passed: bool,
        report: Mapping[str, Any],
    ) -> PluginVersionRecord:
        """Trusted validator seam; does not build or execute source itself."""
        version = await db.get(PluginVersionRecord, version_id)
        if version is None:
            raise LookupError("Plugin version not found")
        package = await self._package_for_version(db, version=version)
        self._assert_workspace_may_use(package, tenant_id)
        version.validation_status = "passed" if passed else "failed"
        version.validation_report_json = _json(dict(report))
        await db.flush()
        return version

    async def record_runtime_instance(
        self,
        db: AsyncSession,
        *,
        tenant_id: str,
        installation_id: str,
        runtime_profile: str,
        runtime_kind: str,
        state: str,
        health_state: str,
        provider: str | None = None,
        provider_reference: str | None = None,
        endpoint_reference: str | None = None,
        artifact_id: str | None = None,
    ) -> PluginRuntimeInstanceRecord:
        """Trusted runtime-controller seam for isolated/remote plugin instances."""
        installation = await db.scalar(
            select(PluginInstallationRecord).where(
                PluginInstallationRecord.id == installation_id,
                PluginInstallationRecord.tenant_id == tenant_id,
            )
        )
        if installation is None:
            raise LookupError("Plugin installation not found")
        version = await db.get(PluginVersionRecord, installation.version_id)
        if version is None:
            raise LookupError("Plugin version not found")
        manifest = PluginManifest.from_dict(json.loads(version.manifest_json))
        if manifest.runtime is None:
            raise PluginPlatformError("Plugin version does not declare a runtime")
        if runtime_profile != manifest.runtime.profile or runtime_kind != manifest.runtime.kind:
            raise PluginPlatformError("Runtime instance does not match the validated plugin manifest")
        default_runtime_profiles().get(runtime_profile)
        if state not in {"provisioning", "ready", "running", "stopped", "failed", "expired"}:
            raise PluginPlatformError("Runtime instance state is invalid")
        if health_state not in {"unknown", "warming", "healthy", "degraded", "unhealthy"}:
            raise PluginPlatformError("Runtime health state is invalid")
        if artifact_id:
            await ArtifactService(db).assert_workspace_artifact(
                tenant_id=tenant_id,
                artifact_id=artifact_id,
            )
        row = PluginRuntimeInstanceRecord(
            tenant_id=tenant_id,
            installation_id=installation.id,
            version_id=version.id,
            runtime_profile=runtime_profile,
            runtime_kind=runtime_kind,
            state=state,
            provider=provider,
            provider_reference=provider_reference,
            endpoint_reference=endpoint_reference,
            artifact_id=artifact_id,
            health_state=health_state,
            health_evidence_json="{}",
            last_heartbeat_at=datetime.utcnow() if state in {"ready", "running"} else None,
        )
        db.add(row)
        await db.flush()
        return row

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
        package = await self._package_for_version(db, version=version)
        self._assert_workspace_may_use(package, tenant_id)

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
            approved_network_json=_json(
                {
                    "mode": manifest.runtime.network.mode if manifest.runtime else "off",
                    "allowed_hosts": list(manifest.runtime.network.allowed_hosts) if manifest.runtime else [],
                }
            ),
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

    async def _assert_activation_ready(
        self,
        db: AsyncSession,
        *,
        installation: PluginInstallationRecord,
    ) -> None:
        version = await db.get(PluginVersionRecord, installation.version_id)
        if version is None or version.validation_status != "passed":
            raise PluginPlatformError("Plugin cannot activate until package validation passes")
        manifest = PluginManifest.from_dict(json.loads(version.manifest_json))
        if manifest.execution_mode is PluginExecutionMode.PLATFORM_NATIVE:
            raise PluginPlatformError("Workspace installations cannot activate as platform_native")
        instance = await db.scalar(
            select(PluginRuntimeInstanceRecord)
            .where(
                PluginRuntimeInstanceRecord.tenant_id == installation.tenant_id,
                PluginRuntimeInstanceRecord.installation_id == installation.id,
                PluginRuntimeInstanceRecord.version_id == version.id,
                PluginRuntimeInstanceRecord.state.in_(["ready", "running"]),
                PluginRuntimeInstanceRecord.health_state == "healthy",
            )
            .order_by(PluginRuntimeInstanceRecord.updated_at.desc())
        )
        if instance is None:
            raise PluginPlatformError("Plugin cannot activate until a healthy validated runtime is ready")

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
        wants_enabled = row.enabled if enabled is None else bool(enabled)
        if status is PluginLifecycleState.ACTIVE or wants_enabled:
            if status is not PluginLifecycleState.ACTIVE:
                raise PluginPlatformError("Enabled plugins must be in active lifecycle state")
            await self._assert_activation_ready(db, installation=row)
            wants_enabled = True
        if status in {
            PluginLifecycleState.DISABLED,
            PluginLifecycleState.FAILED,
            PluginLifecycleState.UNINSTALLING,
            PluginLifecycleState.UNINSTALLED,
        }:
            wants_enabled = False
        row.status = status.value
        row.enabled = wants_enabled
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
                    "credentials": [
                        {
                            "name": item.name,
                            "credential_type": item.credential_type,
                            "required": item.required,
                            "scopes": list(item.scopes),
                            "allowed_hosts": list(item.allowed_hosts),
                            "description": item.description,
                        }
                        for item in manifest.credentials
                    ],
                    "events_produced": [item.name for item in manifest.produces_events],
                    "events_consumed": list(manifest.consumes_events),
                    "requested_bindings": [
                        {
                            "semantic_name": item.semantic_name,
                            "capability_query": item.capability_query,
                            "required": item.required,
                        }
                        for item in manifest.requested_bindings
                    ],
                }
            )
        return result


plugin_platform = PluginPlatformService()
