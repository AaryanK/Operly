"""Model-as-tool capability provider.

The orchestrator requests a capability and optional traits; OPERLY chooses the
concrete model/provider. Provider/model identities never enter the agent contract.
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
            "Delegate one bounded subtask to another registered model selected by capability and traits. Use preference tags such as heavy for difficult reasoning, coding for implementation analysis, long-context for very large inputs, fast for latency-sensitive work, local for privacy/locality, or free for low-cost work. Do not delegate routine work the current model can complete directly.",
            {
                "type": "object",
                "properties": {
                    "capability": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 80,
                        "description": "Required specialist capability such as reasoning, coding, vision, translation, summarization, transcription, or speech.",
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
                    "prefer_tags": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 40},
                        "maxItems": 8,
                        "description": "Optional provider-neutral preferences such as heavy, fast, coding, long-context, local, private, reliable, or free.",
                    },
                    "avoid_tags": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 40},
                        "maxItems": 8,
                        "description": "Optional provider-neutral traits to avoid.",
                    },
                    "prefer_free": {
                        "type": "boolean",
                        "description": "Prefer free eligible models when true. Defaults to true unless heavy/quality preferences intentionally point elsewhere.",
                    },
                },
                "required": ["capability", "objective"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="read_only",
            permissions=("model:invoke",),
            approval_policy=ApprovalPolicy.AUTO,
            plugin_id="builtin:operly_model_runtime",
            category="model",
            tags=frozenset({"model", "delegation", "kernel"}),
            semantic_operations=frozenset(
                {
                    "delegate hard reasoning",
                    "delegate coding analysis",
                    "use specialist model",
                    "use heavier model",
                }
            ),
        ),
    )

    async def execute(self, context, capability_name, arguments):
        try:
            result = await ModelInvocationService().invoke(
                capability=str(arguments.get("capability") or ""),
                objective=str(arguments.get("objective") or ""),
                context=str(arguments.get("context") or ""),
                prefer_free=bool(arguments.get("prefer_free", True)),
                prefer_tags=arguments.get("prefer_tags") or (),
                avoid_tags=arguments.get("avoid_tags") or (),
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
                "resource_id": result.resource_id,
                "capability": result.capability,
                "selected_tags": list(result.selected_tags),
                "content": result.content,
                "delegated": True,
                "tools_exposed": False,
            },
        )

    async def verify(self, context, capability_name, arguments, result):
        # The delegated content is the observation downstream agent/workflow steps
        # need to compose. Verification must preserve it rather than reducing a
        # successful model call to provider metadata only.
        return CapabilityResult(
            result.success and bool(result.evidence.get("content")),
            False,
            {
                "delegated": bool(result.evidence.get("delegated")),
                "provider": result.evidence.get("provider"),
                "model": result.evidence.get("model"),
                "resource_id": result.evidence.get("resource_id"),
                "selected_tags": result.evidence.get("selected_tags") or [],
                "content": result.evidence.get("content"),
            },
        )
