"""Service-binding discovery and capability-gateway execution."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable

from packages.capabilities.firewall import (
    ActionBackedCapabilityFirewall,
    CapabilityInvocation,
    CapabilityInvocationResult,
)
from packages.security.execution_context import ExecutionContext
from packages.service_bindings.contracts import BindingCandidate, BindingInvocation, ServiceBinding


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
    """Project/runtime entrypoint for invoking one pre-authorized binding handle.

    The gateway accepts a binding id rather than a provider credential or arbitrary
    capability id. Authorization semantics are still inherited from the normal
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
