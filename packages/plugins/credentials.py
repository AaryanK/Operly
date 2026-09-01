from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.connector_models import ConnectorSecret
from packages.database.plugin_credential_models import (
    PluginCredentialBindingRecord,
    PluginEgressGrantRecord,
)
from packages.database.plugin_platform_models import PluginInstallationRecord, PluginVersionRecord
from packages.plugins.contracts import CredentialRequest, PluginManifest


@dataclass(frozen=True, slots=True)
class CredentialHandle:
    binding_id: str
    credential_name: str
    credential_type: str
    granted_scopes: tuple[str, ...]
    allowed_hosts: tuple[str, ...]


class PluginCredentialService:
    """Bind encrypted Workspace secrets to plugin-declared credential handles.

    This service deliberately never decrypts or returns a secret. A future trusted
    egress broker may resolve the stored ``secret_reference`` internally after also
    validating runtime identity, host, method, path and current installation state.
    """

    async def _installation_manifest(
        self,
        db: AsyncSession,
        *,
        tenant_id: str,
        installation_id: str,
    ) -> tuple[PluginInstallationRecord, PluginManifest]:
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
        return installation, PluginManifest.from_dict(json.loads(version.manifest_json))

    @staticmethod
    def _request(manifest: PluginManifest, name: str) -> CredentialRequest:
        for item in manifest.credentials:
            if item.name == name:
                return item
        raise PermissionError("Plugin did not declare this credential handle")

    async def bind_secret(
        self,
        db: AsyncSession,
        *,
        tenant_id: str,
        installation_id: str,
        credential_name: str,
        secret_reference: str,
        granted_scopes: list[str] | None = None,
        allowed_hosts: list[str] | None = None,
        created_by: str | None = None,
    ) -> CredentialHandle:
        _, manifest = await self._installation_manifest(
            db, tenant_id=tenant_id, installation_id=installation_id
        )
        request = self._request(manifest, credential_name)
        secret = await db.scalar(
            select(ConnectorSecret).where(
                ConnectorSecret.id == secret_reference,
                ConnectorSecret.tenant_id == tenant_id,
            )
        )
        if secret is None:
            raise PermissionError("Credential secret is unavailable in this Workspace")

        scopes = sorted({str(v) for v in (granted_scopes or []) if str(v).strip()})
        hosts = sorted({str(v).strip().lower() for v in (allowed_hosts or []) if str(v).strip()})
        if not set(scopes).issubset(set(request.scopes)):
            raise PermissionError("Credential scopes may only narrow the manifest request")
        if not set(hosts).issubset(set(request.allowed_hosts)):
            raise PermissionError("Credential hosts may only narrow the manifest request")

        existing = await db.scalar(
            select(PluginCredentialBindingRecord).where(
                PluginCredentialBindingRecord.installation_id == installation_id,
                PluginCredentialBindingRecord.credential_name == credential_name,
            )
        )
        if existing is not None:
            raise ValueError("Credential handle is already bound for this plugin installation")

        row = PluginCredentialBindingRecord(
            tenant_id=tenant_id,
            installation_id=installation_id,
            credential_name=credential_name,
            credential_type=request.credential_type,
            secret_reference=secret_reference,
            granted_scopes_json=json.dumps(scopes, separators=(",", ":")),
            allowed_hosts_json=json.dumps(hosts, separators=(",", ":")),
            status="active",
            created_by=created_by,
        )
        db.add(row)
        await db.flush()
        return CredentialHandle(
            binding_id=row.id,
            credential_name=row.credential_name,
            credential_type=row.credential_type,
            granted_scopes=tuple(scopes),
            allowed_hosts=tuple(hosts),
        )

    async def create_egress_grant(
        self,
        db: AsyncSession,
        *,
        tenant_id: str,
        installation_id: str,
        host: str,
        credential_binding_id: str | None = None,
        methods: list[str] | None = None,
        path_prefixes: list[str] | None = None,
        created_by: str | None = None,
    ) -> PluginEgressGrantRecord:
        _, manifest = await self._installation_manifest(
            db, tenant_id=tenant_id, installation_id=installation_id
        )
        clean_host = str(host or "").strip().lower()
        if manifest.runtime is None or clean_host not in set(manifest.runtime.network.allowed_hosts):
            raise PermissionError("Egress host is not declared by the plugin runtime")

        credential: PluginCredentialBindingRecord | None = None
        if credential_binding_id:
            credential = await db.scalar(
                select(PluginCredentialBindingRecord).where(
                    PluginCredentialBindingRecord.id == credential_binding_id,
                    PluginCredentialBindingRecord.tenant_id == tenant_id,
                    PluginCredentialBindingRecord.installation_id == installation_id,
                    PluginCredentialBindingRecord.status == "active",
                )
            )
            if credential is None:
                raise PermissionError("Credential binding is unavailable")
            if clean_host not in set(json.loads(credential.allowed_hosts_json or "[]")):
                raise PermissionError("Credential binding does not authorize this egress host")

        clean_methods = sorted({str(v).upper().strip() for v in (methods or ["GET"]) if str(v).strip()})
        valid_methods = {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}
        if not clean_methods or not set(clean_methods).issubset(valid_methods):
            raise ValueError("Egress methods are invalid")
        prefixes = sorted({str(v).strip() for v in (path_prefixes or ["/"]) if str(v).strip()})
        if any(not item.startswith("/") for item in prefixes):
            raise ValueError("Egress path prefixes must begin with /")

        row = PluginEgressGrantRecord(
            tenant_id=tenant_id,
            installation_id=installation_id,
            credential_binding_id=credential.id if credential else None,
            host=clean_host,
            methods_json=json.dumps(clean_methods, separators=(",", ":")),
            path_prefixes_json=json.dumps(prefixes, separators=(",", ":")),
            enabled=True,
            created_by=created_by,
        )
        db.add(row)
        await db.flush()
        return row

    async def revoke_binding(
        self,
        db: AsyncSession,
        *,
        tenant_id: str,
        installation_id: str,
        credential_name: str,
    ) -> None:
        row = await db.scalar(
            select(PluginCredentialBindingRecord).where(
                PluginCredentialBindingRecord.tenant_id == tenant_id,
                PluginCredentialBindingRecord.installation_id == installation_id,
                PluginCredentialBindingRecord.credential_name == credential_name,
            )
        )
        if row is None:
            raise LookupError("Credential handle not found")
        row.status = "revoked"
        grants = (
            await db.scalars(
                select(PluginEgressGrantRecord).where(
                    PluginEgressGrantRecord.credential_binding_id == row.id
                )
            )
        ).all()
        for grant in grants:
            grant.enabled = False
        await db.flush()


plugin_credentials = PluginCredentialService()
