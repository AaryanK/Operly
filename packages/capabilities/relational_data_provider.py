from __future__ import annotations

from packages.capabilities.contracts import (
    ApprovalPolicy,
    CapabilityDefinition,
    CapabilityResult,
    ExecutionMode,
)
from packages.capabilities.providers import BaseProvider
from packages.relational_data.contracts import RELATIONAL_CAPABILITY_ID


class RelationalDataProvider(BaseProvider):
    """Discoverable binding-only capability for generated application data.

    The model can discover this capability, but data operations are executed by the
    scoped runtime gateway rather than through the conversational control-plane tool
    loop. That keeps generated app traffic out of agent permissions and sessions.
    """

    name = "operly_relational_data"
    capabilities = (
        CapabilityDefinition(
            RELATIONAL_CAPABILITY_ID,
            "relational_data",
            "Provide workspace/application-scoped relational persistence to generated software through an Operly capability binding. No database credentials are exposed to the generated app.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            {"type": "object"},
            version="1.0.0",
            risk_level="medium",
            permissions=("solution:generate",),
            approval_policy=ApprovalPolicy.POLICY,
            execution_mode=ExecutionMode.ISOLATED_RUNNER,
            source="operly_builtin",
            provider="operly",
            category="data",
            display_name="Relational Data",
            plugin_id="operly.data",
            tags=frozenset({"data", "database", "relational", "persistence", "generated-software"}),
            semantic_operations=frozenset(
                {
                    "persist application records",
                    "query relational application data",
                    "migrate application schema",
                }
            ),
        ),
    )

    async def execute(self, context, capability_name, arguments):
        if capability_name != RELATIONAL_CAPABILITY_ID:
            return CapabilityResult(False, False, {"reason": "unsupported_capability"})
        return CapabilityResult(
            True,
            False,
            {
                "bindingOnly": True,
                "capabilityId": RELATIONAL_CAPABILITY_ID,
                "message": "Attach this capability to generated software; runtime data access is scoped by workspace and application.",
            },
        )


__all__ = ["RelationalDataProvider"]
