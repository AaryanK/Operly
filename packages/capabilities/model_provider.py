"""Model-as-tool capability provider.

The calling agent requests reasoning capability and may pass context references.
OPERLY resolves those references server-side and injects their contents directly into
the target model; the calling model never has to materialize or re-emit that context.
Concrete provider/model identities remain outside the agent contract.
"""
from __future__ import annotations

from packages.capabilities.contracts import ApprovalPolicy, CapabilityDefinition, CapabilityResult
from packages.capabilities.providers import BaseProvider
from packages.context.broker import ContextBroker
from packages.model_runtime.service import ModelInvocationService
from packages.security.surfaces import SurfaceKind


_CONTEXT_REFS_SCHEMA = {
    "type": "array",
    "items": {"type": "string", "minLength": 1, "maxLength": 200},
    "maxItems": 12,
    "description": (
        "Optional authorized context references. OPERLY resolves these directly for "
        "the target model; do not call context.get merely to copy them into this call."
    ),
}


class ModelInvocationProvider(BaseProvider):
    name = "operly_model_runtime"
    capabilities = (
        CapabilityDefinition(
            "model.invoke",
            "model_invoke",
            "Delegate one bounded subtask to another registered model selected by capability and traits. Prefer solving routine work locally. Pass context_refs when the target model needs context that the current model does not need to read.",
            {
                "type": "object",
                "properties": {
                    "capability": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 80,
                        "description": "Specialist capability such as reasoning, coding, vision, translation, summarization, transcription, or speech.",
                    },
                    "objective": {"type": "string", "minLength": 1, "maxLength": 8000},
                    "context": {
                        "type": "string",
                        "maxLength": 6000,
                        "description": "Small caller-authored context only. Prefer context_refs for stored Operly context.",
                    },
                    "context_refs": _CONTEXT_REFS_SCHEMA,
                    "prefer_tags": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 40},
                        "maxItems": 8,
                    },
                    "avoid_tags": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 40},
                        "maxItems": 8,
                    },
                    "prefer_free": {"type": "boolean"},
                    "latency_class": {
                        "type": "string",
                        "enum": ["interactive", "normal", "deep"],
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
                    "pass context to another model by reference",
                }
            ),
        ),
        CapabilityDefinition(
            "model.deep_reason",
            "model_deep_reason",
            "Ask a stronger reasoning model for one difficult remaining subproblem. Use after ordinary context/tool retrieval when the reasoning itself is the bottleneck. Context references are passed directly by the harness without loading them into the current model.",
            {
                "type": "object",
                "properties": {
                    "problem": {"type": "string", "minLength": 1, "maxLength": 8000},
                    "reason": {
                        "type": "string",
                        "enum": [
                            "complex_planning",
                            "stuck",
                            "conflicting_evidence",
                            "high_risk_review",
                            "specialist_reasoning",
                        ],
                    },
                    "attempted": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 1000},
                        "maxItems": 6,
                    },
                    "context_refs": _CONTEXT_REFS_SCHEMA,
                    "desired_output": {"type": "string", "maxLength": 2000},
                },
                "required": ["problem", "reason"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="read_only",
            permissions=("model:invoke",),
            approval_policy=ApprovalPolicy.AUTO,
            plugin_id="builtin:operly_model_runtime",
            category="model",
            tags=frozenset({"model", "delegation", "kernel", "reasoning", "escalation"}),
            semantic_operations=frozenset(
                {
                    "ask stronger model",
                    "deep reasoning",
                    "second opinion",
                    "resolve difficult problem",
                }
            ),
        ),
    )

    @staticmethod
    async def _resolve_context_refs(context, refs) -> tuple[str, list[str], int]:
        clean_refs = [str(item).strip() for item in refs or () if str(item).strip()][:12]
        if not clean_refs:
            return "", [], 0
        invocation = context.invocation or {}
        metadata = invocation.get("metadata") or {}
        rows = await ContextBroker.materialize(
            context.db,
            refs=clean_refs,
            tenant_id=context.tenant_id,
            user_id=context.actor_id,
            conversation_id=str(metadata.get("_conversation_id") or "") or None,
            authority=set(invocation.get("authority") or []),
            surface=SurfaceKind.coerce(
                metadata.get("_surface_kind") or invocation.get("surface")
            ),
        )
        if len(rows) != len(set(clean_refs)):
            # Do not silently turn a partially authorized delegation into a different
            # reasoning request. A missing ref may mean revoked/private authority.
            raise PermissionError("One or more context references are unavailable")
        blocks = []
        used = []
        estimated_tokens = 0
        for row in rows:
            ref = str(row.get("ref") or "")
            used.append(ref)
            estimated_tokens += int(row.get("estimated_tokens") or 0)
            blocks.append(
                f"[ContextRef {ref} | {row.get('scope')} | {row.get('kind')}]\n"
                f"{row.get('content') or ''}"
            )
        return "\n\n".join(blocks), used, estimated_tokens

    async def execute(self, context, capability_name, arguments):
        try:
            ref_context, used_refs, ref_tokens = await self._resolve_context_refs(
                context,
                arguments.get("context_refs") or (),
            )
            if capability_name == "model.deep_reason":
                attempted = [
                    str(item).strip()
                    for item in arguments.get("attempted") or ()
                    if str(item).strip()
                ]
                objective = str(arguments.get("problem") or "")
                desired = str(arguments.get("desired_output") or "").strip()
                caller_context = (
                    "Escalation reason: "
                    + str(arguments.get("reason") or "")
                    + ("\nAttempts already made:\n- " + "\n- ".join(attempted) if attempted else "")
                    + (f"\nDesired output: {desired}" if desired else "")
                )
                result = await ModelInvocationService().invoke(
                    capability="reasoning",
                    objective=objective,
                    context="\n\n".join(part for part in (caller_context, ref_context) if part),
                    prefer_free=False,
                    prefer_tags=("heavy", "reasoning", "reliable"),
                    avoid_tags=("small",),
                    exclude_orchestrator=False,
                    latency_class="deep",
                )
            else:
                caller_context = str(arguments.get("context") or "")
                result = await ModelInvocationService().invoke(
                    capability=str(arguments.get("capability") or ""),
                    objective=str(arguments.get("objective") or ""),
                    context="\n\n".join(part for part in (caller_context, ref_context) if part),
                    prefer_free=bool(arguments.get("prefer_free", True)),
                    prefer_tags=arguments.get("prefer_tags") or (),
                    avoid_tags=arguments.get("avoid_tags") or (),
                    exclude_orchestrator=True,
                    latency_class=str(arguments.get("latency_class") or "normal"),
                )
        except (ValueError, LookupError, RuntimeError, PermissionError) as error:
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
                "context_refs_used": used_refs,
                "context_ref_estimated_tokens": ref_tokens,
                "latency_ms": result.latency_ms,
                "usage": result.usage,
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
                "resource_id": result.evidence.get("resource_id"),
                "selected_tags": result.evidence.get("selected_tags") or [],
                "content": result.evidence.get("content"),
                "context_refs_used": result.evidence.get("context_refs_used") or [],
                "context_ref_estimated_tokens": result.evidence.get("context_ref_estimated_tokens") or 0,
                "latency_ms": result.evidence.get("latency_ms"),
                "usage": result.evidence.get("usage"),
            },
        )
