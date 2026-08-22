"""Service-binding persistence, discovery, and capability-gateway execution."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable, Mapping

from sqlalchemy import select

from packages.capabilities.firewall import (
    ActionBackedCapabilityFirewall,
    CapabilityInvocation,
    CapabilityInvocationResult,
)
from packages.database.software_project_models import ServiceBindingRecord, SoftwareProjectRecord
from packages.security.execution_context import ExecutionContext
from packages.service_bindings.contracts import BindingCandidate, BindingInvocation, ServiceBinding


_FORBIDDEN_SECRET_KEYS = ("password", "secret", "api_key", "apikey", "access_token", "refresh_token", "authorization")


def _safe_binding_configuration(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Reject obvious raw credentials before binding state reaches persistence.

    Credential *aliases/references* are allowed because the target architecture
    intentionally keeps provider secrets behind Operly. Secret values themselves
    must never be embedded in project/binding configuration.
    """
    data = dict(value or {})

    def walk(item: Any, path: str = "configuration") -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                clean = str(key).strip().lower()
                is_alias = clean.endswith(("_alias", "_reference", "_ref"))
                if not is_alias and any(token in clean for token in _FORBIDDEN_SECRET_KEYS):
                    raise ValueError(f"Service binding cannot persist raw credential field: {path}.{key}")
                walk(child, f"{path}.{key}")
        elif isinstance(item, (list, tuple)):
            for index, child in enumerate(item):
                walk(child, f"{path}[{index}]")

    walk(data)
    return data


def _binding(row: ServiceBindingRecord) -> ServiceBinding:
    try:
        configuration = json.loads(row.configuration_json or "{}")
    except (TypeError, json.JSONDecodeError):
        configuration = {}
    if not isinstance(configuration, dict):
        configuration = {}
    return ServiceBinding(
        id=row.id,
        project_id=row.project_id,
        workspace_id=row.tenant_id,
        semantic_name=row.semantic_name,
        capability_id=row.capability_id,
        capability_version=row.capability_version,
        binding_mode=row.binding_mode,
        principal_scope=row.principal_scope,
        configuration=configuration,
        created_at=row.created_at,
    )


class ServiceBindingStore:
    """Durable project-scoped binding storage.

    This service does not grant capability authority. It stores the selected
    semantic-to-capability mapping; runtime invocation still crosses the canonical
    CapabilityFirewall using a trusted ExecutionContext.
    """

    async def create(
        self,
        db,
        *,
        workspace_id: str,
        project_id: str,
        semantic_name: str,
        capability_id: str,
        capability_version: str = "1.0.0",
        binding_mode: str = "capability_gateway",
        principal_scope: str = "project_runtime",
        configuration: Mapping[str, Any] | None = None,
        created_by: str = "",
        capability_registry=None,
        authority: set[str] | None = None,
    ) -> ServiceBinding:
        project = await db.scalar(
            select(SoftwareProjectRecord).where(
                SoftwareProjectRecord.id == project_id,
                SoftwareProjectRecord.tenant_id == workspace_id,
            )
        )
        if project is None:
            raise LookupError("Software project not found")

        clean_semantic = " ".join(str(semantic_name or "").split()).strip()[:160]
        clean_capability = str(capability_id or "").strip()
        if not clean_semantic or not clean_capability:
            raise ValueError("Service binding requires semantic name and capability id")

        if capability_registry is not None:
            definition = capability_registry.definition(clean_capability)
            # If authority is supplied, validate it now. Omitting authority is useful
            # for planning/draft bindings; it never changes runtime firewall checks.
            if authority is not None:
                capability_registry.resolve(
                    workspace_id,
                    definition.id,
                    authority=set(authority),
                )
            capability_version = definition.version

        safe_configuration = _safe_binding_configuration(configuration)
        existing = await db.scalar(
            select(ServiceBindingRecord).where(
                ServiceBindingRecord.tenant_id == workspace_id,
                ServiceBindingRecord.project_id == project_id,
                ServiceBindingRecord.semantic_name == clean_semantic,
            )
        )
        if existing is None:
            row = ServiceBindingRecord(
                tenant_id=workspace_id,
                project_id=project_id,
                semantic_name=clean_semantic,
                capability_id=clean_capability,
                capability_version=str(capability_version or "1.0.0")[:40],
                binding_mode=str(binding_mode or "capability_gateway")[:40],
                principal_scope=str(principal_scope or "project_runtime")[:80],
                configuration_json=json.dumps(safe_configuration, sort_keys=True, default=str),
                status="active",
                created_by=created_by,
            )
            db.add(row)
            await db.flush()
        else:
            row = existing
            row.capability_id = clean_capability
            row.capability_version = str(capability_version or "1.0.0")[:40]
            row.binding_mode = str(binding_mode or "capability_gateway")[:40]
            row.principal_scope = str(principal_scope or "project_runtime")[:80]
            row.configuration_json = json.dumps(safe_configuration, sort_keys=True, default=str)
            row.status = "active"
            await db.flush()
        return _binding(row)

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

    async def deactivate(
        self,
        db,
        *,
        workspace_id: str,
        binding_id: str,
    ) -> ServiceBinding:
        row = await db.scalar(
            select(ServiceBindingRecord).where(
                ServiceBindingRecord.id == binding_id,
                ServiceBindingRecord.tenant_id == workspace_id,
            )
        )
        if row is None:
            raise LookupError("Service binding not found")
        row.status = "inactive"
        await db.flush()
        return _binding(row)


class ServiceBindingResolver:
    """Resolve semantic software operations against workspace capabilities."""

    def __init__(self, capability_registry) -> None:
        self.capability_registry = capability_registry

    def candidates(
        self,
        *,
        workspace_id: str,
        operation: str,
        authority: set[str] | None = None,
        categories: Iterable[str] = (),
        tags: Iterable[str] = (),
        limit: int = 12,
    ) -> tuple[BindingCandidate, ...]:
        rows = self.capability_registry.search(
            workspace_id,
            operation,
            authority=authority,
            categories=categories,
            tags=tags,
            limit=limit,
        )
        return tuple(
            BindingCandidate(
                capability_id=row["id"],
                version=row["version"],
                display_name=row["display_name"],
                description=row["description"],
                risk=row["risk"],
                authorized=row.get("authorized"),
                configured=bool(row.get("configured", True)),
                score=max(0, limit - index),
            )
            for index, row in enumerate(rows)
        )


BindingLoader = Callable[[str], Awaitable[ServiceBinding]]
RegistryLoader = Callable[[str], Awaitable[Any] | Any]


@dataclass(slots=True)
class CapabilityGateway:
    """Project/runtime entrypoint for invoking one configured binding handle.

    The gateway accepts a binding id rather than a provider credential or arbitrary
    capability id. Authorization semantics are inherited from the normal
    CapabilityFirewall and will be refined in the dedicated authorization pass.
    """

    binding_loader: BindingLoader
    registry_loader: RegistryLoader

    async def invoke(
        self,
        invocation: BindingInvocation,
        *,
        execution_context: ExecutionContext,
        project_id: str,
    ) -> CapabilityInvocationResult:
        binding = await self.binding_loader(invocation.binding_id)
        if binding.project_id != project_id:
            return CapabilityInvocationResult(
                ok=False,
                capability_id=binding.capability_id,
                status="DENIED",
                error="Binding does not belong to this project",
            )
        if binding.workspace_id != execution_context.workspace_id:
            return CapabilityInvocationResult(
                ok=False,
                capability_id=binding.capability_id,
                status="DENIED",
                error="Binding does not belong to this workspace",
            )

        arguments = dict(invocation.arguments)
        allowed_fields = binding.configuration.get("allowed_argument_fields")
        if isinstance(allowed_fields, (list, tuple, set)):
            allowed = {str(item) for item in allowed_fields}
            extra = sorted(set(arguments) - allowed)
            if extra:
                return CapabilityInvocationResult(
                    ok=False,
                    capability_id=binding.capability_id,
                    status="DENIED",
                    error="Binding arguments exceed the declared field scope",
                )

        defaults = binding.configuration.get("argument_defaults")
        if isinstance(defaults, dict):
            for key, value in defaults.items():
                arguments.setdefault(str(key), value)

        loaded = self.registry_loader(execution_context.workspace_id)
        registry = await loaded if hasattr(loaded, "__await__") else loaded
        firewall = ActionBackedCapabilityFirewall(registry)
        return await firewall.invoke(
            CapabilityInvocation(
                capability_id=binding.capability_id,
                arguments=arguments,
                objective=f"Software project {project_id} service binding {binding.semantic_name}",
                rationale="Project runtime invoked an explicitly configured service binding",
                expected_outcome=f"Complete bound operation {binding.semantic_name}",
                call_id=invocation.request_id,
                channel="software_runtime",
                metadata={
                    "project_id": project_id,
                    "service_binding_id": binding.id,
                    "binding_mode": binding.binding_mode,
                    "principal_scope": binding.principal_scope,
                },
            ),
            execution_context,
        )
