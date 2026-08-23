"""Task-level routing that runs before business-agent model selection.

The primary router is itself a runtime plugin backed by a small/fast reasoning
model. Deterministic heuristics remain only as a bounded fallback when the routing
model/provider is unavailable or cannot produce a valid decision.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

from packages.harness.plugins import (
    RuntimePluginContext,
    RuntimePluginUnavailable,
    default_runtime_plugins,
)
from packages.model_runtime.contracts import (
    InferenceBudget,
    InferenceRequest,
    InferenceResult,
    ModelInferenceError,
    ModelTraits,
)
from packages.model_runtime.registry import (
    ModelChatAdapter,
    model_for_role as _base_model_for_role,
)
from packages.model_runtime.routing_policy import role_routing_profile
from packages.model_runtime.semantic_router import SemanticRouter, SemanticRoutingError


_TOKEN_RE = re.compile(r"[a-z0-9_-]+")


@dataclass(frozen=True, slots=True)
class TaskRouteDecision:
    task_type: str
    role: str
    tool_policy: str
    confidence: float
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "taskType": self.task_type,
            "role": self.role,
            "toolPolicy": self.tool_policy,
            "confidence": self.confidence,
            "reason": self.reason,
        }


_ROUTE_SPECS: dict[str, tuple[str, str, str]] = {
    "business_agent": (
        "business_reasoning",
        "progressive_capability_access",
        "General business reasoning, conversation, or mixed work where the agent should discover and use authorized capabilities as needed.",
    ),
    "coding": (
        "coding_or_studio",
        "workspace_write_with_validation",
        "Coding, debugging, implementation, Studio/source generation, or software repair.",
    ),
    "global_validator": (
        "validation",
        "read_first_validation",
        "Verification, review, auditing, testing, or independent validation of work/evidence.",
    ),
    "requirements_analyst": (
        "research",
        "read_research_sources",
        "Research, investigation, evidence gathering, comparison, requirements discovery, or market/competitor analysis.",
    ),
    "planner": (
        "planning",
        "read_then_propose",
        "Planning, architecture, strategy, roadmap, design reasoning, or proposal development.",
    ),
    "bounded_task": (
        "bounded_operation",
        "bounded_action_with_approval",
        "A focused operational task that may need tools or side effects such as email, calendar, reminders, updates, approvals, or other governed actions.",
    ),
}

# These intents indicate that first-hop specialist routing is useful. Everything
# else may remain on the primary assistant and answer/use an already-exposed tool
# directly. This intentionally keeps greetings, explanation, arithmetic, account
# reads, and ordinary follow-ups out of the orchestration ceremony.
_SPECIALIST_HINTS = frozenset(
    {
        "code",
        "implement",
        "debug",
        "fix",
        "studio",
        "website",
        "app",
        "html",
        "javascript",
        "validate",
        "verify",
        "audit",
        "review",
        "test",
        "research",
        "investigate",
        "compare",
        "market",
        "competitor",
        "evidence",
        "plan",
        "strategy",
        "roadmap",
        "design",
        "architect",
        "proposal",
        "send",
        "create",
        "update",
        "delete",
        "schedule",
        "email",
        "remind",
        "approve",
    }
)


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(str(text or "").lower()))


def _primary_assistant_decision(objective: str) -> TaskRouteDecision | None:
    """Return the direct assistant path when no specialist workload is evident."""
    text = " ".join(str(objective or "").lower().split())
    tokens = _tokens(text)
    if not text or not any(hint in tokens for hint in _SPECIALIST_HINTS):
        return TaskRouteDecision(
            "business_reasoning",
            "business_agent",
            "progressive_capability_access",
            0.80,
            "direct primary-assistant path; no specialist workload required",
        )
    return None


def classify_business_task(objective: str) -> TaskRouteDecision:
    """Deterministic fallback classifier retained for degraded operation only."""
    text = str(objective or "").lower()
    tokens = _tokens(text)

    def has(*words: str) -> bool:
        return any(word in tokens or word in text for word in words)

    if has("code", "implement", "debug", "fix", "studio", "website", "app", "html", "javascript"):
        return TaskRouteDecision(
            "coding_or_studio",
            "coding",
            "workspace_write_with_validation",
            0.55,
            "fallback heuristic selected coding/studio work",
        )
    if has("validate", "verify", "audit", "review", "check", "test"):
        return TaskRouteDecision(
            "validation",
            "global_validator",
            "read_first_validation",
            0.52,
            "fallback heuristic selected validation work",
        )
    if has("research", "investigate", "find", "compare", "market", "competitor", "evidence"):
        return TaskRouteDecision(
            "research",
            "requirements_analyst",
            "read_research_sources",
            0.50,
            "fallback heuristic selected research work",
        )
    if has("plan", "strategy", "roadmap", "design", "architect", "proposal"):
        return TaskRouteDecision(
            "planning",
            "planner",
            "read_then_propose",
            0.48,
            "fallback heuristic selected planning work",
        )
    if has("send", "create", "update", "delete", "schedule", "email", "remind", "approve"):
        return TaskRouteDecision(
            "bounded_operation",
            "bounded_task",
            "bounded_action_with_approval",
            0.46,
            "fallback heuristic selected a bounded operation",
        )
    return TaskRouteDecision(
        "business_reasoning",
        "business_agent",
        "progressive_capability_access",
        0.35,
        "fallback heuristic selected general business reasoning",
    )


class ModelTaskRouterPlugin:
    """Application-controlled top-level router backed by the model portfolio."""

    id = "model-runtime.task-router"
    kind = "task_router"
    priority = 10

    def supports(self, payload: dict[str, Any], context: RuntimePluginContext) -> bool:
        del context
        return bool(str(payload.get("objective") or "").strip())

    async def invoke(
        self,
        payload: dict[str, Any],
        context: RuntimePluginContext,
    ) -> TaskRouteDecision:
        objective = str(payload.get("objective") or "").strip()
        try:
            router_model = _base_model_for_role("router")
            router_client = ModelChatAdapter(
                router_model,
                budget=InferenceBudget(
                    timeout_seconds=20.0,
                    attempts_per_model=1,
                    max_models=2,
                    max_output_tokens=800,
                ),
            )
            semantic = await SemanticRouter(router_client).decide(
                request=objective,
                domain="selecting the best Operly specialist role for the current user objective",
                routes={role: spec[2] for role, spec in _ROUTE_SPECS.items()},
                context={
                    "channel": context.channel,
                    "surface": context.surface,
                    "hasAttachments": bool(payload.get("has_attachments")),
                    "attachmentCount": int(payload.get("attachment_count") or 0),
                    "availableToolCount": int(payload.get("tool_count") or 0),
                    "hasAvailableCapabilities": bool(payload.get("tool_count")),
                    "note": "Choose a specialist only by workload meaning. Tool availability does not mean the request needs tools, and routing never executes the task.",
                },
            )
        except (ModelInferenceError, SemanticRoutingError, LookupError, RuntimeError) as error:
            raise RuntimePluginUnavailable(str(error)) from error

        if not semantic.domain_match or not semantic.known or not semantic.route_id:
            raise RuntimePluginUnavailable(
                "router model did not choose one bounded specialist role"
            )
        task_type, tool_policy, _ = _ROUTE_SPECS[semantic.route_id]
        return TaskRouteDecision(
            task_type=task_type,
            role=semantic.route_id,
            tool_policy=tool_policy,
            confidence=0.90,
            reason=f"model router: {semantic.reason}",
        )


class DeterministicTaskRouterPlugin:
    """Last-resort router so provider failure does not take down Operly."""

    id = "model-runtime.task-router-fallback"
    kind = "task_router"
    priority = 1000

    def supports(self, payload: dict[str, Any], context: RuntimePluginContext) -> bool:
        del context
        return bool(str(payload.get("objective") or "").strip())

    async def invoke(
        self,
        payload: dict[str, Any],
        context: RuntimePluginContext,
    ) -> TaskRouteDecision:
        del context
        return classify_business_task(str(payload.get("objective") or ""))


def _ensure_router_plugins() -> None:
    registry = default_runtime_plugins()
    installed = {plugin.id for plugin in registry.installed("task_router")}
    if ModelTaskRouterPlugin.id not in installed:
        registry.register(ModelTaskRouterPlugin())
    if DeterministicTaskRouterPlugin.id not in installed:
        registry.register(DeterministicTaskRouterPlugin())


async def route_business_task(
    objective: str,
    *,
    request: InferenceRequest | None = None,
) -> TaskRouteDecision:
    """Route through the installed task-router plugin chain."""
    _ensure_router_plugins()
    metadata = dict(request.metadata) if request is not None else {}
    has_attachment_message = False
    if request is not None:
        has_attachment_message = any(
            str(message.get("role") or "") == "system"
            and "ATTACHMENT ANALYSIS" in str(message.get("content") or "")
            for message in request.messages
        )
    return await default_runtime_plugins().invoke(
        "task_router",
        {
            "objective": objective,
            "tool_count": len(request.tools) if request is not None else 0,
            "has_attachments": bool(metadata.get("has_attachments")) or has_attachment_message,
            "attachment_count": int(metadata.get("attachment_count") or 0),
        },
        RuntimePluginContext(
            channel=str(metadata.get("channel") or ""),
            surface=str(metadata.get("surface") or ""),
            metadata=metadata,
        ),
    )


def _last_user_objective(request: InferenceRequest) -> str:
    for message in reversed(request.messages):
        if str(message.get("role") or "") == "user":
            content = message.get("content")
            if isinstance(content, str):
                return content
            return str(content or "")
    return ""


def _existing_route(request: InferenceRequest) -> TaskRouteDecision | None:
    """Reuse the first-hop route for later tool-loop inference steps."""
    for message in reversed(request.messages):
        raw = message.get("_operly_task_route")
        if not isinstance(raw, dict):
            continue
        role = str(raw.get("role") or "")
        if role not in _ROUTE_SPECS:
            continue
        task_type, tool_policy, _ = _ROUTE_SPECS[role]
        return TaskRouteDecision(
            task_type=str(raw.get("taskType") or task_type),
            role=role,
            tool_policy=str(raw.get("toolPolicy") or tool_policy),
            confidence=float(raw.get("confidence") or 0.9),
            reason=str(raw.get("reason") or "reused top-level route"),
        )
    return None


def _task_budget(
    decision: TaskRouteDecision,
    current: InferenceBudget | None,
) -> InferenceBudget:
    current = current or InferenceBudget()
    defaults = {
        "bounded_operation": (45.0, 2, 4000),
        "research": (120.0, 3, 9000),
        "coding_or_studio": (150.0, 3, 12000),
        "validation": (90.0, 3, 7000),
        "planning": (90.0, 3, 8000),
        "business_reasoning": (75.0, 3, 7000),
    }
    timeout, models, output = defaults.get(
        decision.task_type,
        defaults["business_reasoning"],
    )
    return InferenceBudget(
        timeout_seconds=current.timeout_seconds or timeout,
        attempts_per_model=max(1, current.attempts_per_model),
        max_models=current.max_models or models,
        max_output_tokens=current.max_output_tokens or output,
    )


class TaskRoutedBusinessModel:
    """Lazy model proxy preserving a normal assistant-first tool loop."""

    id = "task-router:business-agent"
    tags = frozenset({"task-routed", "plugin-routed"})
    capabilities = frozenset({"text", "tools", "reasoning", "coding"})
    traits = ModelTraits()

    def __init__(self) -> None:
        self.last_decision: TaskRouteDecision | None = None

    async def infer(self, request: InferenceRequest) -> InferenceResult:
        decision = _existing_route(request)
        if decision is None:
            objective = _last_user_objective(request)
            decision = _primary_assistant_decision(objective)
            if decision is None:
                decision = await route_business_task(objective, request=request)
        self.last_decision = decision

        # Top-level AgentRuntime owns the capability loop. If the router selected a
        # pure reasoning specialist while tools are exposed, keep execution on the
        # tool-capable primary assistant; those reasoning roles remain available via
        # bounded model.invoke delegation instead of receiving schemas they were not
        # selected to support.
        execution_role = decision.role
        requested_profile = role_routing_profile(execution_role)
        if request.tools and "tools" not in requested_profile.requires:
            execution_role = "business_agent"
        execution_profile = role_routing_profile(execution_role)

        selected = _base_model_for_role(execution_role)
        metadata = dict(request.metadata)
        route_metadata = decision.as_dict()
        route_metadata["executionRole"] = execution_role
        route_metadata["toolSchemasForwarded"] = bool(request.tools and "tools" in execution_profile.requires)
        metadata["task_route"] = route_metadata
        routed = InferenceRequest(
            messages=request.messages,
            tools=request.tools if "tools" in execution_profile.requires else (),
            response_schema=request.response_schema,
            modality_inputs=request.modality_inputs,
            budget=_task_budget(decision, request.budget),
            metadata=metadata,
        )
        result = await selected.infer(routed)
        message = dict(result.message)
        # AgentRuntime preserves this field in the in-memory loop so subsequent
        # inference steps reuse the first-hop route instead of paying for routing
        # again. Persisted user/assistant history intentionally omits it.
        message["_operly_task_route"] = decision.as_dict()
        return replace(result, message=message)


def model_for_role(role: str):
    """Public role resolver with plugin routing for the primary business agent."""
    if str(role).strip().lower() == "business_agent":
        return TaskRoutedBusinessModel()
    return _base_model_for_role(role)
