"""Adaptive structured planning for objectives that actually need decomposition."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from packages.agents.run_state import CompactRunState, RunPlan, RunTask
from packages.database.runtime_trace_events import emit_runtime_trace_event
from packages.model_runtime import InferenceBudget, InferenceRequest
from packages.model_runtime.registry import model_for_role
from packages.model_runtime.trace_events import RuntimeTraceEvent


_ACTION_WORDS = frozenset(
    {
        "analyze",
        "research",
        "find",
        "compare",
        "create",
        "generate",
        "write",
        "edit",
        "update",
        "send",
        "email",
        "schedule",
        "deploy",
        "test",
        "monitor",
        "track",
        "publish",
        "report",
    }
)
_COMPLEXITY_MARKERS = (
    " and then ",
    " then ",
    " after that ",
    " once ",
    " before ",
    " across ",
    " workflow",
    " multiple ",
    " several ",
    " parallel",
)


@dataclass(frozen=True, slots=True)
class ComplexityDecision:
    planning_required: bool
    score: int
    reasons: tuple[str, ...]


class ObjectiveComplexityGate:
    """Cheap deterministic fast path so simple requests pay no planner call."""

    @staticmethod
    def evaluate(objective: str) -> ComplexityDecision:
        text = " ".join(str(objective or "").lower().split())
        if not text:
            return ComplexityDecision(False, 0, ())
        score = 0
        reasons: list[str] = []
        words = set(re.findall(r"[a-z0-9_-]+", text))
        actions = sorted(words & _ACTION_WORDS)
        if len(actions) >= 3:
            score += 3
            reasons.append("multiple_actions")
        elif len(actions) == 2:
            score += 1
            reasons.append("two_actions")
        markers = [marker.strip() for marker in _COMPLEXITY_MARKERS if marker in f" {text} "]
        if markers:
            score += min(3, len(markers) + 1)
            reasons.append("dependency_language")
        if len(text) >= 320:
            score += 2
            reasons.append("long_objective")
        elif len(text) >= 180:
            score += 1
            reasons.append("medium_objective")
        if text.count(",") >= 3 or text.count(";") >= 2:
            score += 1
            reasons.append("many_clauses")
        return ComplexityDecision(score >= 3, score, tuple(reasons))


def _parse_json_object(value: str) -> dict[str, Any]:
    text = str(value or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                return {}
        return {}


def _bounded_strings(value: Any, *, limit: int, item_chars: int = 500) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(
        str(item).strip()[:item_chars]
        for item in value[:limit]
        if str(item).strip()
    )


class AdaptivePlanner:
    """Use a small reasoning model for a bounded implementation-ready run plan."""

    def __init__(self, *, max_tasks: int = 8) -> None:
        self.max_tasks = max(1, min(int(max_tasks), 12))

    def _fallback(self, objective: str, *, revision: int = 0) -> RunPlan:
        return RunPlan(
            objective=objective,
            success_criteria=("The requested objective is truthfully completed or a concrete blocker is reported.",),
            tasks=[
                RunTask(
                    id="task-1",
                    objective=objective,
                    success_criteria=("Complete the bounded objective and verify any claimed external action.",),
                )
            ],
            planning_required=True,
            revision=revision,
        )

    def _normalize_plan(
        self,
        objective: str,
        payload: dict[str, Any],
        *,
        revision: int,
    ) -> RunPlan:
        tasks_raw = payload.get("tasks")
        if not isinstance(tasks_raw, list) or not tasks_raw:
            return self._fallback(objective, revision=revision)
        tasks: list[RunTask] = []
        seen: set[str] = set()
        for index, raw in enumerate(tasks_raw[: self.max_tasks], start=1):
            if not isinstance(raw, dict):
                continue
            task_id = str(raw.get("id") or f"task-{index}").strip()[:80]
            if not task_id or task_id in seen:
                task_id = f"task-{index}"
            seen.add(task_id)
            task_objective = " ".join(str(raw.get("objective") or "").split()).strip()[:2000]
            if not task_objective:
                continue
            tasks.append(
                RunTask(
                    id=task_id,
                    objective=task_objective,
                    dependencies=_bounded_strings(raw.get("dependencies"), limit=8, item_chars=80),
                    success_criteria=_bounded_strings(raw.get("success_criteria"), limit=6),
                    context_intents=_bounded_strings(raw.get("context_intents"), limit=8),
                    capability_intents=_bounded_strings(raw.get("capability_intents"), limit=8),
                    assigned_role=str(raw.get("assigned_role") or "business_agent")[:80],
                    can_parallelize=bool(raw.get("can_parallelize", False)),
                )
            )
        if not tasks:
            return self._fallback(objective, revision=revision)
        ids = {task.id for task in tasks}
        for task in tasks:
            task.dependencies = tuple(dep for dep in task.dependencies if dep in ids and dep != task.id)
        return RunPlan(
            objective=objective,
            success_criteria=_bounded_strings(payload.get("success_criteria"), limit=8),
            tasks=tasks,
            planning_required=True,
            revision=revision,
        )

    async def _infer_plan(
        self,
        *,
        objective: str,
        extra: str = "",
    ) -> dict[str, Any]:
        model = model_for_role("requirements_analyst")
        system = (
            "You are OPERLY's bounded workflow planner. Return JSON only; do not provide chain-of-thought. "
            "Decompose only when it materially helps execution. Identify what context is needed, what operations are needed, dependencies, parallelizable work, and observable success criteria. "
            "Do not invent capability IDs; use capability intents such as 'search email' or 'create calendar event'. Keep the plan concise."
        )
        user = {
            "objective": objective,
            "additional_run_state": extra,
            "output_contract": {
                "success_criteria": ["observable criterion"],
                "tasks": [
                    {
                        "id": "task-1",
                        "objective": "bounded sub-objective",
                        "dependencies": [],
                        "success_criteria": [],
                        "context_intents": [],
                        "capability_intents": [],
                        "assigned_role": "business_agent",
                        "can_parallelize": False,
                    }
                ],
            },
            "limits": {"max_tasks": self.max_tasks},
        }
        result = await model.infer(
            InferenceRequest(
                messages=(
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
                ),
                budget=InferenceBudget(
                    timeout_seconds=12.0,
                    attempts_per_model=1,
                    max_models=2,
                    max_output_tokens=3000,
                ),
                metadata={"runtime_component": "adaptive_planner"},
            )
        )
        return _parse_json_object(str(result.message.get("content") or ""))

    async def plan(
        self,
        objective: str,
        *,
        trace_metadata: dict[str, Any] | None = None,
    ) -> RunPlan:
        decision = ObjectiveComplexityGate.evaluate(objective)
        if not decision.planning_required:
            return RunPlan(
                objective=objective,
                success_criteria=(),
                tasks=[],
                planning_required=False,
                revision=0,
            )
        try:
            payload = await self._infer_plan(objective=objective)
            plan = self._normalize_plan(objective, payload, revision=0)
        except (LookupError, RuntimeError, ValueError):
            plan = self._fallback(objective)
        await emit_runtime_trace_event(
            RuntimeTraceEvent.PLAN_CREATED,
            {
                "planning_required": True,
                "complexity_score": decision.score,
                "complexity_reasons": list(decision.reasons),
                "task_count": len(plan.tasks),
                "task_ids": [task.id for task in plan.tasks],
            },
            metadata=dict(trace_metadata or {}),
            component="agent-run-controller",
        )
        return plan

    async def replan(
        self,
        state: CompactRunState,
        *,
        reason: str,
        trace_metadata: dict[str, Any] | None = None,
    ) -> RunPlan:
        revision = (state.plan.revision if state.plan else 0) + 1
        extra = json.dumps(
            {
                "reason_for_replan": str(reason)[:1000],
                "compact_run_state": state.prompt_summary(),
            },
            ensure_ascii=False,
            default=str,
        )[:12_000]
        try:
            payload = await self._infer_plan(objective=state.objective, extra=extra)
            plan = self._normalize_plan(state.objective, payload, revision=revision)
        except (LookupError, RuntimeError, ValueError):
            plan = self._fallback(state.objective, revision=revision)
        await emit_runtime_trace_event(
            RuntimeTraceEvent.PLAN_REVISED,
            {
                "revision": revision,
                "reason": str(reason)[:500],
                "task_count": len(plan.tasks),
                "task_ids": [task.id for task in plan.tasks],
            },
            metadata=dict(trace_metadata or {}),
            component="agent-run-controller",
        )
        return plan
