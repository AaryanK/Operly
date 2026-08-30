"""Durable approval continuation for canonical Workspace Agent Runtime v2.

Runtime v2 owns orchestration. This module binds its RunState to the existing
AgentRunRecord store so a governed capability can stop at WAITING_APPROVAL and
continue after the durable action reaches a terminal state.

Continuation always re-authorizes the original user against current workspace
membership and permissions. The approving admin never becomes resumed authority.
Verified mutations are replay-protected while the unfinished plan continues.
"""
from __future__ import annotations

import copy
import json
from typing import Any

from sqlalchemy import select

from packages.agent_runtime_v2.contracts import (
    Observation,
    Plan,
    RunState,
    Step,
    StepOutput,
    StepState,
)
from packages.agent_runtime_v2.engine import _cacheable
from packages.agents.persistence import checkpoint_agent_run
from packages.business_brain.runtime_v2 import RuntimeV2Engine
from packages.capabilities.agent_harness import PluginAgentHarness, PluginInvocationContext
from packages.database.agent_models import AgentMessage
from packages.database.artifact_models import AgentRunRecord
from packages.database.company_models import BusinessActionRecord
from packages.database.db import session_scope
from packages.security.execution_context import ExecutionContextError, resolve_execution_context
from packages.security.surfaces import SurfaceKind

_WAITING_RUN_STATES = ("waiting_approval", "waiting_external", "running")
_TERMINAL_ACTION_STATES = frozenset({"VERIFIED", "REJECTED", "FAILED", "VERIFICATION_FAILED"})
_APPROVAL_WAIT_STATES = frozenset({"WAITING_APPROVAL", "AWAITING_APPROVAL"})


def _json_object(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _step_output(raw: Any) -> StepOutput | None:
    if not isinstance(raw, dict):
        return None
    coverage = raw.get("coverage") if isinstance(raw.get("coverage"), dict) else {}
    return StepOutput(
        summary=str(raw.get("summary") or "")[:8_000],
        findings=tuple(
            dict(item)
            for item in list(raw.get("findings") or [])[:100]
            if isinstance(item, dict)
        ),
        refs=tuple(
            str(item)
            for item in list(raw.get("refs") or [])[:200]
            if str(item).strip()
        ),
        coverage_complete=(
            bool(coverage.get("complete"))
            if coverage.get("complete") is not None
            else None
        ),
        coverage_reason=str(coverage.get("reason") or "")[:4_000],
    )


def _plan(raw: Any) -> Plan:
    value = raw if isinstance(raw, dict) else {}
    steps: list[Step] = []
    for item in list(value.get("steps") or []):
        if not isinstance(item, dict):
            continue
        run_if = item.get("run_if") if isinstance(item.get("run_if"), dict) else {}
        steps.append(
            Step(
                id=str(item.get("id") or "").strip(),
                objective=str(item.get("objective") or "")[:20_000],
                capabilities=tuple(
                    str(capability)
                    for capability in list(item.get("capabilities") or [])
                    if str(capability).strip()
                ),
                depends_on=tuple(
                    str(dependency)
                    for dependency in list(item.get("depends_on") or [])
                    if str(dependency).strip()
                ),
                mutating=bool(item.get("mutating")),
                run_if_step_id=(str(run_if.get("step_id") or "").strip() or None),
                run_if_field=(str(run_if.get("field") or "").strip() or None),
                run_if_equals=bool(run_if.get("equals", True)),
                requires_complete_coverage=bool(item.get("requires_complete_coverage")),
            )
        )
    return Plan(
        goal=str(value.get("goal") or "")[:20_000],
        constraints=tuple(
            str(item)
            for item in list(value.get("constraints") or [])[:100]
            if str(item).strip()
        ),
        steps=tuple(steps),
        final_step_id=str(value.get("final_step_id") or "").strip(),
        blocked=tuple(
            dict(item)
            for item in list(value.get("blocked") or [])[:100]
            if isinstance(item, dict)
        ),
    )


def _state(raw: Any) -> RunState:
    value = raw if isinstance(raw, dict) else {}
    plan = _plan(value.get("plan"))
    raw_steps = value.get("steps") if isinstance(value.get("steps"), dict) else {}
    steps: dict[str, StepState] = {}
    for step in plan.steps:
        item = raw_steps.get(step.id) if isinstance(raw_steps.get(step.id), dict) else {}
        observations: list[Observation] = []
        for observation in list(item.get("observations") or []):
            if not isinstance(observation, dict):
                continue
            observations.append(
                Observation(
                    capability_id=str(observation.get("capability_id") or ""),
                    arguments=(
                        dict(observation.get("arguments") or {})
                        if isinstance(observation.get("arguments"), dict)
                        else {}
                    ),
                    result=(
                        dict(observation.get("result") or {})
                        if isinstance(observation.get("result"), dict)
                        else {}
                    ),
                    signature=str(observation.get("signature") or ""),
                    memoized=bool(observation.get("memoized")),
                )
            )
        steps[step.id] = StepState(
            id=step.id,
            status=str(item.get("status") or "pending"),
            summary=str(item.get("summary") or "")[:8_000],
            output=_step_output(item.get("output")),
            observations=observations,
            model_calls=max(0, int(item.get("model_calls") or 0)),
            input_tokens=max(0, int(item.get("input_tokens") or 0)),
            output_tokens=max(0, int(item.get("output_tokens") or 0)),
        )
    usage = value.get("token_usage") if isinstance(value.get("token_usage"), dict) else {}
    return RunState(
        run_id=str(value.get("run_id") or "").strip(),
        objective=str(value.get("objective") or plan.goal or "")[:50_000],
        plan=plan,
        steps=steps,
        runtime_context=(
            dict(value.get("runtime_context") or {})
            if isinstance(value.get("runtime_context"), dict)
            else {}
        ),
        status=str(value.get("status") or "running"),
        stop_reason=(str(value.get("stop_reason")) if value.get("stop_reason") else None),
        mutation_epoch=max(0, int(value.get("mutation_epoch") or 0)),
        model_calls=max(0, int(value.get("model_calls") or 0)),
        input_tokens=max(0, int(usage.get("input_tokens") or 0)),
        output_tokens=max(0, int(usage.get("output_tokens") or 0)),
    )


def _resume_context(*, request, conversation_id: str, execution, user_id: str | None) -> dict[str, Any]:
    return {
        "tenant_id": request.tenant_id,
        "user_id": user_id,
        # ActionService persists the authority-layer principal, not the web surface
        # conversation alias (for example user:<id> vs web-user:<id>).
        "principal_id": execution.principal_id or request.principal_id,
        "conversation_principal_id": request.principal_id,
        "conversation_id": conversation_id,
        "channel": request.channel,
        "surface": execution.surface.value,
        "workspace_mode": execution.workspace_mode,
    }


def _has_approval_wait(runtime_state: dict[str, Any]) -> bool:
    raw_steps = runtime_state.get("steps") if isinstance(runtime_state.get("steps"), dict) else {}
    for step in raw_steps.values():
        if not isinstance(step, dict):
            continue
        for observation in list(step.get("observations") or []):
            if not isinstance(observation, dict):
                continue
            result = observation.get("result") if isinstance(observation.get("result"), dict) else {}
            status = str(result.get("status") or result.get("lifecycle_status") or "").upper()
            if status in _APPROVAL_WAIT_STATES:
                return True
    return False


def _checkpoint_lifecycle(runtime_state: dict[str, Any]) -> str:
    status = str(runtime_state.get("status") or "").lower()
    if status == "completed":
        return "completed"
    if status == "waiting":
        return "waiting_approval" if _has_approval_wait(runtime_state) else "waiting_external"
    if status in {"blocked", "failed"}:
        return "failed"
    return "running"


async def checkpoint_workspace_runtime_v2(
    *,
    run: dict[str, Any],
    objective: str,
    request,
    conversation_id: str,
    execution,
    user_id: str | None,
) -> None:
    """Persist one Runtime-v2 result using the existing durable AgentRun store."""

    runtime_state = run.get("runtime_v2") if isinstance(run.get("runtime_v2"), dict) else None
    runtime_run_id = str(run.get("runtime_run_id") or "").strip()
    if not runtime_state or not runtime_run_id:
        return
    durable = copy.deepcopy(runtime_state)
    durable["resume_context"] = _resume_context(
        request=request,
        conversation_id=conversation_id,
        execution=execution,
        user_id=user_id,
    )
    lifecycle = _checkpoint_lifecycle(runtime_state)
    await checkpoint_agent_run(
        runtime_run_id=runtime_run_id,
        objective=objective,
        metadata={**durable["resume_context"], "_conversation_id": conversation_id},
        state=durable,
        event_type=f"runtime_v2.{lifecycle}",
        lifecycle_state=lifecycle,
        payload={
            "status": runtime_state.get("status"),
            "stop_reason": runtime_state.get("stop_reason"),
        },
        error=(str(run.get("message") or "")[:20_000] if lifecycle == "failed" else None),
    )


def _matching_observation(raw_state: dict[str, Any], action_id: str) -> tuple[str, int] | None:
    raw_steps = raw_state.get("steps") if isinstance(raw_state.get("steps"), dict) else {}
    for step_id, step in raw_steps.items():
        if not isinstance(step, dict):
            continue
        for index, observation in enumerate(list(step.get("observations") or [])):
            if not isinstance(observation, dict):
                continue
            result = observation.get("result") if isinstance(observation.get("result"), dict) else {}
            if str(result.get("action_id") or "") == action_id:
                return str(step_id), index
    return None


async def _waiting_run_for_action(
    *,
    tenant_id: str,
    action_id: str,
) -> tuple[AgentRunRecord, dict[str, Any], tuple[str, int]] | None:
    async with session_scope() as db:
        rows = list(
            (
                await db.scalars(
                    select(AgentRunRecord)
                    .where(
                        AgentRunRecord.scope_kind == "workspace",
                        AgentRunRecord.tenant_id == tenant_id,
                        AgentRunRecord.scope_id == tenant_id,
                        AgentRunRecord.state.in_(_WAITING_RUN_STATES),
                        AgentRunRecord.completed_at.is_(None),
                    )
                    .order_by(AgentRunRecord.updated_at.desc())
                    .limit(100)
                )
            ).all()
        )
        for row in rows:
            checkpoint = _json_object(row.checkpoint_json)
            match = _matching_observation(checkpoint, action_id)
            if match is not None:
                return row, checkpoint, match
    return None


def _terminal_action_result(action: dict[str, Any]) -> dict[str, Any]:
    status = str(action.get("status") or "").strip().upper()
    result = _json_object(action.get("result_json"))
    verification = _json_object(action.get("verification_json"))
    observation = result.get("evidence") if isinstance(result.get("evidence"), dict) else result
    return {
        "ok": status == "VERIFIED",
        "status": status,
        "action_id": action.get("id"),
        "approval_id": action.get("approval_id"),
        "observation": observation,
        "verification": verification,
        "lifecycle": {
            "status": status,
            "completed": status == "VERIFIED",
            "verified": status == "VERIFIED",
        },
    }


def _terminal_verified(result: dict[str, Any]) -> bool:
    status = str(result.get("status") or result.get("lifecycle_status") or "").upper()
    verification = result.get("verification") if isinstance(result.get("verification"), dict) else {}
    return status in {"VERIFIED", "SUCCESS", "SUCCEEDED", "COMPLETED"} or verification.get("success") is True


def _verified_mutation_receipts(state: RunState) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    receipts: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for step in state.steps.values():
        for observation in step.observations:
            if (
                observation.capability_id
                and not _cacheable(observation.capability_id)
                and _terminal_verified(observation.result)
            ):
                receipts.append(
                    (
                        observation.capability_id,
                        copy.deepcopy(observation.arguments),
                        copy.deepcopy(observation.result),
                    )
                )
    return receipts


async def _continue_state(*, engine: RuntimeV2Engine, state: RunState, schemas, invoke, metadata: dict[str, Any]) -> RunState:
    """Continue only unfinished steps of a reconstructed Runtime-v2 state."""

    state.status = "running"
    state.stop_reason = None
    terminal_dependency_states = {"completed", "skipped"}
    remaining = {
        step.id: step
        for step in state.plan.steps
        if state.steps.get(step.id) is not None
        and state.steps[step.id].status not in terminal_dependency_states
    }
    for step in remaining.values():
        if state.steps[step.id].status == "waiting":
            state.steps[step.id].status = "pending"

    while remaining:
        runnable = [
            step
            for step in remaining.values()
            if all(
                state.steps.get(dependency)
                and state.steps[dependency].status in terminal_dependency_states
                for dependency in step.depends_on
            )
        ]
        if not runnable:
            state.status = "blocked"
            state.stop_reason = "unsatisfied_step_dependencies"
            return state

        step = runnable[0]
        condition_ok, condition_reason = engine._condition_matches(state, step)
        if not condition_ok:
            step_state = state.steps[step.id]
            step_state.status = "skipped"
            step_state.output = StepOutput(
                summary=f"Skipped because condition was false: {condition_reason}",
                findings=(),
                refs=(),
                coverage_complete=True,
                coverage_reason="Step was not required under the execution condition.",
            )
            step_state.summary = step_state.output.summary
            remaining.pop(step.id, None)
            continue

        stop_reason = await engine._run_step(
            state=state,
            step=step,
            schemas=schemas,
            invoke=invoke,
            metadata=metadata,
        )
        remaining.pop(step.id, None)
        if stop_reason is not None:
            state.status = "waiting" if stop_reason == "waiting_external" else "blocked"
            state.stop_reason = stop_reason
            return state

    final = state.steps.get(state.plan.final_step_id)
    if final is None or final.status != "completed":
        state.status = "blocked"
        state.stop_reason = "final_step_incomplete"
        return state
    state.status = "completed"
    state.stop_reason = "completed"
    return state


def _state_message(state: RunState) -> str:
    if state.status == "completed":
        final = state.steps.get(state.plan.final_step_id)
        return final.summary if final is not None and final.summary else "Completed."
    failing = next(
        (item for item in state.steps.values() if item.status in {"blocked", "failed", "waiting"}),
        None,
    )
    if failing is not None and failing.summary:
        return failing.summary
    return f"Runtime v2 stopped: {state.stop_reason or state.status}."


async def _checkpoint_resumed_state(*, state: RunState, resume_context: dict[str, Any], message: str) -> None:
    durable = state.as_dict()
    durable["resume_context"] = dict(resume_context)
    lifecycle = _checkpoint_lifecycle(durable)
    await checkpoint_agent_run(
        runtime_run_id=state.run_id,
        objective=state.objective,
        metadata={
            **resume_context,
            "_conversation_id": resume_context.get("conversation_id"),
        },
        state=durable,
        event_type=f"runtime_v2.resume.{lifecycle}",
        lifecycle_state=lifecycle,
        payload={"status": state.status, "stop_reason": state.stop_reason},
        error=message[:20_000] if lifecycle == "failed" else None,
    )


async def resume_workspace_runtime_v2_after_action(
    *,
    tenant_id: str,
    action_id: str | None,
) -> dict[str, Any] | None:
    """Resume the exact Runtime-v2 step correlated to a terminal workspace action."""

    clean_action_id = str(action_id or "").strip()
    if not clean_action_id:
        return None

    async with session_scope() as db:
        action = await db.get(BusinessActionRecord, clean_action_id)
        if action is None or action.scope_kind != "workspace" or action.tenant_id != tenant_id:
            return None
        action_status = str(action.status or "").strip().upper()
        if action_status not in _TERMINAL_ACTION_STATES:
            return {
                "resumed": False,
                "reason": "action_not_terminal",
                "action_status": action_status,
            }
        action_snapshot = {
            "id": action.id,
            "status": action_status,
            "approval_id": action.approval_id,
            "capability": action.capability,
            "arguments_json": action.arguments_json,
            "result_json": action.result_json,
            "verification_json": action.verification_json,
            "principal_id": action.principal_id,
        }

    located = await _waiting_run_for_action(tenant_id=tenant_id, action_id=clean_action_id)
    if located is None:
        return None
    run, raw_state, (step_id, observation_index) = located
    resume_context = (
        dict(raw_state.get("resume_context") or {})
        if isinstance(raw_state.get("resume_context"), dict)
        else {}
    )
    user_id = str(resume_context.get("user_id") or "").strip() or None
    principal_id = str(resume_context.get("principal_id") or "").strip()
    conversation_id = str(resume_context.get("conversation_id") or run.conversation_id or "").strip() or None
    channel = str(resume_context.get("channel") or run.channel or "operly")
    surface = SurfaceKind.coerce(resume_context.get("surface") or run.surface)

    if not user_id or not principal_id:
        return {
            "resumed": False,
            "runtime_run_id": run.id,
            "step_id": step_id,
            "reason": "original_principal_requires_live_ingress_authority",
        }
    if action_snapshot["principal_id"] and str(action_snapshot["principal_id"]) != principal_id:
        return {
            "resumed": False,
            "runtime_run_id": run.id,
            "step_id": step_id,
            "reason": "action_principal_mismatch",
        }

    try:
        async with session_scope() as db:
            execution = await resolve_execution_context(
                db,
                workspace_id=tenant_id,
                user_id=user_id,
                channel=channel,
                surface=surface,
                conversation_id=conversation_id,
                metadata={"principal_id": principal_id, "_surface_kind": surface.value},
                require_membership=True,
            )
    except ExecutionContextError:
        return {
            "resumed": False,
            "runtime_run_id": run.id,
            "step_id": step_id,
            "reason": "original_authority_unavailable",
        }
    if execution.principal_id != principal_id:
        return {
            "resumed": False,
            "runtime_run_id": run.id,
            "step_id": step_id,
            "reason": "original_principal_changed",
        }

    state = _state(raw_state)
    step_state = state.steps.get(step_id)
    if step_state is None or observation_index >= len(step_state.observations):
        return {
            "resumed": False,
            "runtime_run_id": run.id,
            "step_id": step_id,
            "reason": "runtime_waiting_observation_unavailable",
        }

    terminal_result = _terminal_action_result(action_snapshot)
    step_state.observations[observation_index].result = copy.deepcopy(terminal_result)

    if action_status != "VERIFIED":
        step_state.status = "blocked"
        step_state.summary = f"Action {clean_action_id} ended in {action_status}."
        state.status = "blocked"
        state.stop_reason = "approval_rejected" if action_status == "REJECTED" else "approved_action_failed"
        message = _state_message(state)
        lifecycle = "cancelled" if action_status == "REJECTED" else "failed"
        durable = state.as_dict()
        durable["resume_context"] = resume_context
        await checkpoint_agent_run(
            runtime_run_id=state.run_id,
            objective=state.objective,
            metadata={**resume_context, "_conversation_id": conversation_id},
            state=durable,
            event_type=f"runtime_v2.resume.{lifecycle}",
            lifecycle_state=lifecycle,
            payload={"action_id": clean_action_id, "action_status": action_status},
            error=message[:20_000] if lifecycle == "failed" else None,
        )
        return {
            "resumed": True,
            "runtime_run_id": state.run_id,
            "step_id": step_id,
            "stop_reason": state.stop_reason,
            "execution_truth": {"status": state.status.upper(), "completed": False, "verified": False},
            "runtime_v2": state.as_dict(),
            "message": message,
        }

    step_state.status = "pending"
    step_state.summary = "Approved action verified; continuing the Runtime-v2 plan."
    state.status = "running"
    state.stop_reason = None

    plugin_harness = PluginAgentHarness()
    plugin_metadata = {
        "_conversation_id": conversation_id,
        "_surface_kind": execution.surface.value,
        "principal_id": principal_id,
        "workspace_mode": execution.workspace_mode,
        "effective_permissions": sorted(execution.permissions),
    }
    plugin_context = PluginInvocationContext(
        tenant_id=tenant_id,
        user_id=user_id,
        role=execution.role,
        objective=state.objective,
        channel=channel,
        metadata=plugin_metadata,
        surface=execution.surface,
        principal_id=principal_id,
    )
    registry = await plugin_harness.registry_for(plugin_context)
    session_view = await plugin_harness.session_view_for(
        plugin_context,
        authority=set(execution.permissions),
        registry=registry,
    )
    session_view.expose(
        sorted(
            {
                capability
                for step in state.plan.steps
                if state.steps.get(step.id) is not None
                and state.steps[step.id].status not in {"completed", "skipped"}
                for capability in step.capabilities
            }
        )
    )

    async def schemas():
        return await plugin_harness.schemas(plugin_context)

    receipts = _verified_mutation_receipts(state)

    async def invoke(name: str, arguments: dict[str, Any], call_id: str | None):
        for receipt_name, receipt_arguments, receipt_result in receipts:
            if name == receipt_name and arguments == receipt_arguments:
                return copy.deepcopy(receipt_result)
        return await plugin_harness.invoke(name, arguments, plugin_context, call_id=call_id)

    state = await _continue_state(
        engine=RuntimeV2Engine(),
        state=state,
        schemas=schemas,
        invoke=invoke,
        metadata={
            "runtime_run_id": state.run_id,
            "runtime_controller": "agent_runtime_v2",
            "tenant_id": tenant_id,
            "user_id": user_id,
            "principal_id": principal_id,
            "conversation_id": conversation_id,
            "channel": channel,
            "surface": execution.surface.value,
            "workspace_mode": execution.workspace_mode,
            "resumed_from_action_id": clean_action_id,
        },
    )
    message = _state_message(state)
    await _checkpoint_resumed_state(state=state, resume_context=resume_context, message=message)

    if conversation_id and message:
        async with session_scope() as db:
            db.add(
                AgentMessage(
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    role="assistant",
                    content=message,
                )
            )

    return {
        "resumed": True,
        "runtime_run_id": state.run_id,
        "step_id": step_id,
        "stop_reason": state.stop_reason,
        "execution_truth": {
            "status": state.status.upper(),
            "completed": state.status == "completed",
            "verified": state.status == "completed",
        },
        "runtime_v2": state.as_dict(),
        "message": message,
    }
