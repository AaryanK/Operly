from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.capability_binding_models import CapabilityBindingRecord
from packages.kernel.contracts import RuntimeRequest
from packages.kernel.runtime import RuntimeExecutionError
from packages.plugins.bindings import RuntimeBindingService
from packages.plugins.budgets import ResourceBudgetExceeded, ResourceBudgetService
from packages.security.execution_context import resolve_execution_context
from packages.security.surfaces import SurfaceKind
from packages.workspace_modules.tools.runtime import build_workspace_runtime


@dataclass(frozen=True, slots=True)
class GatewayInvocationResult:
    status: str
    request_id: str
    run_id: str | None
    result: Mapping[str, Any] | None = None
    approval_id: str | None = None


def _apply_constraints(binding: CapabilityBindingRecord, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Apply monotonic argument narrowing stored on a binding.

    Supported baseline constraints are intentionally simple and deterministic. More
    expressive constraints can later move to a versioned policy schema without
    changing the Capability Gateway identity model.
    """
    constraints = json.loads(binding.argument_constraints_json or "{}")
    if not isinstance(constraints, dict):
        raise PermissionError("Capability binding argument constraints are invalid")
    result = dict(arguments)
    allowed_fields = constraints.get("allowed_fields")
    if allowed_fields is not None:
        allowed = {str(item) for item in allowed_fields}
        unexpected = set(result) - allowed
        if unexpected:
            raise PermissionError(
                "Capability binding does not allow argument fields: "
                + ", ".join(sorted(unexpected))
            )
    fixed = constraints.get("fixed") or {}
    if not isinstance(fixed, dict):
        raise PermissionError("Capability binding fixed constraints are invalid")
    for key, expected in fixed.items():
        if key in result and result[key] != expected:
            raise PermissionError(f"Capability binding fixes argument {key}")
        result[key] = expected
    enum = constraints.get("enum") or {}
    if not isinstance(enum, dict):
        raise PermissionError("Capability binding enum constraints are invalid")
    for key, choices in enum.items():
        if key not in result:
            continue
        if not isinstance(choices, list) or result[key] not in choices:
            raise PermissionError(f"Capability binding rejects argument {key}")
    required = {str(item) for item in (constraints.get("required_fields") or [])}
    missing = required - set(result)
    if missing:
        raise PermissionError(
            "Capability binding requires argument fields: " + ", ".join(sorted(missing))
        )
    return result


class CapabilityGatewayService:
    """Canonical bridge from isolated digital workloads into Workspace capabilities."""

    def __init__(self) -> None:
        self.runtime_bindings = RuntimeBindingService()
        self.budgets = ResourceBudgetService()

    async def invoke(
        self,
        db: AsyncSession,
        *,
        runtime_token: str,
        binding_id: str,
        arguments: Mapping[str, Any],
        goal: str = "Hosted digital workload invocation",
        request_id: str | None = None,
        approval_id: str | None = None,
    ) -> GatewayInvocationResult:
        identity = await self.runtime_bindings.authenticate(db, token=runtime_token)
        binding = await self.runtime_bindings.resolve_binding(
            db, identity=identity, binding_id=binding_id
        )
        if binding.authority_user_id is None:
            raise PermissionError("Capability binding has no delegated authority principal")
        if binding.subject_kind != "plugin_installation" or binding.subject_id != identity.installation_id:
            raise PermissionError("Runtime identity does not own this capability binding")

        context = await resolve_execution_context(
            db,
            workspace_id=binding.tenant_id,
            user_id=binding.authority_user_id,
            channel="capability_gateway",
            surface=SurfaceKind.PLUGIN_RUNTIME,
            conversation_id=f"plugin-runtime:{identity.installation_id}",
            metadata={
                "runtime_identity_id": identity.id,
                "plugin_installation_id": identity.installation_id,
                "capability_binding_id": binding.id,
            },
        )
        runtime = build_workspace_runtime()
        spec = runtime.registry.get(binding.capability_id)
        if spec.version != binding.capability_version:
            raise PermissionError(
                "Capability binding version is stale; rebind against the current capability contract"
            )

        narrowed_arguments = _apply_constraints(binding, arguments)
        resolved_request_id = request_id or f"gateway:{identity.id}:{uuid4()}"
        # The usage metric is consumed before the side effect. If a hard quota rejects
        # the call, Kernel is never entered. Retry/idempotency remains Kernel-owned.
        await self.budgets.consume(
            db,
            tenant_id=binding.tenant_id,
            subject_kind="plugin_installation",
            subject_id=identity.installation_id,
            metric="capability_invocations",
            quantity=1,
            reference_kind="capability_binding",
            reference_id=binding.id,
        )
        response = await runtime.execute(
            db,
            context=context,
            request=RuntimeRequest(
                goal=str(goal or "Hosted digital workload invocation")[:4000],
                capability_id=binding.capability_id,
                arguments=narrowed_arguments,
                conversation_id=f"plugin-runtime:{identity.installation_id}",
                request_id=resolved_request_id,
                approval_id=approval_id,
            ),
        )
        return GatewayInvocationResult(
            status="completed",
            request_id=resolved_request_id,
            run_id=response.run_id,
            result=response.result or {},
        )


capability_gateway = CapabilityGatewayService()

__all__ = [
    "CapabilityGatewayService",
    "GatewayInvocationResult",
    "ResourceBudgetExceeded",
    "RuntimeExecutionError",
    "capability_gateway",
]
