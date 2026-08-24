"""Canonical capability invocation seam.

Authorization, schema validation, action lifecycle, and provenance converge here so
provider plugins cannot accidentally bypass the same execution contract used by
agents, Studio, MCP, and scheduled workflows.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from packages.actions.lifecycle import lifecycle_truth, normalize_lifecycle_status
from packages.actions.service import ActionService
from packages.capabilities.validation import PluginSchemaError, validate_arguments
from packages.database.db import session_scope
from packages.security.execution_context import ExecutionContext
from packages.security.surfaces import capability_surface_allowed
from packages.security.temporal_context import resolve_temporal_context


class CapabilityDecision(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class CapabilityInvocation:
    capability_id: str
    arguments: dict[str, Any]
    objective: str
    rationale: str = ""
    expected_outcome: str = ""
    call_id: str | None = None
    channel: str = "operly"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CapabilityInvocationResult:
    ok: bool
    capability_id: str
    status: str
    action_id: str | None = None
    approval_id: str | None = None
    observation: dict[str, Any] = field(default_factory=dict)
    verification: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    errors: tuple[dict[str, str], ...] = ()
    retryable: bool = False
    authority: dict[str, Any] = field(default_factory=dict)
    lifecycle: dict[str, bool] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        result = {
            "ok": self.ok,
            "plugin": self.capability_id,
            "status": self.status,
            "action_id": self.action_id,
            "approval_id": self.approval_id,
            "observation": self.observation,
            "verification": self.verification,
            "retryable": self.retryable,
            "authority": self.authority,
        }
        if self.lifecycle:
            result["lifecycle"] = self.lifecycle
        if self.error:
            result["error"] = self.error
        if self.errors:
            result["errors"] = list(self.errors)
        return result


class ActionBackedCapabilityFirewall:
    """Single policy/validation/action boundary for every capability invocation."""

    def __init__(self, registry) -> None:
        self.registry = registry

    @staticmethod
    def _authority(execution_context: ExecutionContext) -> dict[str, Any]:
        # This boundary is workspace-scoped. Personal connectors must be resolved by
        # the personal runtime or explicit delegation resolver; request metadata alone
        # is never enough to manufacture personal authority here.
        return {
            "owner_type": "workspace",
            "owner_id": execution_context.workspace_id,
            "delegation_id": None,
            "surface": execution_context.surface.value,
        }

    async def evaluate(
        self,
        request: CapabilityInvocation,
        execution_context: ExecutionContext,
    ) -> CapabilityDecision:
        try:
            definition = self.registry.definition(request.capability_id)
        except LookupError:
            return CapabilityDecision.DENY
        if not capability_surface_allowed(definition.id, execution_context.surface):
            return CapabilityDecision.DENY
        if not set(definition.permissions).issubset(set(execution_context.permissions)):
            return CapabilityDecision.DENY
        approval = str(definition.approval_policy)
        if approval.endswith("always"):
            return CapabilityDecision.ASK
        return CapabilityDecision.ALLOW

    async def invoke(
        self,
        request: CapabilityInvocation,
        execution_context: ExecutionContext,
    ) -> CapabilityInvocationResult:
        authority = set(execution_context.permissions)
        authority_source = self._authority(execution_context)
        try:
            definition = self.registry.definition(request.capability_id)
            if not capability_surface_allowed(definition.id, execution_context.surface):
                raise PermissionError("Capability is unavailable on this surface")
            self.registry.resolve(
                execution_context.workspace_id,
                request.capability_id,
                authority=authority,
            )
        except (LookupError, PermissionError) as error:
            return CapabilityInvocationResult(
                ok=False,
                capability_id=request.capability_id,
                status="DENIED",
                error=str(error),
                authority=authority_source,
            )

        # Validate before an Action is created and, critically, before provider code
        # is invoked. Raw JSON duplicate-key rejection happens one layer earlier in
        # AgentRuntime while this check also protects MCP/Studio/direct callers.
        try:
            validate_arguments(definition.input_schema, request.arguments)
        except PluginSchemaError as error:
            return CapabilityInvocationResult(
                ok=False,
                capability_id=request.capability_id,
                status="INVALID_ARGUMENTS",
                error="Capability arguments failed schema validation",
                errors=tuple(error.as_errors()),
                retryable=True,
                authority=authority_source,
            )

        metadata = dict(request.metadata)
        if execution_context.user_id:
            metadata["principal_id"] = f"user:{execution_context.user_id}"
        metadata.setdefault("client_id", request.channel or "operly")
        metadata["authority"] = sorted(authority)
        metadata["authority_source"] = authority_source
        # Surface is canonical execution state. Always overwrite any caller-supplied
        # metadata so providers cannot be tricked into widening private visibility.
        metadata["_surface_kind"] = execution_context.surface.value
        metadata["surface"] = execution_context.surface.value

        async with session_scope() as db:
            temporal = await resolve_temporal_context(
                db,
                user_id=execution_context.user_id,
                tenant_id=execution_context.workspace_id,
            )
            metadata["temporal_context"] = temporal.as_dict()
            service = ActionService(
                db,
                self.registry,
                authority=authority,
                actor_id=execution_context.user_id,
            )
            try:
                action = await service.propose(
                    tenant_id=execution_context.workspace_id,
                    objective=request.objective,
                    capability=request.capability_id,
                    arguments=dict(request.arguments),
                    rationale=(
                        request.rationale
                        or f"Model selected {request.capability_id} for the current objective"
                    )[:2000],
                    expected_outcome=(
                        request.expected_outcome or definition.description
                    )[:2000],
                    risk_level=definition.risk_level,
                    causation_id=request.call_id,
                    idempotency_key=(
                        f"{execution_context.workspace_id}:{request.call_id}"
                        if request.call_id
                        else None
                    ),
                    runtime_context={
                        "channel": request.channel,
                        "metadata": metadata,
                        "authority": sorted(authority),
                        "authority_source": authority_source,
                        "temporal_context": temporal.as_dict(),
                    },
                )
            except PluginSchemaError as error:
                return CapabilityInvocationResult(
                    ok=False,
                    capability_id=request.capability_id,
                    status="INVALID_ARGUMENTS",
                    error="Capability arguments failed schema validation",
                    errors=tuple(error.as_errors()),
                    retryable=True,
                    authority=authority_source,
                )
            except (ValueError, PermissionError, LookupError) as error:
                return CapabilityInvocationResult(
                    ok=False,
                    capability_id=request.capability_id,
                    status="FAILED",
                    error=str(error),
                    authority=authority_source,
                )

            await db.commit()
            result = json.loads(action.result_json or "{}")
            normalized = normalize_lifecycle_status(action.status)
            status = normalized.value
            return CapabilityInvocationResult(
                ok=status in {"VERIFIED", "WAITING_APPROVAL"},
                capability_id=request.capability_id,
                status=status,
                action_id=action.id,
                approval_id=action.approval_id,
                observation=result.get("evidence", {}),
                verification=json.loads(action.verification_json or "{}"),
                authority=authority_source,
                lifecycle=lifecycle_truth(action.status),
            )


CapabilityFirewall = ActionBackedCapabilityFirewall
