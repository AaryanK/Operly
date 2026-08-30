from __future__ import annotations

from dataclasses import dataclass

from packages.kernel.contracts import AuthorizationDecision, CapabilitySpec
from packages.security.execution_context import ExecutionContext
from packages.security.surfaces import capability_surface_allowed


@dataclass(frozen=True, slots=True)
class AuthorizationResult:
    decision: AuthorizationDecision
    reason: str


class CapabilityPolicyEngine:
    """Fail-closed deterministic policy boundary.

    The model/planner may request a capability, but it cannot grant scope, permissions,
    surface visibility, or approval. Those are recomputed from trusted context here.
    """

    def evaluate(self, context: ExecutionContext, spec: CapabilitySpec) -> AuthorizationResult:
        if context.scope_kind.value not in spec.scopes:
            return AuthorizationResult(AuthorizationDecision.DENY, "capability scope mismatch")
        if not capability_surface_allowed(spec.id, context.surface):
            return AuthorizationResult(AuthorizationDecision.DENY, "capability is hidden on this surface")
        missing = [permission for permission in spec.permissions if not context.can(permission)]
        if missing:
            return AuthorizationResult(
                AuthorizationDecision.DENY,
                "missing permissions: " + ", ".join(sorted(missing)),
            )
        if spec.approval_required:
            return AuthorizationResult(
                AuthorizationDecision.ASK,
                "capability requires explicit approval",
            )
        return AuthorizationResult(AuthorizationDecision.ALLOW, "authorized")
