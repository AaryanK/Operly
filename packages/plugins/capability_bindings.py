from __future__ import annotations

import json
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.capability_binding_models import CapabilityBindingRecord
from packages.kernel.contracts import CapabilitySpec
from packages.security.execution_context import ExecutionContext


class CapabilityBindingService:
    """Create narrow workload handles to capabilities without copying provider auth."""

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
        if not context.is_workspace or not context.workspace_id:
            raise PermissionError("Capability bindings require Workspace authority")
        if capability.resource_scope != "workspace" or "workspace" not in capability.scopes:
            raise PermissionError("Only Workspace capabilities may be bound to Workspace workloads")
        missing = [permission for permission in capability.permissions if not context.can(permission)]
        if missing:
            raise PermissionError(
                f"Current principal cannot delegate capability permissions: {', '.join(missing)}"
            )
        clean_kind = str(subject_kind or "").strip().lower()
        if clean_kind not in {"software_project", "plugin_installation", "solution", "workflow"}:
            raise ValueError("Unsupported capability binding subject_kind")
        clean_subject = str(subject_id or "").strip()
        clean_semantic = str(semantic_name or "").strip().lower()
        if not clean_subject or not clean_semantic:
            raise ValueError("Capability binding subject_id and semantic_name are required")
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
            configuration_json=json.dumps(dict(configuration or {}), separators=(",", ":"), sort_keys=True),
            argument_constraints_json=json.dumps(
                dict(argument_constraints or {}), separators=(",", ":"), sort_keys=True
            ),
            rate_policy_json=json.dumps(dict(rate_policy or {}), separators=(",", ":"), sort_keys=True),
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
