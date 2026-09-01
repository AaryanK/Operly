from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.capability_binding_models import CapabilityBindingRecord
from packages.database.plugin_platform_models import (
    PluginInstallationRecord,
    PluginRuntimeIdentityRecord,
)


@dataclass(frozen=True, slots=True)
class IssuedRuntimeIdentity:
    identity_id: str
    token: str
    expires_at: datetime


class RuntimeBindingService:
    """Short-lived workload identity for future Capability Gateway calls.

    Only token hashes are persisted. A runtime identity references explicit universal
    CapabilityBinding IDs and never contains provider credentials or arbitrary
    Workspace permissions. The Capability Gateway must still resolve current runtime,
    binding and Kernel authority before every invocation.
    """

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    async def issue(
        self,
        db: AsyncSession,
        *,
        tenant_id: str,
        installation_id: str,
        runtime_instance_id: str | None,
        allowed_binding_ids: list[str],
        ttl_seconds: int = 900,
    ) -> IssuedRuntimeIdentity:
        installation = await db.scalar(
            select(PluginInstallationRecord).where(
                PluginInstallationRecord.id == installation_id,
                PluginInstallationRecord.tenant_id == tenant_id,
                PluginInstallationRecord.enabled.is_(True),
                PluginInstallationRecord.status == "active",
            )
        )
        if installation is None:
            raise PermissionError("Plugin installation is not active")
        normalized = sorted(
            {str(item).strip() for item in allowed_binding_ids if str(item).strip()}
        )
        if len(normalized) > 100:
            raise ValueError("Too many capability bindings requested for one runtime identity")
        for binding_id in normalized:
            binding = await db.scalar(
                select(CapabilityBindingRecord).where(
                    CapabilityBindingRecord.id == binding_id,
                    CapabilityBindingRecord.tenant_id == tenant_id,
                    CapabilityBindingRecord.subject_kind == "plugin_installation",
                    CapabilityBindingRecord.subject_id == installation_id,
                    CapabilityBindingRecord.status == "active",
                    CapabilityBindingRecord.enabled.is_(True),
                )
            )
            if binding is None:
                raise PermissionError(f"Capability binding is unavailable: {binding_id}")
        ttl = max(60, min(int(ttl_seconds), 3600))
        expires_at = datetime.utcnow() + timedelta(seconds=ttl)
        token = "opr_" + secrets.token_urlsafe(36)
        row = PluginRuntimeIdentityRecord(
            tenant_id=tenant_id,
            installation_id=installation_id,
            runtime_instance_id=runtime_instance_id,
            token_hash=self._hash(token),
            allowed_bindings_json=json.dumps(normalized, separators=(",", ":")),
            issued_to=f"plugin:{installation_id}",
            expires_at=expires_at,
        )
        db.add(row)
        await db.flush()
        return IssuedRuntimeIdentity(
            identity_id=row.id,
            token=token,
            expires_at=expires_at,
        )

    async def authenticate(
        self,
        db: AsyncSession,
        *,
        token: str,
    ) -> PluginRuntimeIdentityRecord:
        row = await db.scalar(
            select(PluginRuntimeIdentityRecord).where(
                PluginRuntimeIdentityRecord.token_hash == self._hash(token)
            )
        )
        now = datetime.utcnow()
        if row is None or row.revoked_at is not None or row.expires_at <= now:
            raise PermissionError("Runtime identity is invalid or expired")
        installation = await db.scalar(
            select(PluginInstallationRecord).where(
                PluginInstallationRecord.id == row.installation_id,
                PluginInstallationRecord.tenant_id == row.tenant_id,
                PluginInstallationRecord.enabled.is_(True),
                PluginInstallationRecord.status == "active",
            )
        )
        if installation is None:
            raise PermissionError("Runtime identity installation is no longer active")
        return row

    async def resolve_binding(
        self,
        db: AsyncSession,
        *,
        identity: PluginRuntimeIdentityRecord,
        binding_id: str,
    ) -> CapabilityBindingRecord:
        allowed = set(json.loads(identity.allowed_bindings_json or "[]"))
        if binding_id not in allowed:
            raise PermissionError("Runtime identity is not delegated this capability binding")
        binding = await db.scalar(
            select(CapabilityBindingRecord).where(
                CapabilityBindingRecord.id == binding_id,
                CapabilityBindingRecord.tenant_id == identity.tenant_id,
                CapabilityBindingRecord.subject_kind == "plugin_installation",
                CapabilityBindingRecord.subject_id == identity.installation_id,
                CapabilityBindingRecord.status == "active",
                CapabilityBindingRecord.enabled.is_(True),
            )
        )
        if binding is None:
            raise PermissionError("Capability binding is unavailable")
        return binding

    async def revoke(
        self,
        db: AsyncSession,
        *,
        identity_id: str,
        tenant_id: str,
    ) -> None:
        row = await db.scalar(
            select(PluginRuntimeIdentityRecord).where(
                PluginRuntimeIdentityRecord.id == identity_id,
                PluginRuntimeIdentityRecord.tenant_id == tenant_id,
            )
        )
        if row is None:
            raise LookupError("Runtime identity not found")
        row.revoked_at = datetime.utcnow()
        await db.flush()


runtime_bindings = RuntimeBindingService()
