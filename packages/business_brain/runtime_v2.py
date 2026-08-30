"""Workspace binding for the clean Agent Runtime v2.

Runtime v2 reuses Operly's trusted ExecutionContext, PluginAgentHarness, capability
firewall, approvals and connectors. It replaces only orchestration.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from packages.agent_runtime_v2 import RuntimeV2Planner
from packages.agent_runtime_v2.engine import RuntimeV2Engine as _BaseRuntimeV2Engine
from packages.agent_runtime_v2.state_projection import RuntimeV2ProjectedEngineMixin
from packages.business_brain.runtime_v2_catalog import compact_capability_catalog
from packages.capabilities.agent_harness import PluginAgentHarness, PluginInvocationContext
from packages.security.execution_context import ExecutionContext


class RuntimeV2Engine(RuntimeV2ProjectedEngineMixin, _BaseRuntimeV2Engine):
    """Runtime v2 engine with capability-aware worker-state projection."""


def _blocked_message(blocked: tuple[dict[str, Any], ...]) -> str:
    details = []
    for item in blocked[:6]:
        requirement = str(item.get("requirement") or "Required operation").strip()
        reason = str(item.get("reason") or "unavailable").strip()
        details.append(f"{requirement}: {reason}")
    return "Runtime v2 could not safely start because " + "; ".join(details)


async def run_workspace_runtime_v2(
    *,
    objective: str,
    request,
    conversation_id: str,
    execution: ExecutionContext,
    plugin_harness: PluginAgentHarness,
    plugin_context: PluginInvocationContext,
) -> dict[str, Any]:
    authority = set(execution.permissions)
    registry = await plugin_harness.registry_for(plugin_context)
    session_view = await plugin_harness.session_view_for(
        plugin_context,
        authority=authority,
        registry=registry,
    )
    catalog = await compact_capability_catalog(
        objective=objective,
        scope_id=request.tenant_id,
        authority=authority,
        registry=registry,
        visible=lambda capability_id: plugin_harness.capability_authorized(
            capability_id,
            authority,
            plugin_context,
        ),
    )
    runtime_run_id = str(uuid4())
    metadata = {
        "runtime_run_id": runtime_run_id,
        "runtime_controller": "agent_runtime_v2",
        "tenant_id": request.tenant_id,
        "user_id": plugin_context.user_id,
        "principal_id": request.principal_id,
        "conversation_id": conversation_id,
        "channel": request.channel,
        "surface": execution.surface.value,
        "workspace_mode": execution.workspace_mode,
    }
    runtime_context = {
        "now": datetime.now(timezone.utc).isoformat(),
        "timezone": str(plugin_context.metadata.get("timezone") or "UTC"),
        "surface": execution.surface.value,
        "channel": request.channel,
        "workspace_mode": execution.workspace_mode,
    }
    planned = await RuntimeV2Planner().plan(
        objective=objective,
        capability_catalog=catalog,
        runtime_context=runtime_context,
        metadata=metadata,
    )

    selected = [
        capability_id
        for step in planned.plan.steps
        for capability_id in step.capabilities
    ]
    if selected:
        session_view.expose(selected)

    async def schemas():
        return await plugin_harness.schemas(plugin_context)

    async def invoke(name: str, arguments: dict[str, Any], call_id: str | None):
        return await plugin_harness.invoke(
            name,
            arguments,
            plugin_context,
            call_id=call_id,
        )

    state = await RuntimeV2Engine().run(
        objective=objective,
        plan=planned.plan,
        schemas=schemas,
        invoke=invoke,
        metadata=metadata,
        runtime_context=runtime_context,
        run_id=runtime_run_id,
        planner_input_tokens=planned.input_tokens,
        planner_output_tokens=planned.output_tokens,
    )
    if state.status == "completed":
        final = state.steps.get(planned.plan.final_step_id)
        message = final.summary if final is not None else "Completed."
    elif planned.plan.blocked:
        message = _blocked_message(planned.plan.blocked)
    else:
        failing = next(
            (
                item
                for item in state.steps.values()
                if item.status in {"blocked", "failed", "waiting"}
            ),
            None,
        )
        message = (
            failing.summary
            if failing is not None and failing.summary
            else f"Runtime v2 stopped: {state.stop_reason or state.status}."
        )

    return {
        "message": message,
        "runtime_run_id": runtime_run_id,
        "stop_reason": state.stop_reason,
        "replans": 0,
        "run_plan": planned.plan.as_dict(),
        "execution_truth": {
            "status": state.status.upper(),
            "completed": state.status == "completed",
            "verified": state.status == "completed",
        },
        "runtime_v2": state.as_dict(),
    }
