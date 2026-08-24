from __future__ import annotations

from packages.capabilities.contracts import ApprovalPolicy, CapabilityDefinition, CapabilityResult, ExecutionMode
from packages.capabilities.providers import BaseProvider
from packages.workspace_entities.contracts import WORKSPACE_ENTITY_CAPABILITY_ID


class WorkspaceEntityProvider(BaseProvider):
    """Discoverable binding-only capability for workspace-wide canonical entities."""

    name = "operly_workspace_entities"
    capabilities = (
        CapabilityDefinition(
            WORKSPACE_ENTITY_CAPABILITY_ID,
            "workspace_entities",
            "Read or update canonical workspace business entities such as employees, customers and locations through a generated-software binding. Entity identity is shared across Solutions in the workspace.",
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
            display_name="Workspace Entity Graph",
            plugin_id="operly.data",
            tags=frozenset({"data", "entities", "employee", "customer", "location", "generated-software"}),
            semantic_operations=frozenset({
                "reuse workspace employee identities",
                "reuse workspace customer identities",
                "reuse workspace location identities",
                "share canonical business entities across Solutions",
            }),
        ),
    )

    async def execute(self, context, capability_name, arguments):
        if capability_name != WORKSPACE_ENTITY_CAPABILITY_ID:
            return CapabilityResult(False, False, {"reason": "unsupported_capability"})
        return CapabilityResult(True, False, {
            "bindingOnly": True,
            "capabilityId": WORKSPACE_ENTITY_CAPABILITY_ID,
            "message": "Generated software must declare canonical entity kinds in operly.entities.json; runtime access is scoped to the workspace, application, entity kinds and read/write modes.",
        })


__all__ = ["WorkspaceEntityProvider"]
