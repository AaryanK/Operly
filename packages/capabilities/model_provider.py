"""Semantic AI capability provider over Operly's existing model runtime.

Agents, Workflows, Tasks, Studio, MCP, and Solutions request an ``ai.*`` ability
through the normal Capability Registry and firewall.  They never select a concrete
provider/model.  The existing model runtime resolves a bounded specialist route and
returns its result to the caller, which remains responsible for its original
objective.

``model.invoke`` and ``model.deep_reason`` remain as compatibility capabilities while
callers migrate to the semantic ``ai.*`` family.
"""
from __future__ import annotations

from dataclasses import dataclass

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

_COMMON_AI_PROPERTIES = {
    "objective": {
        "type": "string",
        "minLength": 1,
        "maxLength": 8000,
        "description": "The bounded specialist subtask. The parent run still owns the original objective.",
    },
    "context": {
        "type": "string",
        "maxLength": 12000,
        "description": "Small task-local context. Prefer context_refs for stored Operly context.",
    },
    "context_refs": _CONTEXT_REFS_SCHEMA,
    "prefer_tags": {
        "type": "array",
        "items": {"type": "string", "maxLength": 40},
        "maxItems": 8,
        "description": "Optional provider-neutral scheduling traits such as reliable or fast.",
    },
    "avoid_tags": {
        "type": "array",
        "items": {"type": "string", "maxLength": 40},
        "maxItems": 8,
        "description": "Optional provider-neutral scheduling traits to avoid.",
    },
    "prefer_free": {"type": "boolean"},
    "latency_class": {
        "type": "string",
        "enum": ["interactive", "normal", "deep"],
    },
}


def _ai_schema() -> dict:
    return {
        "type": "object",
        "properties": dict(_COMMON_AI_PROPERTIES),
        "required": ["objective"],
        "additionalProperties": False,
    }


@dataclass(frozen=True, slots=True)
class _AIProfile:
    model_capability: str
    prefer_tags: tuple[str, ...]
    avoid_tags: tuple[str, ...] = ()
    prefer_free: bool = True
    latency_class: str = "normal"
    instruction: str = ""


_AI_PROFILES: dict[str, _AIProfile] = {
    "ai.generate": _AIProfile(
        "text",
        ("reliable", "fast"),
        instruction="Generate the requested bounded result directly.",
    ),
    "ai.reason": _AIProfile(
        "reasoning",
        ("reasoning", "reliable"),
        instruction="Solve the bounded reasoning subproblem and return the useful conclusion and evidence.",
    ),
    "ai.plan": _AIProfile(
        "reasoning",
        ("reasoning", "reliable", "heavy"),
        instruction="Produce a bounded actionable plan for this subproblem; do not assume ownership of the parent objective.",
    ),
    "ai.code.generate": _AIProfile(
        "coding",
        ("coding", "reliable", "fast"),
        instruction="Generate the smallest correct implementation needed for this bounded coding subtask.",
    ),
    "ai.code.repair": _AIProfile(
        "coding",
        ("coding", "reasoning", "reliable"),
        instruction="Repair the smallest amount necessary from the supplied failure evidence and preserve unrelated behavior.",
    ),
    "ai.code.review": _AIProfile(
        "coding",
        ("coding", "reasoning", "reliable"),
        instruction="Review the supplied implementation against the stated contract and report concrete defects or acceptance evidence.",
    ),
    "ai.extract.requirements": _AIProfile(
        "reasoning",
        ("reasoning", "reliable"),
        instruction="Extract explicit requirements, constraints, actors, and acceptance conditions without inventing unsupported requirements.",
    ),
}


def _semantic_definition(
    capability_id: str,
    description: str,
    operations: set[str],
    *,
    tags: set[str],
) -> CapabilityDefinition:
    return CapabilityDefinition(
        capability_id,
        capability_id.replace(".", "_"),
        description,
        _ai_schema(),
        {"type": "object"},
        risk_level="read_only",
        permissions=("model:invoke",),
        approval_policy=ApprovalPolicy.AUTO,
        plugin_id="builtin:operly_model_runtime",
        category="ai",
        tags=frozenset({"ai", "model", "delegation", "kernel", *tags}),
        semantic_operations=frozenset(operations),
    )


class ModelInvocationProvider(BaseProvider):
    name = "operly_model_runtime"
    capabilities = (
        _semantic_definition(
            "ai.generate",
            "Delegate one bounded generation subtask to a provider-neutral specialist model. The result returns to the current run; it does not complete the caller's objective.",
            {"generate text", "draft content", "summarize", "specialist generation"},
            tags={"generation"},
        ),
        _semantic_definition(
            "ai.reason",
            "Delegate one bounded reasoning subproblem to a provider-neutral specialist. Use when reasoning, rather than missing data or capabilities, is the bottleneck. The parent run must continue afterward.",
            {"deep reasoning", "analyze difficult subproblem", "second opinion", "resolve conflicting evidence"},
            tags={"reasoning", "escalation"},
        ),
        _semantic_definition(
            "ai.plan",
            "Delegate one bounded planning subproblem to a planning-capable specialist while the current Agent, Workflow, Task, or Studio run keeps ownership of completion.",
            {"plan software", "plan workflow", "decompose operation", "create bounded plan"},
            tags={"planning", "reasoning"},
        ),
        _semantic_definition(
            "ai.code.generate",
            "Delegate a bounded code-generation subtask to a coding specialist selected by Operly. The caller remains responsible for build/test/acceptance.",
            {"generate code", "implement source", "write function", "coding specialist"},
            tags={"coding", "generation"},
        ),
        _semantic_definition(
            "ai.code.repair",
            "Delegate a targeted source repair to a coding specialist selected by Operly. Return the repair to the parent run, which must re-run deterministic verification.",
            {"repair code", "fix failing build", "debug source", "patch implementation"},
            tags={"coding", "repair"},
        ),
        _semantic_definition(
            "ai.code.review",
            "Delegate a bounded code review to a coding/reasoning specialist selected by Operly. The result is review evidence, not completion of the parent objective.",
            {"review code", "inspect implementation", "find code defect", "validate source"},
            tags={"coding", "review"},
        ),
        _semantic_definition(
            "ai.extract.requirements",
            "Delegate bounded requirement extraction to a specialist selected by Operly. Use the structured findings as input to the existing planner/workflow rather than transferring run ownership.",
            {"extract requirements", "identify constraints", "derive acceptance conditions", "requirements analysis"},
            tags={"requirements", "reasoning"},
        ),
        CapabilityDefinition(
            "model.invoke",
            "model_invoke",
            "Compatibility capability: delegate one bounded subtask to another registered model selected by capability and traits. Prefer semantic ai.* capabilities for new work.",
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
            tags=frozenset({"model", "delegation", "kernel", "legacy"}),
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
            "Compatibility capability for one difficult reasoning subproblem. Prefer ai.reason for new work.",
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
            tags=frozenset({"model", "delegation", "kernel", "reasoning", "escalation", "legacy"}),
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

    def __init__(self, model_service: ModelInvocationService | None = None) -> None:
        self.model_service = model_service or ModelInvocationService()

    @staticmethod
    def _invocation_metadata(context) -> tuple[dict, int]:
        invocation = context.invocation or {}
        metadata = invocation.get("metadata") if isinstance(invocation.get("metadata"), dict) else {}
        raw_depth = invocation.get("ai_delegation_depth", metadata.get("ai_delegation_depth", 0))
        try:
            depth = max(0, int(raw_depth or 0))
        except (TypeError, ValueError):
            depth = 0
        return metadata, depth

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
        metadata, parent_depth = self._invocation_metadata(context)
        if parent_depth >= 1:
            return CapabilityResult(
                False,
                False,
                {
                    "reason": "ai_delegation_depth_exceeded",
                    "delegation_depth": parent_depth,
                    "max_delegation_depth": 1,
                },
            )

        try:
            ref_context, used_refs, ref_tokens = await self._resolve_context_refs(
                context,
                arguments.get("context_refs") or (),
            )
            ai_profile = _AI_PROFILES.get(capability_name)
            ai_capability = capability_name if ai_profile is not None else None

            if ai_profile is not None:
                caller_context = str(arguments.get("context") or "")
                scheduling_context = "\n\n".join(
                    part
                    for part in (
                        f"Specialist contract: {ai_profile.instruction}" if ai_profile.instruction else "",
                        caller_context,
                        ref_context,
                    )
                    if part
                )
                prefer_tags = tuple(dict.fromkeys(
                    (*ai_profile.prefer_tags, *(arguments.get("prefer_tags") or ()))
                ))
                avoid_tags = tuple(dict.fromkeys(
                    (*ai_profile.avoid_tags, *(arguments.get("avoid_tags") or ()))
                ))
                result = await self.model_service.invoke(
                    capability=ai_profile.model_capability,
                    objective=str(arguments.get("objective") or ""),
                    context=scheduling_context,
                    prefer_free=bool(arguments.get("prefer_free", ai_profile.prefer_free)),
                    prefer_tags=prefer_tags,
                    avoid_tags=avoid_tags,
                    exclude_orchestrator=True,
                    latency_class=str(arguments.get("latency_class") or ai_profile.latency_class),
                )
            elif capability_name == "model.deep_reason":
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
                result = await self.model_service.invoke(
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
                result = await self.model_service.invoke(
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

        parent_run_id = str(metadata.get("runtime_run_id") or "") or None
        delegation = {
            "parent_run_id": parent_run_id,
            "parent_execution_id": getattr(context, "execution_id", None),
            "depth": parent_depth + 1,
            "max_depth": 1,
            "child_tools_exposed": False,
            "terminal_for_parent": False,
            "parent_retains_objective": True,
        }
        return CapabilityResult(
            True,
            False,
            {
                "provider": result.provider,
                "model": result.model,
                "resource_id": result.resource_id,
                "capability": result.capability,
                "ai_capability": ai_capability,
                "selected_tags": list(result.selected_tags),
                "content": result.content,
                "delegated": True,
                "tools_exposed": False,
                "delegation": delegation,
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
                "ai_capability": result.evidence.get("ai_capability"),
                "provider": result.evidence.get("provider"),
                "model": result.evidence.get("model"),
                "resource_id": result.evidence.get("resource_id"),
                "selected_tags": result.evidence.get("selected_tags") or [],
                "content": result.evidence.get("content"),
                "delegation": result.evidence.get("delegation") or {},
                "context_refs_used": result.evidence.get("context_refs_used") or [],
                "context_ref_estimated_tokens": result.evidence.get("context_ref_estimated_tokens") or 0,
                "latency_ms": result.evidence.get("latency_ms"),
                "usage": result.evidence.get("usage"),
            },
        )