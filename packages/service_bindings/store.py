"""Persistent ServiceBinding store.

Bindings contain only capability identifiers and bounded configuration. Provider
credentials remain owned by connector/plugin secret stores and are never copied
into software project records.
"""
from __future__ import annotations

import json
from typing import Any, Mapping

from sqlalchemy import select

from packages.database.software_project_models import ServiceBindingRecord, SoftwareProjectRecord
from packages.service_bindings.contracts import ServiceBinding


_FORBIDDEN_SECRET_KEYS = (
    "password",
    "secret",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "authorization",
    "credential",
    "bearer",
)
_SUPPORTED_BINDING_MODES = frozenset({"capability_gateway"})
_SUPPORTED_PRINCIPAL_SCOPES = frozenset({"project_runtime"})


def _configuration(value: str | None) -> dict:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _safe_configuration(value: Mapping[str, Any] | None) -> dict[str, Any]:
    data = dict(value or {})

    def walk(item: Any, path: str = "configuration") -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                clean = str(key).strip().lower()
                # References/aliases name a secret owned elsewhere; they do not
                # contain the provider credential and are valid binding metadata.
                is_reference = clean.endswith(("_alias", "_reference", "_ref"))
                if not is_reference and any(token in clean for token in _FORBIDDEN_SECRET_KEYS):
                    raise ValueError(
                        f"Service binding cannot persist raw credential field: {path}.{key}"
                    )
                walk(child, f"{path}.{key}")
        elif isinstance(item, (list, tuple)):
            for index, child in enumerate(item):
                walk(child, f"{path}[{index}]")

    walk(data)
    return data


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
    """Durable semantic-to-capability mappings for one SoftwareProject.

    Creating a binding never grants runtime authority. If a registry is supplied,
    explicit current authority is mandatory and the target must resolve now. Every
    actual invocation is independently re-evaluated by CapabilityFirewall.
    """

    def __init__(self, capability_registry=None) -> None:
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
        capability_version: str = "1.0.0",
        binding_mode: str = "capability_gateway",
        principal_scope: str = "project_runtime",
        configuration: Mapping[str, Any] | None = None,
        authority: set[str] | None = None,
    ) -> ServiceBinding:
        await self._project(db, workspace_id, project_id)
        clean_name = " ".join(str(semantic_name or "").split()).strip()
        clean_capability = str(capability_id or "").strip()
        clean_mode = str(binding_mode or "capability_gateway").strip()
        clean_principal_scope = str(principal_scope or "project_runtime").strip()
        if not clean_name:
            raise ValueError("Binding semantic name is required")
        if not clean_capability:
            raise ValueError("Binding capability id is required")
        if clean_mode not in _SUPPORTED_BINDING_MODES:
            raise ValueError("Unsupported service binding mode")
        if clean_principal_scope not in _SUPPORTED_PRINCIPAL_SCOPES:
            raise ValueError("Unsupported service binding principal scope")

        if self.capability_registry is not None:
            if authority is None:
                raise PermissionError("Binding validation requires explicit current authority")
            definition = self.capability_registry.definition(clean_capability)
            self.capability_registry.resolve(
                workspace_id,
                definition.id,
                authority=set(authority),
            )
            clean_capability = definition.id
            capability_version = definition.version

        existing = await db.scalar(
            select(ServiceBindingRecord).where(
                ServiceBindingRecord.project_id == project_id,
                ServiceBindingRecord.semantic_name == clean_name,
            )
        )
        safe = _safe_configuration(configuration)
        payload = json.dumps(safe, sort_keys=True, default=str)
        if existing is None:
            existing = ServiceBindingRecord(
                tenant_id=workspace_id,
                project_id=project_id,
                semantic_name=clean_name[:160],
                capability_id=clean_capability,
                capability_version=str(capability_version or "1.0.0")[:40],
                binding_mode=clean_mode[:40],
                principal_scope=clean_principal_scope[:80],
                configuration_json=payload,
                status="active",
                created_by=user_id,
            )
            db.add(existing)
        else:
            if existing.tenant_id != workspace_id:
                raise PermissionError("Binding belongs to another workspace")
            existing.capability_id = clean_capability
            existing.capability_version = str(capability_version or "1.0.0")[:40]
            existing.binding_mode = clean_mode[:40]
            existing.principal_scope = clean_principal_scope[:80]
            existing.configuration_json = payload
            existing.status = "active"
        await db.flush()
        return _binding(existing)

    async def get(
        self,
        db,
        *,
        workspace_id: str,
        binding_id: str,
        include_inactive: bool = False,
    ) -> ServiceBinding:
        statement = select(ServiceBindingRecord).where(
            ServiceBindingRecord.id == binding_id,
            ServiceBindingRecord.tenant_id == workspace_id,
        )
        if not include_inactive:
            statement = statement.where(ServiceBindingRecord.status == "active")
        row = await db.scalar(statement)
        if row is None:
            raise LookupError("Service binding not found")
        return _binding(row)

    async def list(
        self,
        db,
        *,
        workspace_id: str,
        project_id: str,
        include_inactive: bool = False,
    ) -> tuple[ServiceBinding, ...]:
        await self._project(db, workspace_id, project_id)
        statement = select(ServiceBindingRecord).where(
            ServiceBindingRecord.tenant_id == workspace_id,
            ServiceBindingRecord.project_id == project_id,
        )
        if not include_inactive:
            statement = statement.where(ServiceBindingRecord.status == "active")
        rows = (
            await db.scalars(statement.order_by(ServiceBindingRecord.created_at))
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
