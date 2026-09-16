from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from packages.agent_runtime.contracts import AgentBudget
from packages.agent_runtime.inference import OpenAICompatibleAgentModel
from packages.agent_runtime.planning import AgentPlannerModel, AgentPlanningError, GovernedAgentPlanner
from packages.agent_runtime.store import create_run, get_run_for_context, list_steps
from packages.database.agent_runtime_models import AgentRuntimeRun
from packages.kernel.registry import CapabilityRegistry
from packages.personal_modules.runtime import build_personal_runtime
from packages.security.execution_context import ExecutionContext
from packages.security.surfaces import SurfaceKind


class PersonalTaskConflict(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PersonalTaskSubmission:
    row: AgentRuntimeRun
    replayed: bool


def stable_personal_task_id(context: ExecutionContext, request_id: str) -> str:
    principal = str(context.principal_id or "").strip()
    stable_request = str(request_id or "").strip()
    if not principal or not 8 <= len(stable_request) <= 120:
        raise ValueError("Personal task submission requires a stable request_id")
    digest = hashlib.sha256(f"{principal}\0{stable_request}".encode("utf-8")).hexdigest()
    return f"personal-task:{digest}"


def _json_object(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return dict(value) if isinstance(value, dict) else {}


def _json_array(raw: str) -> list[Any]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return list(value) if isinstance(value, list) else []


def _clean_goal(goal: str) -> str:
    clean = " ".join(str(goal or "").replace("\x00", " ").split())
    if not clean:
        raise ValueError("Personal task objective is required")
    return clean


def _deadline_identity(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


async def find_personal_task_replay(
    db: AsyncSession,
    *,
    context: ExecutionContext,
    request_id: str,
    goal: str,
    budget: AgentBudget,
    deadline_at: datetime | None = None,
) -> PersonalTaskSubmission | None:
    """Resolve a transport retry before a new conversation has to be created."""

    if not context.is_personal or context.surface is not SurfaceKind.PERSONAL_PRIVATE:
        raise PersonalTaskConflict("Durable Personal tasks require Personal-private authority")
    clean_goal = _clean_goal(goal)
    run_id = stable_personal_task_id(context, request_id)
    existing = await db.get(AgentRuntimeRun, run_id)
    if existing is None:
        return None
    scoped = await get_run_for_context(db, context=context, run_id=run_id)
    if scoped is None:
        raise PersonalTaskConflict("Personal task request identity belongs to another authority")
    stored_budget = _json_object(existing.budget_json)
    if (
        existing.goal != clean_goal
        or int(stored_budget.get("max_steps", -1)) != budget.max_steps
        or int(stored_budget.get("max_mutations", -1)) != budget.max_mutations
        or _deadline_identity(existing.deadline_at) != _deadline_identity(deadline_at)
        or (
            context.conversation_id is not None
            and existing.conversation_id != context.conversation_id
        )
    ):
        raise PersonalTaskConflict(
            "This request ID was already used for a different Personal task submission"
        )
    return PersonalTaskSubmission(row=existing, replayed=True)


async def submit_personal_task(
    db: AsyncSession,
    *,
    context: ExecutionContext,
    request_id: str,
    goal: str,
    budget: AgentBudget,
    deadline_at: datetime | None = None,
    model: AgentPlannerModel | None = None,
) -> PersonalTaskSubmission:
    """Plan and persist one Personal task before it is acknowledged to the caller.

    The client request identity deterministically names the durable run inside the
    authenticated Personal principal. A transport replay therefore returns the same
    task instead of creating another logical workflow. A changed objective/budget under
    the same request ID fails closed.
    """

    replay = await find_personal_task_replay(
        db,
        context=context,
        request_id=request_id,
        goal=goal,
        budget=budget,
        deadline_at=deadline_at,
    )
    if replay is not None:
        return replay

    clean_goal = _clean_goal(goal)
    run_id = stable_personal_task_id(context, request_id)

    personal_runtime = build_personal_runtime()
    available = await personal_runtime.available_capabilities(
        db,
        context=context,
        query=clean_goal,
        limit=25,
    )
    if not available:
        raise AgentPlanningError(
            "No currently available Personal capability can satisfy this objective",
            code="no_authorized_capabilities",
        )

    # The planner sees only capabilities that are both authorized and currently
    # available for this account. The execution worker still re-resolves provider
    # availability and authority again before every durable step.
    planning_registry = CapabilityRegistry()
    for spec in available:
        planning_registry.register(spec)

    planner = GovernedAgentPlanner(
        registry=planning_registry,
        model=model or OpenAICompatibleAgentModel(),
    )
    plan = await planner.plan(
        run_id=run_id,
        goal=clean_goal,
        context=context,
        budget=budget,
    )
    row = await create_run(db, context=context, plan=plan)
    row.deadline_at = deadline_at
    row.plan_version = 1
    row.checkpoint_version = 0
    # This is deliberately a reference to the live authority source, not a persisted
    # permission snapshot. The worker re-resolves the principal's current authority
    # before every capability, so revoked permissions cannot be resurrected by a task.
    row.grants_reference_json = json.dumps(
        {
            "authority_mode": "live_reresolve",
            "principal_id": str(context.principal_id or ""),
            "scope_kind": context.scope_kind.value,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    row.verified_observations_json = "[]"
    row.wait_predicate_json = "{}"
    await db.flush()
    return PersonalTaskSubmission(row=row, replayed=False)


async def personal_task_payload(
    db: AsyncSession,
    *,
    context: ExecutionContext,
    run_id: str,
) -> dict[str, Any] | None:
    row = await get_run_for_context(db, context=context, run_id=run_id)
    if row is None:
        return None
    steps = await list_steps(db, run_id=row.id)
    current = next((step for step in steps if step.step_id == row.current_step_id), None)
    return {
        "task_id": row.id,
        "scope_kind": row.scope_kind,
        "conversation_id": row.conversation_id,
        "objective": row.goal,
        "status": row.status,
        "plan_version": int(row.plan_version or 1),
        "checkpoint_version": int(row.checkpoint_version or 0),
        "current_step_id": row.current_step_id,
        "cancellation_requested": bool(row.cancellation_requested),
        "approval_id": current.approval_id if current is not None else None,
        "deadline_at": row.deadline_at.isoformat() if row.deadline_at else None,
        "grants_reference": _json_object(row.grants_reference_json),
        "verified_observations": _json_array(row.verified_observations_json),
        "wait_predicate": _json_object(row.wait_predicate_json),
        "error_code": row.error_code,
        "error": row.error_message,
        "result": _json_object(row.result_json),
        "steps": [
            {
                "step_id": step.step_id,
                "order": step.step_order,
                "capability_id": step.capability_id,
                "status": step.status,
                "attempt_count": step.attempt_count,
                "request_id": step.request_id,
                "kernel_run_id": step.kernel_run_id,
                "approval_id": step.approval_id,
                "result": _json_object(step.result_json),
                "error_code": step.error_code,
                "error": step.error_message,
            }
            for step in steps
        ],
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }