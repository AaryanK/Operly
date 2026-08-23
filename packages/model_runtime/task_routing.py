"""Task-level routing that runs before business-agent model selection."""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

from packages.model_runtime.contracts import (
    InferenceBudget,
    InferenceRequest,
    InferenceResult,
    ModelTraits,
)
from packages.model_runtime.registry import model_for_role as _base_model_for_role


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


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(str(text or "").lower()))


def classify_business_task(objective: str) -> TaskRouteDecision:
    """Classify the requested kind of work before selecting a role/model pool.

    This deliberately classifies task semantics, not capability/provider names.
    Capability discovery remains a separate harness concern.
    """
    text = str(objective or "").lower()
    tokens = _tokens(text)

    def has(*words: str) -> bool:
        return any(word in tokens or word in text for word in words)

    if has("code", "implement", "debug", "fix", "studio", "website", "app", "html", "javascript"):
        return TaskRouteDecision(
            "coding_or_studio",
            "coding",
            "workspace_write_with_validation",
            0.90,
            "objective requests code, Studio generation, repair, or implementation",
        )
    if has("validate", "verify", "audit", "review", "check", "test"):
        return TaskRouteDecision(
            "validation",
            "global_validator",
            "read_first_validation",
            0.86,
            "objective is primarily validation, review, or verification",
        )
    if has("research", "investigate", "find", "compare", "market", "competitor", "evidence"):
        return TaskRouteDecision(
            "research",
            "requirements_analyst",
            "read_research_sources",
            0.84,
            "objective requires evidence gathering or comparative research",
        )
    if has("plan", "strategy", "roadmap", "design", "architect", "proposal"):
        return TaskRouteDecision(
            "planning",
            "planner",
            "read_then_propose",
            0.82,
            "objective asks for planning, architecture, or a proposal",
        )
    if has("send", "create", "update", "delete", "schedule", "email", "remind", "approve"):
        return TaskRouteDecision(
            "bounded_operation",
            "bounded_task",
            "bounded_action_with_approval",
            0.80,
            "objective is a bounded operational action",
        )
    return TaskRouteDecision(
        "business_reasoning",
        "business_agent",
        "progressive_capability_access",
        0.60,
        "general business reasoning with no stronger task-type signal",
    )


def _last_user_objective(request: InferenceRequest) -> str:
    for message in reversed(request.messages):
        if str(message.get("role") or "") == "user":
            content = message.get("content")
            if isinstance(content, str):
                return content
            return str(content or "")
    return ""


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
    timeout, models, output = defaults[decision.task_type]
    return InferenceBudget(
        timeout_seconds=current.timeout_seconds or timeout,
        attempts_per_model=max(1, current.attempts_per_model),
        max_models=current.max_models or models,
        max_output_tokens=current.max_output_tokens or output,
    )


class TaskRoutedBusinessModel:
    """Lazy model proxy selecting the role pool after the task is understood."""

    id = "task-router:business-agent"
    tags = frozenset({"task-routed"})
    capabilities = frozenset({"text", "tools", "reasoning", "coding"})
    traits = ModelTraits()

    def __init__(self) -> None:
        self.last_decision: TaskRouteDecision | None = None

    async def infer(self, request: InferenceRequest) -> InferenceResult:
        decision = classify_business_task(_last_user_objective(request))
        self.last_decision = decision
        selected = _base_model_for_role(decision.role)
        metadata = dict(request.metadata)
        metadata["task_route"] = decision.as_dict()
        routed = InferenceRequest(
            messages=request.messages,
            tools=request.tools,
            response_schema=request.response_schema,
            modality_inputs=request.modality_inputs,
            budget=_task_budget(decision, request.budget),
            metadata=metadata,
        )
        result = await selected.infer(routed)
        message = dict(result.message)
        # The shared AgentRuntime preserves this field in the run messages, making
        # task routing inspectable without putting provider policy in product code.
        message["_operly_task_route"] = decision.as_dict()
        return replace(result, message=message)


def model_for_role(role: str):
    """Public role resolver with task routing for the primary business agent."""
    if str(role).strip().lower() == "business_agent":
        return TaskRoutedBusinessModel()
    return _base_model_for_role(role)
