"""Workspace binding for the clean Agent Runtime v2.

Runtime v2 reuses Operly's trusted ExecutionContext, PluginAgentHarness, capability
firewall, approvals and connectors. It replaces only orchestration.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from packages.agent_runtime_v2 import RuntimeV2Engine, RuntimeV2Planner
from packages.capabilities.agent_harness import PluginAgentHarness, PluginInvocationContext
from packages.security.execution_context import ExecutionContext


_RUNTIME_V2_ENV = "OPERLY_AGENT_RUNTIME_V2"
_TRUE_VALUES = frozenset({"1", "true", "yes", "on", "enabled", "v2"})


def workspace_runtime_v2_enabled() -> bool:
    return str(os.getenv(_RUNTIME_V2_ENV, "0")).strip().lower() in _TRUE_VALUES


def _domain_queries(objective: str) -> list[str]:
    """Create a few deterministic catalog queries from literal request vocabulary.

    This is not model-authored capability intent resolution. It only narrows the
    metadata index sent to the planner; the planner must still select exact IDs and the
    harness rechecks availability/authority at execution.
    """

    lowered = str(objective or "").lower()
    queries: list[str] = []
    markers = (
        (("email", "gmail", "mail", "inbox"), "gmail email read search"),
        (("calendar", "meeting", "event", "schedule"), "calendar events read list"),
        (("task", "todo", "reminder", "follow-up", "follow up"), "task create list"),
        (("discord", "server", "channel"), "discord"),
        (("canva", "design"), "canva design"),
        (("file", "attachment", "pdf", "document", "spreadsheet"), "files artifact"),
        (("website", "site", "page"), "website"),
        (("software", "code", "repository", "repo"), "software"),
    )
    for words, query in markers:
        if any(word in lowered for word in words):
            queries.append(query)
    if not queries:
        queries.append(str(objective or "")[:800])
    return queries[:6]


def _required_fields(definition) -> list[str]:
    schema = getattr(definition, "input_schema", None)
    if not isinstance(schema, dict):
        return []
    return [str(item) for item in list(schema.get("required") or [])[:12] if str(item).strip()]


async def _compact_catalog(
    *,
    objective: str,
    tenant_id: str,
    authority: set[str],
    registry,
    plugin_harness: PluginAgentHarness,
    plugin_context: PluginInvocationContext,
) -> list[dict[str, Any]]:
    rows_by_id: dict[str, dict[str, Any]] = {}
    for query in _domain_queries(objective):
        for row in registry.search(
            tenant_id,
            query,
            authority=authority,
            limit=16,
        ):
            capability_id = str(row.get("id") or "").strip()
            if not capability_id or capability_id in rows_by_id:
                continue
            if not plugin_harness.capability_authorized(
                capability_id,
                authority,
                plugin_context,
            ):
                continue
            try:
                definition = registry.definition(capability_id)
                availability = registry.availability(
                    tenant_id,
                    capability_id,
                    authority=authority,
                )
            except (LookupError, PermissionError):
                continue
            rows_by_id[capability_id] = {
                "id": capability_id,
                "description": str(getattr(definition, "description", "") or "")[:360],
                "risk": str(getattr(definition, "risk_level", "") or ""),
                "required_fields": _required_fields(definition),
                "available": bool(availability.available),
                "unavailable_reason": (
                    str(availability.reason or availability.next_action or "")[:500]
                    if not availability.available
                    else None
                ),
            }
            if len(rows_by_id) >= 28:
                break
        if len(rows_by_id) >= 28:
            break
    return list(rows_by_id.values())


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
    catalog = await _compact_catalog(
        objective=objective,
        tenant_id=request.tenant_id,
        authority=authority,
        registry=registry,
        plugin_harness=plugin_harness,
        plugin_context=plugin_context,
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
