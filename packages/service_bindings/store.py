"""Persistent ServiceBinding store.

Bindings contain only capability identifiers and bounded configuration. Provider
credentials remain owned by connector/plugin secret stores and are never copied
into software project records.
"""
from __future__ import annotations

import json

from sqlalchemy import select

from packages.database.software_project_models import ServiceBindingRecord, SoftwareProjectRecord
from packages.service_bindings.contracts import ServiceBinding


def _configuration(value: str | None) -> dict:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _binding(row: ServiceBindingRecord) -> ServiceBinding:
    return ServiceBinding(
        id=row.id,
        project_id=row.project_id,
        workspace_id=row.tenant_id,
        semantic_name=row.semantic_name,
        capability_id=row.capability_id,
        capability_version=row.capability_version,
        binding_mode=row.binding_mode,
        principal_scope=row.principal_scope,
        configuration=_configuration(row.configuration_json),
        created_at=row.created_at,
    )


class ServiceBindingStore:
    def __init__(self, capability_registry) -> None:
        self.capability_registry = capability_registry

    async def _project(self, db, workspace_id: str, project_id: str) -> SoftwareProjectRecord:
        row = await db.scalar(
            select(SoftwareProjectRecord).where(
                SoftwareProjectRecord.id == project_id,
                SoftwareProjectRecord.tenant_id == workspace_id,
            )
        )
        if row is None:
            raise LookupError("Software project not found")
        return row

    async def create(
        self,
        db,
        *,
        workspace_id: str,
        project_id: str,
        user_id: str,
        semantic_name: str,
        capability_id: str,
        binding_mode: str = "capability_gateway",
        principal_scope: str = "project_runtime",
        configuration: dict | None = None,
    ) -> ServiceBinding:
        await self._project(db, workspace_id, project_id)
        clean_name = " ".join(str(semantic_name or "").split()).strip()
        if not clean_name:
            raise ValueError("Binding semantic name is required")

        definition = self.capability_registry.definition(str(capability_id or "").strip())
        # This verifies plugin installation/configuration at binding time. It does
        # not grant execution authority; the CapabilityFirewall re-evaluates every
        # invocation under the runtime principal in the authorization pass.
        self.capability_registry.resolve(workspace_id, definition.id)

        existing = await db.scalar(
            select(ServiceBindingRecord).where(
                ServiceBindingRecord.project_id == project_id,
                ServiceBindingRecord.semantic_name == clean_name,
            )
        )
        payload = json.dumps(configuration or {}, sort_keys=True, default=str)
        if existing is None:
            existing = ServiceBindingRecord(
                tenant_id=workspace_id,
                project_id=project_id,
                semantic_name=clean_name[:160],
                capability_id=definition.id,
                capability_version=definition.version,
                binding_mode=str(binding_mode or "capability_gateway")[:40],
                principal_scope=str(principal_scope or "project_runtime")[:80],
                configuration_json=payload,
                status="active",
                created_by=user_id,
            )
            db.add(existing)
        else:
            if existing.tenant_id != workspace_id:
                raise PermissionError("Binding belongs to another workspace")
            existing.capability_id = definition.id
            existing.capability_version = definition.version
            existing.binding_mode = str(binding_mode or "capability_gateway")[:40]
            existing.principal_scope = str(principal_scope or "project_runtime")[:80]
            existing.configuration_json = payload
            existing.status = "active"
        await db.flush()
        return _binding(existing)

    async def get(self, db, *, workspace_id: str, binding_id: str) -> ServiceBinding:
        row = await db.scalar(
            select(ServiceBindingRecord).where(
                ServiceBindingRecord.id == binding_id,
                ServiceBindingRecord.tenant_id == workspace_id,
                ServiceBindingRecord.status == "active",
            )
        )
        if row is None:
            raise LookupError("Service binding not found")
        return _binding(row)

    async def list(self, db, *, workspace_id: str, project_id: str) -> tuple[ServiceBinding, ...]:
        await self._project(db, workspace_id, project_id)
        rows = (
            await db.scalars(
                select(ServiceBindingRecord)
                .where(
                    ServiceBindingRecord.tenant_id == workspace_id,
                    ServiceBindingRecord.project_id == project_id,
                    ServiceBindingRecord.status == "active",
                )
                .order_by(ServiceBindingRecord.created_at)
            )
        ).all()
        return tuple(_binding(row) for row in rows)

    async def revoke(self, db, *, workspace_id: str, binding_id: str) -> None:
        row = await db.scalar(
            select(ServiceBindingRecord).where(
                ServiceBindingRecord.id == binding_id,
                ServiceBindingRecord.tenant_id == workspace_id,
            )
        )
        if row is None:
            raise LookupError("Service binding not found")
        row.status = "revoked"
        await db.flush()
