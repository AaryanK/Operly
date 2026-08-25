"""Adaptive structured planning only after execution evidence shows it is useful."""
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


@dataclass(frozen=True, slots=True)
class ComplexityDecision:
    """Compatibility shape retained while callers migrate off pre-execution gates."""

    planning_required: bool
    score: int
    reasons: tuple[str, ...]


class ObjectiveComplexityGate:
    """Deprecated compatibility facade: Operly now starts direct instead of keyword-routing.

    Complexity is learned from actual execution evidence. The controller invokes the
    planner only after a capability-backed attempt is incomplete/failed or another
    bounded runtime condition requires decomposition.
    """

    @staticmethod
    def evaluate(objective: str) -> ComplexityDecision:
        del objective
        return ComplexityDecision(False, 0, ("direct_first",))


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
    """Direct first; use a small reasoning model only when evidence demands a plan."""

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
        success_criteria = _bounded_strings(payload.get("success_criteria"), limit=8)
        if not success_criteria:
            success_criteria = (objective[:700],)
        return RunPlan(
            objective=objective,
            success_criteria=success_criteria,
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
            "You are OPERLY's bounded recovery planner. Return JSON only; do not provide chain-of-thought. "
            "A direct capability-backed attempt has already produced incomplete/failing evidence. Decompose only what remains, preserving the literal root objective. "
            "Identify missing context, required operations, dependencies, parallelizable work, and observable success criteria. "
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
                        "objective": "bounded remaining sub-objective",
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
                metadata={"runtime_component": "adaptive_replanner"},
            )
        )
        return _parse_json_object(str(result.message.get("content") or ""))

    async def plan(
        self,
        objective: str,
        *,
        trace_metadata: dict[str, Any] | None = None,
    ) -> RunPlan:
        # No lexical/keyword complexity routing. AgentRuntime gets the first attempt;
        # controller evidence verification decides whether a semantic replan is worth
        # paying for. Informational turns therefore avoid planner/verifier cost unless
        # they actually invoke capabilities.
        del trace_metadata
        return RunPlan(
            objective=objective,
            success_criteria=(),
            tasks=[],
            planning_required=False,
            revision=0,
        )

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
                "strategy": "evidence_triggered",
                "task_count": len(plan.tasks),
                "task_ids": [task.id for task in plan.tasks],
            },
            metadata=dict(trace_metadata or {}),
            component="agent-run-controller",
        )
        return plan
