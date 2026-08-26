"""Canonical capability invocation seam.

Authorization, schema validation, action lifecycle, and provenance converge here so
provider plugins cannot bypass the same execution contract used by agents, Studio,
MCP, scheduled workflows, humans, or delegated software runtimes.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from packages.actions.attributed_service import AttributedActionService
from packages.actions.lifecycle import lifecycle_truth, normalize_lifecycle_status
from packages.capabilities.validation import PluginSchemaError, validate_arguments
from packages.database.db import session_scope
from packages.security.delegation import (
    delegation_allows,
    delegation_authority,
    effective_principal_key,
)
from packages.security.execution_context import ExecutionContext, ScopeKind
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
        delegated = delegation_authority(execution_context)
        if execution_context.scope_kind is ScopeKind.PERSONAL:
            return {
                "owner_type": "personal",
                "owner_id": execution_context.user_id,
                "scope_id": execution_context.scope_id,
                "surface": execution_context.surface.value,
                **delegated,
            }
        return {
            "owner_type": "workspace",
            "owner_id": execution_context.workspace_id,
            "scope_id": execution_context.scope_id,
            "workspace_mode": execution_context.workspace_mode,
            "surface": execution_context.surface.value,
            **delegated,
        }

    @staticmethod
    def _actor_chain(
        metadata: dict[str, Any],
        execution_context: ExecutionContext,
    ) -> tuple[str, str | None, str, str | None, list[dict[str, Any]]]:
        principal = str(
            metadata.get("initiator_id")
            or effective_principal_key(execution_context)
            or ""
        ).strip() or None
        if metadata.get("initiator_type"):
            initiator_type = str(metadata["initiator_type"])
        elif principal and principal.startswith("guest:"):
            initiator_type = "guest"
        elif execution_context.user_id:
            initiator_type = "user"
        else:
            initiator_type = "principal"

        executor_type = str(metadata.get("executor_type") or initiator_type)
        executor_id = str(metadata.get("executor_id") or principal or "").strip() or None
        raw_delegation = metadata.get("delegation_chain")
        delegation = [
            dict(item)
            for item in (raw_delegation if isinstance(raw_delegation, list) else [])
            if isinstance(item, dict)
        ]
        return initiator_type, principal, executor_type, executor_id, delegation

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
        if not delegation_allows(execution_context, definition.id):
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
        # Capability exposure is only a usability optimization. Invocation repeats the
        # deterministic permission/surface/delegation checks and therefore fails closed
        # even if a caller fabricates a tool call that the model never saw.
        authority = set(execution_context.permissions)
        authority_source = self._authority(execution_context)
        scope_id = execution_context.scope_id
        if not scope_id:
            return CapabilityInvocationResult(
                ok=False,
                capability_id=request.capability_id,
                status="DENIED",
                error="Execution scope is unavailable",
                authority=authority_source,
            )

        try:
            definition = self.registry.definition(request.capability_id)
            if not capability_surface_allowed(definition.id, execution_context.surface):
                raise PermissionError("Capability is unavailable on this surface")
            if not delegation_allows(execution_context, definition.id):
                raise PermissionError("Capability is outside delegated principal scope")
            self.registry.resolve(
                scope_id,
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
        principal_key = effective_principal_key(execution_context)
        if principal_key:
            metadata["principal_id"] = principal_key
        delegated = delegation_authority(execution_context)
        metadata["principal_kind"] = delegated.get("principal_kind")
        metadata["delegation_id"] = delegated.get("delegation_id")
        if delegated.get("delegator_user_id"):
            metadata["delegator_user_id"] = delegated["delegator_user_id"]
        if delegated.get("parent_principal_id"):
            metadata["parent_principal_id"] = delegated["parent_principal_id"]
        metadata.setdefault("client_id", request.channel or "operly")
        metadata["authority"] = sorted(authority)
        metadata["authority_source"] = authority_source
        metadata["scope_kind"] = execution_context.scope_kind.value
        metadata["scope_id"] = scope_id
        metadata["workspace_mode"] = execution_context.workspace_mode
        if execution_context.focus_workspace_id:
            metadata["focus_workspace_id"] = execution_context.focus_workspace_id
        metadata["_surface_kind"] = execution_context.surface.value
        metadata["surface"] = execution_context.surface.value

        (
            initiator_type,
            initiator_id,
            executor_type,
            executor_id,
            delegation_chain,
        ) = self._actor_chain(metadata, execution_context)
        metadata["initiator_type"] = initiator_type
        metadata["initiator_id"] = initiator_id
        metadata["executor_type"] = executor_type
        metadata["executor_id"] = executor_id
        metadata["delegation_chain"] = delegation_chain
        metadata["actor_chain"] = {
            "initiator": {"type": initiator_type, "id": initiator_id},
            "executor": {"type": executor_type, "id": executor_id},
            "delegation": delegation_chain,
        }

        async with session_scope() as db:
            temporal = await resolve_temporal_context(
                db,
                user_id=execution_context.user_id,
                tenant_id=(
                    execution_context.workspace_id
                    if execution_context.scope_kind is ScopeKind.WORKSPACE
                    else execution_context.focus_workspace_id
                ),
            )
            metadata["temporal_context"] = temporal.as_dict()
            service = AttributedActionService(
                db,
                self.registry,
                authority=authority,
                actor_id=execution_context.user_id,
                initiator_type=initiator_type,
                initiator_id=initiator_id,
                executor_type=executor_type,
                executor_id=executor_id,
                delegation_chain=delegation_chain,
            )
            try:
                action = await service.propose(
                    tenant_id=(
                        execution_context.workspace_id
                        if execution_context.scope_kind is ScopeKind.WORKSPACE
                        else None
                    ),
                    owner_user_id=(
                        execution_context.user_id
                        if execution_context.scope_kind is ScopeKind.PERSONAL
                        else None
                    ),
                    scope_kind=execution_context.scope_kind,
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
                    idempotency_key=(f"{scope_id}:{request.call_id}" if request.call_id else None),
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
