"""Task routing plus capability-driven model selection.

Task labels are retained for workload policy, observability, and compatibility,
but no longer determine the execution model. Each inference turn derives concrete
model requirements (tools, coding, reasoning, context) and asks the shared model
portfolio to satisfy those requirements directly.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

from packages.model_runtime.contracts import (
    InferenceBudget,
    InferenceRequest,
    InferenceResult,
    Model,
    ModelInferenceError,
    ModelTraits,
)
from packages.model_runtime.registry import ModelChatAdapter, model_for_role as _base_model_for_role
from packages.model_runtime.requirements import ModelRequirements, model_for_requirements
from packages.model_runtime.semantic_router import SemanticRouter, SemanticRoutingError
from packages.plugins.extensions import (
    ApplicationPluginContext,
    ApplicationPluginUnavailable,
    default_application_plugins,
)

_TOKEN_RE = re.compile(r"[a-z0-9_-]+")
_EXPLICIT_EXECUTION_PHRASES = (
    "do not merely plan",
    "do not just plan",
    "don't just plan",
    "dont just plan",
    "not just plan",
)


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
            "fallback heuristic identified a coding/studio workload shape",
        )
    if has("validate", "verify", "audit", "review", "check", "test"):
        return TaskRouteDecision(
            "validation",
            "global_validator",
            "read_first_validation",
            0.52,
            "fallback heuristic identified a validation workload shape",
        )
    if has("research", "investigate", "find", "compare", "market", "competitor", "evidence"):
        return TaskRouteDecision(
            "research",
            "requirements_analyst",
            "read_research_sources",
            0.50,
            "fallback heuristic identified a research workload shape",
        )
    explicit_execution = any(phrase in text for phrase in _EXPLICIT_EXECUTION_PHRASES)
    if explicit_execution and has(
        "send",
        "create",
        "update",
        "delete",
        "schedule",
        "email",
        "remind",
        "approve",
    ):
        return TaskRouteDecision(
            "bounded_operation",
            "bounded_task",
            "bounded_action_with_approval",
            0.60,
            "fallback heuristic honored explicit execution ownership",
        )
    if has("plan", "strategy", "roadmap", "design", "architect", "proposal"):
        return TaskRouteDecision(
            "planning",
            "planner",
            "read_then_propose",
            0.48,
            "fallback heuristic identified a planning workload shape",
        )
    if has("send", "create", "update", "delete", "schedule", "email", "remind", "approve"):
        return TaskRouteDecision(
            "bounded_operation",
            "bounded_task",
            "bounded_action_with_approval",
            0.46,
            "fallback heuristic identified a bounded operation",
        )
    return TaskRouteDecision(
        "business_reasoning",
        "business_agent",
        "progressive_capability_access",
        0.35,
        "fallback heuristic identified general business reasoning",
    )


def _context_requirement(request: InferenceRequest) -> int | None:
    """Estimate the minimum useful context tier without binding to a provider."""
    chars = sum(len(str(message.get("content") or "")) for message in request.messages)
    chars += sum(len(str(tool)) for tool in request.tools)
    estimated_tokens = max(1, chars // 4)
    if estimated_tokens >= 48_000:
        return 128_000
    if estimated_tokens >= 18_000:
        return 64_000
    if estimated_tokens >= 7_000:
        return 32_000
    return None


def requirements_for_task(
    decision: TaskRouteDecision,
    request: InferenceRequest,
) -> ModelRequirements:
    """Translate a workload shape into primary-worker requirements.

    Normal agent turns are deliberately small-model-first *and non-heavy*. A task
    label may add required capabilities such as coding/reasoning/long-context, but it
    must not silently promote routine execution or automatic provider failover to the
    heavy tier. When stronger reasoning is actually needed, the worker has the
    explicit model.deep_reason capability.
    """
    required = {"text"}
    preferred = {"reliable", "small", "fast"}
    if request.tools:
        required.add("tools")
        preferred.add("tools")

    if decision.task_type == "coding_or_studio":
        required.add("coding")
        preferred.update({"coding", "reasoning"})
    elif decision.task_type in {"validation", "research", "planning"}:
        required.add("reasoning")
        preferred.update({"reasoning", "long-context"})
    elif decision.task_type == "bounded_operation":
        preferred.update({"fast", "reliable"})
    else:
        preferred.update({"reasoning", "fast"})

    minimum = _context_requirement(request)
    if minimum:
        preferred.add("long-context")

    return ModelRequirements(
        requires=frozenset(required),
        prefer_tags=frozenset(preferred),
        avoid_tags=frozenset({"heavy"}),
        prefer_free=True,
        max_models=max(1, int((request.budget.max_models if request.budget else None) or 3)),
        min_context_tokens=minimum,
        reason=f"task={decision.task_type}; tools={bool(request.tools)}; small-first; heavy=explicit-escalation-only",
    )


class ModelTaskRouterPlugin:
    """Application-controlled workload router backed by the dynamic portfolio."""

    id = "model-runtime.task-router"
    kind = "task_router"
    priority = 10

    def supports(self, payload: dict[str, Any], context: ApplicationPluginContext) -> bool:
        del context
        return bool(str(payload.get("objective") or "").strip())

    async def invoke(
        self,
        payload: dict[str, Any],
        context: ApplicationPluginContext,
    ) -> TaskRouteDecision:
        objective = str(payload.get("objective") or "").strip()
        try:
            router_requirements = ModelRequirements(
                requires=frozenset({"text", "reasoning"}),
                prefer_tags=frozenset({"fast", "small", "reliable"}),
                avoid_tags=frozenset({"heavy"}),
                prefer_free=True,
                max_models=2,
                reason="low-latency task-shape routing; heavy tier excluded",
            )
            router_model = model_for_requirements(router_requirements, fallback_role="router")
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
                domain="identifying the current Operly workload shape for policy and budgeting",
                routes={role: spec[2] for role, spec in _ROUTE_SPECS.items()},
                context={
                    "channel": context.channel,
                    "surface": context.surface,
                    "hasAttachments": bool(payload.get("has_attachments")),
                    "attachmentCount": int(payload.get("attachment_count") or 0),
                    "availableToolCount": int(payload.get("tool_count") or 0),
                    "hasAvailableCapabilities": bool(payload.get("tool_count")),
                    "note": "Choose workload meaning only. This label controls policy/telemetry, not a fixed execution model; model selection happens from concrete requirements afterward.",
                },
            )
        except (ModelInferenceError, SemanticRoutingError, LookupError, RuntimeError) as error:
            raise ApplicationPluginUnavailable(str(error)) from error

        if not semantic.domain_match or not semantic.known or not semantic.route_id:
            raise ApplicationPluginUnavailable("router model did not choose one bounded workload shape")
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

    def supports(self, payload: dict[str, Any], context: ApplicationPluginContext) -> bool:
        del context
        return bool(str(payload.get("objective") or "").strip())

    async def invoke(
        self,
        payload: dict[str, Any],
        context: ApplicationPluginContext,
    ) -> TaskRouteDecision:
        del context
        return classify_business_task(str(payload.get("objective") or ""))


def _ensure_router_plugins() -> None:
    registry = default_application_plugins()
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
    """Route through the installed task-router extension chain."""
    _ensure_router_plugins()
    metadata = dict(request.metadata) if request is not None else {}
    has_attachment_message = False
    if request is not None:
        has_attachment_message = any(
            str(message.get("role") or "") == "system"
            and "ATTACHMENT ANALYSIS" in str(message.get("content") or "")
            for message in request.messages
        )
    return await default_application_plugins().invoke(
        "task_router",
        {
            "objective": objective,
            "tool_count": len(request.tools) if request is not None else 0,
            "has_attachments": bool(metadata.get("has_attachments")) or has_attachment_message,
            "attachment_count": int(metadata.get("attachment_count") or 0),
        },
        ApplicationPluginContext(
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
    timeout, models, output = defaults.get(decision.task_type, defaults["business_reasoning"])
    return InferenceBudget(
        timeout_seconds=current.timeout_seconds or timeout,
        attempts_per_model=max(1, current.attempts_per_model),
        max_models=current.max_models or models,
        max_output_tokens=current.max_output_tokens or output,
    )


class TaskRoutedBusinessModel:
    """Lazy model proxy with workload routing and requirements-first execution."""

    id = "task-router:business-agent"
    tags = frozenset({"task-routed", "requirements-routed", "small-first"})
    capabilities = frozenset({"text", "tools", "reasoning", "coding"})
    traits = ModelTraits()

    def __init__(self) -> None:
        self.last_decision: TaskRouteDecision | None = None
        self.last_requirements: ModelRequirements | None = None
        self._selected_requirements: ModelRequirements | None = None
        self._selected_fallback_role: str | None = None
        self._selected_model: Model | None = None

    def _select_model(
        self,
        requirements: ModelRequirements,
        *,
        fallback_role: str,
    ) -> Model:
        """Reuse one compatible model session so pool cooldown/preference state survives turns."""
        if (
            self._selected_model is not None
            and self._selected_requirements == requirements
            and self._selected_fallback_role == fallback_role
        ):
            return self._selected_model

        selected = model_for_requirements(requirements, fallback_role=fallback_role)
        self._selected_requirements = requirements
        self._selected_fallback_role = fallback_role
        self._selected_model = selected
        return selected

    async def infer(self, request: InferenceRequest) -> InferenceResult:
        decision = _existing_route(request)
        if decision is None:
            objective = _last_user_objective(request)
            decision = _primary_assistant_decision(objective)
            if decision is None:
                decision = await route_business_task(objective, request=request)
        self.last_decision = decision

        requirements = requirements_for_task(decision, request)
        self.last_requirements = requirements
        fallback_role = "business_agent" if request.tools else decision.role
        selected = self._select_model(requirements, fallback_role=fallback_role)

        metadata = dict(request.metadata)
        route_metadata = decision.as_dict()
        route_metadata["compatibilityRole"] = decision.role
        route_metadata["modelRequirements"] = requirements.as_dict()
        route_metadata["toolSchemasForwarded"] = bool(request.tools and "tools" in requirements.requires)
        route_metadata["smallModelFirst"] = "small" in requirements.prefer_tags
        route_metadata["automaticHeavyFallback"] = False
        metadata["task_route"] = route_metadata
        routed = InferenceRequest(
            messages=request.messages,
            tools=request.tools if "tools" in requirements.requires else (),
            response_schema=request.response_schema,
            modality_inputs=request.modality_inputs,
            budget=_task_budget(decision, request.budget),
            metadata=metadata,
        )
        result = await selected.infer(routed)
        message = dict(result.message)
        message["_operly_task_route"] = decision.as_dict()
        return replace(result, message=message)


def model_for_role(role: str):
    """Public compatibility resolver; primary agent execution is requirements-routed."""
    if str(role).strip().lower() == "business_agent":
        return TaskRoutedBusinessModel()
    return _base_model_for_role(role)
