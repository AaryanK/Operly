"""Model-as-tool capability provider.

The orchestrator requests a capability; OPERLY chooses the concrete model and
provider. This keeps other models out of the harness contract and prevents the
primary model from coupling itself to today's catalog.
"""
from __future__ import annotations

from packages.capabilities.contracts import ApprovalPolicy, CapabilityDefinition, CapabilityResult
from packages.capabilities.providers import BaseProvider
from packages.model_runtime.service import ModelInvocationService


class ModelInvocationProvider(BaseProvider):
    name = "operly_model_runtime"
    capabilities = (
        CapabilityDefinition(
            "model.invoke",
            "model_invoke",
            "Delegate one bounded reasoning/coding/analysis subtask to another registered model selected by capability. Do not use this when the current model can complete the task directly.",
            {
                "type": "object",
                "properties": {
                    "capability": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 80,
                        "description": "Required specialist capability such as reasoning, coding, vision, translation, or summarization.",
                    },
                    "objective": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 8000,
                    },
                    "context": {
                        "type": "string",
                        "maxLength": 12000,
                    },
                },
                "required": ["capability", "objective"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="read_only",
            permissions=("model:invoke",),
            approval_policy=ApprovalPolicy.AUTO,
        ),
    )

    async def execute(self, context, capability_name, arguments):
        try:
            result = await ModelInvocationService().invoke(
                capability=str(arguments.get("capability") or ""),
                objective=str(arguments.get("objective") or ""),
                context=str(arguments.get("context") or ""),
                prefer_free=True,
                exclude_orchestrator=True,
            )
        except (ValueError, LookupError, RuntimeError) as error:
            return CapabilityResult(False, False, {"reason": str(error)[:1000]})
        return CapabilityResult(
            True,
            False,
            {
                "provider": result.provider,
                "model": result.model,
                "capability": result.capability,
                "content": result.content,
                "delegated": True,
                "tools_exposed": False,
            },
        )

    async def verify(self, context, capability_name, arguments, result):
        return CapabilityResult(
            result.success and bool(result.evidence.get("content")),
            False,
            {
                "delegated": bool(result.evidence.get("delegated")),
                "provider": result.evidence.get("provider"),
                "model": result.evidence.get("model"),
            },
        )
