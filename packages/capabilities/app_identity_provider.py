from __future__ import annotations

from packages.app_identity.contracts import APP_IDENTITY_CAPABILITY_ID
from packages.capabilities.contracts import (
    ApprovalPolicy,
    CapabilityDefinition,
    CapabilityResult,
    ExecutionMode,
)
from packages.capabilities.providers import BaseProvider


class AppIdentityProvider(BaseProvider):
    """Binding-only identity capability for users of generated software."""

    name = "operly_app_identity"
    capabilities = (
        CapabilityDefinition(
            APP_IDENTITY_CAPABILITY_ID,
            "generated_app_identity",
            "Provide application-scoped registration, login, session verification, logout and invitation acceptance for generated software without reusing Operly account sessions.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            {"type": "object"},
            version="1.0.0",
            risk_level="high",
            permissions=("solution:generate",),
            approval_policy=ApprovalPolicy.POLICY,
            execution_mode=ExecutionMode.ISOLATED_RUNNER,
            source="operly_builtin",
            provider="operly",
            category="identity",
            display_name="App Users & Sessions",
            plugin_id="operly.identity",
            tags=frozenset({"identity", "authentication", "users", "sessions", "generated-software"}),
            semantic_operations=frozenset(
                {
                    "register application users",
                    "authenticate application users",
                    "verify application sessions",
                    "accept application invitations",
                }
            ),
        ),
    )

    async def execute(self, context, capability_name, arguments):
        if capability_name != APP_IDENTITY_CAPABILITY_ID:
            return CapabilityResult(False, False, {"reason": "unsupported_capability"})
        return CapabilityResult(
            True,
            False,
            {
                "bindingOnly": True,
                "capabilityId": APP_IDENTITY_CAPABILITY_ID,
                "message": "Attach this capability to generated software; end-user credentials remain isolated from Operly account authentication.",
            },
        )


__all__ = ["AppIdentityProvider"]
