from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.agent_runtime_models import (
    AgentRuntimeRun,
    AgentRuntimeStep,
    AgentRuntimeStepAttempt,
)
from packages.security.execution_context import ExecutionContext, ScopeKind
from packages.security.surfaces import SurfaceKind

from .contracts import (
    AgentBudget,
    AgentPlan,
    AgentPlanStep,
    AgentStepResult,
    AgentStepStatus,
    stable_step_request_id,
)


class AgentRunStateError(RuntimeError):
    pass


TERMINAL_RUN_STATUSES = frozenset(
    {"completed", "failed", "cancelled", "budget_exhausted", "execution_uncertain"}
)
_ALLOWED_TRANSITIONS = {
    "queued": frozenset({"running", "cancelled"}),
    "running": frozenset(
        {
            "waiting_approval",
            "completed",
            "failed",
            "cancelled",
            "budget_exhausted",
            "execution_uncertain",
        }
    ),
    "waiting_approval": frozenset({"queued", "cancelled"}),
}
_ALLOWED_PERSONAL_SURFACES = frozenset({SurfaceKind.PERSONAL_PRIVATE})
_ALLOWED_WORKSPACE_SURFACES = frozenset(
    {SurfaceKind.WORKSPACE_PRIVATE, SurfaceKind.WORKSPACE_SHARED}
)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _object_json(raw: str, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as error:
        raise AgentRunStateError(f"Stored {label} is invalid JSON") from error
    if not isinstance(value, dict):
        raise AgentRunStateError(f"Stored {label} must be a JSON object")
    return value


def _plan_json(plan: AgentPlan) -> str:
    return _json(
        {
            "run_id": plan.run_id,
            "goal": plan.goal,
            "steps": [
                {
                    "step_id": step.step_id,
                    "capability_id": step.capability_id,
                    "arguments": dict(step.arguments),
                    "approval_id": step.approval_id,
                }
                for step in plan.steps
            ],
        }
    )


def _budget_json(plan: AgentPlan) -> str:
    return _json(
        {
            "max_steps": plan.budget.max_steps,
            "max_mutations": plan.budget.max_mutations,
        }
    )


def _scope_values(context: ExecutionContext) -> tuple[str, str | None, str | None]:
    if not context.principal_id:
        raise AgentRunStateError("Agent runs require a resolved principal_id")
    if not context.user_id:
        raise AgentRunStateError("Initial agent runtime supports authenticated users only")
    if context.is_guest_workspace:
        raise AgentRunStateError(
            "Guest Workspace agent runs require durable external-installation provenance"
        )
    if context.scope_kind is ScopeKind.PERSONAL:
        if context.surface not in _ALLOWED_PERSONAL_SURFACES:
            raise AgentRunStateError(
                "Initial Personal agent runs require the trusted personal-private surface"
            )
        return "personal", None, context.user_id
    if not context.workspace_id:
        raise AgentRunStateError("Workspace agent runs require a workspace_id")
    if context.surface not in _ALLOWED_WORKSPACE_SURFACES:
        raise AgentRunStateError(
            "Delegated/external Workspace surfaces require durable delegation provenance"
        )
    return "workspace", context.workspace_id, None


def _context_matches_run(context: ExecutionContext, row: AgentRuntimeRun) -> bool:
    if str(context.principal_id or "") != row.principal_id:
        return False
    if row.scope_kind == "personal":
        return (
            context.scope_kind is ScopeKind.PERSONAL
            and context.user_id == row.owner_user_id
            and context.workspace_id is None
        )
    return (
        context.scope_kind is ScopeKind.WORKSPACE
        and context.workspace_id == row.workspace_id
        and not context.is_guest_workspace
    )


async def create_run(
    db: AsyncSession,
    *,
    context: ExecutionContext,
    plan: AgentPlan,
) -> AgentRuntimeRun:
    if await db.get(AgentRuntimeRun, plan.run_id) is not None:
        raise AgentRunStateError("Agent run_id already exists")

    scope_kind, workspace_id, owner_user_id = _scope_values(context)
    row = AgentRuntimeRun(
        id=plan.run_id,
        scope_kind=scope_kind,
        workspace_id=workspace_id,
        owner_user_id=owner_user_id,
        authority_user_id=context.user_id,
        principal_id=str(context.principal_id),
        conversation_id=context.conversation_id,
        source_channel=str(context.channel or "unknown")[:40],
        source_surface=context.surface.value,
        goal=plan.goal,
        plan_json=_plan_json(plan),
        budget_json=_budget_json(plan),
        status="queued",
    )
    db.add(row)
    for order, step in enumerate(plan.steps):
        db.add(
            AgentRuntimeStep(
                agent_run_id=plan.run_id,
                step_id=step.step_id,
                step_order=order,
                capability_id=step.capability_id,
                arguments_json=_json(dict(step.arguments)),
                request_id=stable_step_request_id(plan.run_id, step.step_id),
                status="pending",
                approval_id=step.approval_id,
            )
        )
    await db.flush()
    return row


async def load_plan(db: AsyncSession, *, run_id: str) -> AgentPlan:
    row = await db.get(AgentRuntimeRun, run_id)
    if row is None:
        raise AgentRunStateError("Agent run does not exist")
    budget_data = _object_json(row.budget_json, label="agent budget")
    steps = await list_steps(db, run_id=run_id)
    if not steps:
        raise AgentRunStateError("Durable agent run has no steps")
    try:
        budget = AgentBudget(
            max_steps=int(budget_data.get("max_steps", 0)),
            max_mutations=int(budget_data.get("max_mutations", -1)),
        )
        return AgentPlan(
            run_id=row.id,
            goal=row.goal,
            budget=budget,
            steps=tuple(
                AgentPlanStep(
                    step_id=step.step_id,
                    capability_id=step.capability_id,
                    arguments=_object_json(
                        step.arguments_json, label=f"step {step.step_id} arguments"
                    ),
                    approval_id=step.approval_id,
                )
                for step in steps
            ),
        )
    except (TypeError, ValueError) as error:
        raise AgentRunStateError("Stored agent plan violates runtime contracts") from error


async def list_steps(db: AsyncSession, *, run_id: str) -> list[AgentRuntimeStep]:
    return list(
        (
            await db.scalars(
                select(AgentRuntimeStep)
                .where(AgentRuntimeStep.agent_run_id == run_id)
                .order_by(AgentRuntimeStep.step_order)
            )
        ).all()
    )


async def get_run_for_context(
    db: AsyncSession,
    *,
    context: ExecutionContext,
    run_id: str,
) -> AgentRuntimeRun | None:
    row = await db.get(AgentRuntimeRun, run_id)
    if row is None or not _context_matches_run(context, row):
        return None
    return row


async def request_cancellation(
    db: AsyncSession,
    *,
    context: ExecutionContext,
    run_id: str,
) -> AgentRuntimeRun:
    row = await db.scalar(
        select(AgentRuntimeRun).where(AgentRuntimeRun.id == run_id).with_for_update()
    )
    if row is None or not _context_matches_run(context, row):
        raise AgentRunStateError("Agent run is unavailable in this authority scope")
    if row.status in TERMINAL_RUN_STATUSES:
        return row

    now = datetime.utcnow()
    row.cancellation_requested = True
    has_live_lease = bool(
        row.lease_token and row.lease_until is not None and row.lease_until > now
    )
    if row.status in {"queued", "waiting_approval"} or (
        row.status == "running" and not has_live_lease
    ):
        row.status = "cancelled"
        row.finished_at = now
        row.lease_token = None
        row.lease_until = None
    row.updated_at = now
    await db.flush()
    return row


async def cancellation_requested(db: AsyncSession, *, run_id: str) -> bool:
    result = await db.execute(
        select(AgentRuntimeRun.cancellation_requested).where(AgentRuntimeRun.id == run_id)
    )
    value = result.scalar_one_or_none()
    if value is None:
        raise AgentRunStateError("Agent run does not exist")
    return bool(value)


async def claim_run(
    db: AsyncSession,
    *,
    run_id: str,
    lease_token: str,
    lease_seconds: int = 300,
) -> AgentRuntimeRun | None:
    token = str(lease_token or "").strip()
    if not token or len(token) > 80:
        raise ValueError("lease_token must contain 1-80 characters")
    if not 5 <= lease_seconds <= 900:
        raise ValueError("lease_seconds must be between 5 and 900")

    now = datetime.utcnow()
    lease_until = now + timedelta(seconds=lease_seconds)
    statement = (
        update(AgentRuntimeRun)
        .where(
            AgentRuntimeRun.id == run_id,
            AgentRuntimeRun.status.in_(("queued", "running")),
            or_(
                AgentRuntimeRun.cancellation_requested.is_(False),
                AgentRuntimeRun.status == "running",
            ),
            or_(
                AgentRuntimeRun.lease_until.is_(None),
                AgentRuntimeRun.lease_until < now,
                AgentRuntimeRun.lease_token == token,
            ),
        )
        .values(
            status="running",
            lease_token=token,
            lease_until=lease_until,
            started_at=func.coalesce(AgentRuntimeRun.started_at, now),
            updated_at=now,
        )
    )
    result = await db.execute(statement)
    if result.rowcount != 1:
        return None
    await db.flush()
    return await db.get(AgentRuntimeRun, run_id)


async def renew_run_lease(
    db: AsyncSession,
    *,
    run_id: str,
    lease_token: str,
    lease_seconds: int = 300,
) -> bool:
    token = str(lease_token or "").strip()
    if not token or len(token) > 80:
        raise ValueError("lease_token must contain 1-80 characters")
    if not 5 <= lease_seconds <= 900:
        raise ValueError("lease_seconds must be between 5 and 900")
    now = datetime.utcnow()
    result = await db.execute(
        update(AgentRuntimeRun)
        .where(
            AgentRuntimeRun.id == run_id,
            AgentRuntimeRun.status == "running",
            AgentRuntimeRun.lease_token == token,
        )
        .values(
            lease_until=now + timedelta(seconds=lease_seconds),
            updated_at=now,
        )
    )
    await db.flush()
    return result.rowcount == 1


async def release_run_lease(
    db: AsyncSession,
    *,
    run_id: str,
    lease_token: str,
) -> bool:
    result = await db.execute(
        update(AgentRuntimeRun)
        .where(
            AgentRuntimeRun.id == run_id,
            AgentRuntimeRun.lease_token == str(lease_token or "").strip(),
        )
        .values(lease_token=None, lease_until=None, updated_at=datetime.utcnow())
    )
    await db.flush()
    return result.rowcount == 1


async def transition_run(
    db: AsyncSession,
    *,
    run_id: str,
    to_status: str,
    current_step_id: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    result: dict[str, Any] | None = None,
) -> AgentRuntimeRun:
    row = await db.scalar(
        select(AgentRuntimeRun).where(AgentRuntimeRun.id == run_id).with_for_update()
    )
    if row is None:
        raise AgentRunStateError("Agent run does not exist")
    target = str(to_status or "").strip()
    if target not in _ALLOWED_TRANSITIONS.get(row.status, frozenset()):
        raise AgentRunStateError(f"Invalid agent run transition: {row.status} -> {target}")

    now = datetime.utcnow()
    row.status = target
    row.current_step_id = current_step_id
    row.error_code = error_code
    row.error_message = error_message or None
    if result is not None:
        row.result_json = _json(result)
    if target == "waiting_approval" or target in TERMINAL_RUN_STATUSES:
        row.lease_token = None
        row.lease_until = None
    if target in TERMINAL_RUN_STATUSES:
        row.finished_at = now
    row.updated_at = now
    await db.flush()
    return row


async def queue_after_approval(
    db: AsyncSession,
    *,
    context: ExecutionContext,
    run_id: str,
    approval_id: str,
) -> AgentRuntimeRun:
    approval = str(approval_id or "").strip()
    if not approval or len(approval) > 36:
        raise ValueError("approval_id must contain 1-36 characters")
    row = await db.scalar(
        select(AgentRuntimeRun).where(AgentRuntimeRun.id == run_id).with_for_update()
    )
    if row is None or not _context_matches_run(context, row):
        raise AgentRunStateError("Agent run is unavailable in this authority scope")
    if row.cancellation_requested:
        raise AgentRunStateError("Cancelled agent run cannot resume")
    if row.status != "waiting_approval" or not row.current_step_id:
        raise AgentRunStateError("Agent run is not waiting for approval")
    step = await db.scalar(
        select(AgentRuntimeStep)
        .where(
            AgentRuntimeStep.agent_run_id == run_id,
            AgentRuntimeStep.step_id == row.current_step_id,
        )
        .with_for_update()
    )
    if step is None or step.status != AgentStepStatus.WAITING_APPROVAL.value:
        raise AgentRunStateError("Approval-waiting agent step is unavailable")
    step.approval_id = approval
    step.status = "pending"
    step.error_code = None
    step.error_message = None
    step.updated_at = datetime.utcnow()
    row.status = "queued"
    row.lease_token = None
    row.lease_until = None
    row.error_code = None
    row.error_message = None
    row.updated_at = datetime.utcnow()
    await db.flush()
    return row


async def record_step_result(
    db: AsyncSession,
    *,
    run_id: str,
    step_result: AgentStepResult,
) -> AgentRuntimeStep:
    step = await db.scalar(
        select(AgentRuntimeStep)
        .where(
            AgentRuntimeStep.agent_run_id == run_id,
            AgentRuntimeStep.step_id == step_result.step_id,
        )
        .with_for_update()
    )
    if step is None:
        raise AgentRunStateError("Agent step does not exist")
    if step.capability_id != step_result.capability_id or step.request_id != step_result.request_id:
        raise AgentRunStateError("Agent step result does not match durable step identity")

    now = datetime.utcnow()
    attempt = step.attempt_count + 1
    db.add(
        AgentRuntimeStepAttempt(
            agent_run_id=run_id,
            agent_step_id=step.id,
            attempt=attempt,
            capability_id=step.capability_id,
            request_id=step.request_id,
            status=step_result.status.value,
            kernel_run_id=step_result.kernel_run_id,
            approval_id=step_result.approval_id,
            arguments_json=step.arguments_json,
            result_json=_json(dict(step_result.result or {})),
            error_code=step_result.error_code,
            error_message=step_result.error,
            finished_at=now,
        )
    )
    step.attempt_count = attempt
    step.status = step_result.status.value
    step.kernel_run_id = step_result.kernel_run_id
    step.approval_id = step_result.approval_id
    step.result_json = _json(dict(step_result.result or {}))
    step.error_code = step_result.error_code
    step.error_message = step_result.error
    step.started_at = step.started_at or now
    step.finished_at = (
        now
        if step_result.status
        in {
            AgentStepStatus.COMPLETED,
            AgentStepStatus.FAILED,
            AgentStepStatus.CANCELLED,
            AgentStepStatus.EXECUTION_UNCERTAIN,
        }
        else None
    )
    step.updated_at = now
    await db.flush()
    return step