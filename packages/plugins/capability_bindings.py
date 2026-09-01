from __future__ import annotations

import json
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.capability_binding_models import CapabilityBindingRecord
from packages.database.plugin_platform_models import PluginInstallationRecord
from packages.kernel.contracts import CapabilitySpec
from packages.security.execution_context import ExecutionContext


class CapabilityBindingService:
    """Create narrow workload handles without copying provider authorization.

    The persisted model is intentionally subject-generic. The baseline provisioning
    adapter currently supports plugin installations; other subject kinds should add an
    explicit ownership validator before they are allowed to create gateway bindings.
    """

    async def _validate_subject(
        self,
        db: AsyncSession,
        *,
        tenant_id: str,
        subject_kind: str,
        subject_id: str,
    ) -> None:
        if subject_kind != "plugin_installation":
            raise ValueError(
                "Capability binding subject is not provisionable yet; add a trusted subject ownership adapter first"
            )
        installation = await db.scalar(
            select(PluginInstallationRecord).where(
                PluginInstallationRecord.id == subject_id,
                PluginInstallationRecord.tenant_id == tenant_id,
            )
        )
        if installation is None:
            raise LookupError("Plugin installation not found for capability binding")
        if installation.status in {"uninstalling", "uninstalled"}:
            raise PermissionError("Plugin installation cannot receive new capability bindings")

    async def create(
        self,
        db: AsyncSession,
        *,
        context: ExecutionContext,
        subject_kind: str,
        subject_id: str,
        semantic_name: str,
        capability: CapabilitySpec,
        configuration: Mapping[str, Any] | None = None,
        argument_constraints: Mapping[str, Any] | None = None,
        rate_policy: Mapping[str, Any] | None = None,
    ) -> CapabilityBindingRecord:
        if not context.is_workspace or not context.workspace_id or not context.user_id:
            raise PermissionError("Capability bindings require an authenticated Workspace member")
        if capability.resource_scope != "workspace" or "workspace" not in capability.scopes:
            raise PermissionError("Only Workspace capabilities may be bound to Workspace workloads")
        missing = [permission for permission in capability.permissions if not context.can(permission)]
        if missing:
            raise PermissionError(
                f"Current principal cannot delegate capability permissions: {', '.join(missing)}"
            )
        clean_kind = str(subject_kind or "").strip().lower()
        clean_subject = str(subject_id or "").strip()
        clean_semantic = str(semantic_name or "").strip().lower()
        if not clean_subject or not clean_semantic:
            raise ValueError("Capability binding subject_id and semantic_name are required")
        if len(clean_semantic) > 160:
            raise ValueError("Capability binding semantic_name is too long")
        await self._validate_subject(
            db,
            tenant_id=context.workspace_id,
            subject_kind=clean_kind,
            subject_id=clean_subject,
        )
        existing = await db.scalar(
            select(CapabilityBindingRecord).where(
                CapabilityBindingRecord.tenant_id == context.workspace_id,
                CapabilityBindingRecord.subject_kind == clean_kind,
                CapabilityBindingRecord.subject_id == clean_subject,
                CapabilityBindingRecord.semantic_name == clean_semantic,
            )
        )
        if existing is not None:
            raise ValueError("Capability binding semantic name already exists for this subject")
        row = CapabilityBindingRecord(
            tenant_id=context.workspace_id,
            subject_kind=clean_kind,
            subject_id=clean_subject,
            semantic_name=clean_semantic,
            capability_id=capability.id,
            capability_version=capability.version,
            authority_user_id=context.user_id,
            configuration_json=json.dumps(
                dict(configuration or {}), separators=(",", ":"), sort_keys=True
            ),
            argument_constraints_json=json.dumps(
                dict(argument_constraints or {}), separators=(",", ":"), sort_keys=True
            ),
            rate_policy_json=json.dumps(
                dict(rate_policy or {}), separators=(",", ":"), sort_keys=True
            ),
            status="active",
            enabled=True,
            created_by=context.user_id,
        )
        db.add(row)
        await db.flush()
        return row

    async def get(
        self,
        db: AsyncSession,
        *,
        tenant_id: str,
        binding_id: str,
    ) -> CapabilityBindingRecord:
        row = await db.scalar(
            select(CapabilityBindingRecord).where(
                CapabilityBindingRecord.id == binding_id,
                CapabilityBindingRecord.tenant_id == tenant_id,
            )
        )
        if row is None:
            raise LookupError("Capability binding not found")
        return row

    async def list_for_subject(
        self,
        db: AsyncSession,
        *,
        tenant_id: str,
        subject_kind: str,
        subject_id: str,
    ) -> list[CapabilityBindingRecord]:
        return list(
            (
                await db.scalars(
                    select(CapabilityBindingRecord)
                    .where(
                        CapabilityBindingRecord.tenant_id == tenant_id,
                        CapabilityBindingRecord.subject_kind == subject_kind,
                        CapabilityBindingRecord.subject_id == subject_id,
                    )
                    .order_by(CapabilityBindingRecord.created_at.asc())
                )
            ).all()
        )

    async def revoke(
        self,
        db: AsyncSession,
        *,
        tenant_id: str,
        binding_id: str,
    ) -> None:
        row = await self.get(db, tenant_id=tenant_id, binding_id=binding_id)
        row.status = "revoked"
        row.enabled = False
        await db.flush()


capability_bindings = CapabilityBindingService()
