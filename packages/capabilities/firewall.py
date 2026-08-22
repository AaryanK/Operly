"""Canonical capability invocation seam.

The authorization redesign is deliberately deferred. This module makes one
execution boundary real now by adapting the existing trusted ExecutionContext and
ActionService lifecycle without changing current permission/approval semantics.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from packages.actions.service import ActionService
from packages.database.db import session_scope
from packages.security.execution_context import ExecutionContext
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

    def as_dict(self) -> dict[str, Any]:
        result = {
            "ok": self.ok,
            "plugin": self.capability_id,
            "status": self.status,
            "action_id": self.action_id,
            "approval_id": self.approval_id,
            "observation": self.observation,
            "verification": self.verification,
        }
        if self.error:
            result["error"] = self.error
        return result


class ActionBackedCapabilityFirewall:
    """Compatibility firewall backed by the existing ActionService policy.

    Current approval and role behavior is intentionally preserved. The next
    authorization pass can replace ``evaluate``/guard composition behind this
    public seam without changing agents, MCP, Studio, or future capability gateway
    callers.
    """

    def __init__(self, registry) -> None:
        self.registry = registry

    async def evaluate(
        self,
        request: CapabilityInvocation,
        execution_context: ExecutionContext,
    ) -> CapabilityDecision:
        try:
            definition = self.registry.definition(request.capability_id)
        except LookupError:
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
        try:
            definition = self.registry.definition(request.capability_id)
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
            )

        metadata = dict(request.metadata)
        if execution_context.user_id:
            metadata["principal_id"] = f"user:{execution_context.user_id}"
        metadata.setdefault("client_id", request.channel or "operly")
        metadata["authority"] = sorted(authority)

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
                        "temporal_context": temporal.as_dict(),
                    },
                )
            except (ValueError, PermissionError, LookupError) as error:
                return CapabilityInvocationResult(
                    ok=False,
                    capability_id=request.capability_id,
                    status="FAILED",
                    error=str(error),
                )

            await db.commit()
            result = json.loads(action.result_json or "{}")
            return CapabilityInvocationResult(
                ok=action.status in {"VERIFIED", "WAITING_APPROVAL"},
                capability_id=request.capability_id,
                status=str(action.status),
                action_id=action.id,
                approval_id=action.approval_id,
                observation=result.get("evidence", {}),
                verification=json.loads(action.verification_json or "{}"),
            )


CapabilityFirewall = ActionBackedCapabilityFirewall
