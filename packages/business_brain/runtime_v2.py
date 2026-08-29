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
from packages.capabilities.agent_harness import PluginAgentHarness, PluginInvocationContext
from packages.security.execution_context import ExecutionContext


class RuntimeV2Engine(RuntimeV2ProjectedEngineMixin, _BaseRuntimeV2Engine):
    """Runtime v2 engine with capability-aware worker-state projection."""


def _mentions(lowered: str, *values: str) -> bool:
    return any(value in lowered for value in values)


def _domain_catalog_requests(objective: str) -> list[dict[str, Any]]:
    """Return deterministic domain slices for the planner's capability catalog.

    This is application-owned catalog narrowing, not model-authored pseudo-intent
    resolution. Exact preferred IDs are looked up through the registry and are still
    authorization/availability checked before the planner can see them.
    """

    lowered = str(objective or "").lower()
    requests: list[dict[str, Any]] = []
    if _mentions(lowered, "email", "gmail", "mail", "inbox"):
        requests.append(
            {
                "query": "gmail email search read message thread",
                "namespace": "gmail.",
                "preferred": (
                    "gmail.search",
                    "gmail.read_message",
                    "gmail.read_thread",
                ),
            }
        )
    if _mentions(lowered, "calendar", "meeting", "event", "schedule"):
        requests.append(
            {
                "query": "calendar list events read",
                "namespace": "calendar.",
                "preferred": ("calendar.list_events",),
            }
        )
    if _mentions(lowered, "task", "todo", "reminder") or (
        _mentions(lowered, "follow-up", "follow up")
        and _mentions(lowered, "create", "make", "add")
    ):
        task_preferred = ["task.create"]
        if _mentions(lowered, "duplicate", "already", "existing"):
            task_preferred.insert(0, "task.list")
        requests.append(
            {
                "query": "task list create durable task",
                "namespace": "task.",
                "preferred": tuple(task_preferred),
            }
        )
    if _mentions(lowered, "discord", "server", "channel"):
        requests.append(
            {"query": "discord", "namespace": "discord.", "preferred": ()}
        )
    if _mentions(lowered, "canva", "design"):
        requests.append(
            {"query": "canva design", "namespace": "canva.", "preferred": ()}
        )
    # Avoid treating incidental words such as "document" in "I owe a document" as
    # a request for the file-authoring surface.
    if _mentions(lowered, "attachment", "pdf", "spreadsheet", "uploaded file") or _mentions(
        lowered,
        "create a document",
        "read the file",
        "process the file",
        "inspect the file",
    ):
        requests.append(
            {
                "query": "files artifact attachment document",
                "namespace": "files.",
                "alternate_namespaces": ("artifact.",),
                "preferred": (),
            }
        )
    if _mentions(lowered, "website", "site", "web page"):
        requests.append(
            {"query": "website", "namespace": "website.", "preferred": ()}
        )
    if _mentions(lowered, "software", "codebase", "repository", " repo "):
        requests.append(
            {"query": "software project code repository", "namespace": "software.", "preferred": ()}
        )
    if _mentions(lowered, "memory", "context history", "conversation history"):
        requests.append(
            {
                "query": "context search get history",
                "namespace": "context.",
                "preferred": ("context.search", "context.get"),
            }
        )
    if not requests:
        requests.append(
            {
                "query": str(objective or "")[:800],
                "namespace": "",
                "preferred": (),
            }
        )
    return requests[:6]


def _required_fields(definition) -> list[str]:
    schema = getattr(definition, "input_schema", None)
    if not isinstance(schema, dict):
        return []
    return [str(item) for item in list(schema.get("required") or [])[:12] if str(item).strip()]


def _namespace_matches(capability_id: str, request: dict[str, Any]) -> bool:
    namespace = str(request.get("namespace") or "")
    alternates = tuple(str(item) for item in request.get("alternate_namespaces") or ())
    return not namespace or capability_id.startswith((namespace, *alternates))


async def _compact_catalog(
    *,
    objective: str,
    tenant_id: str,
    authority: set[str],
    registry,
    plugin_harness: PluginAgentHarness,
    plugin_context: PluginInvocationContext,
) -> list[dict[str, Any]]:
    """Build a small per-domain catalog without cross-domain crowd-out.

    Preferred exact operations are resolved first. Semantic search then contributes a
    few adjacent operations from the same namespace. A Gmail request therefore cannot
    lose gmail.search because CRM/files happened to rank above it in one global top-N.
    """

    requests = _domain_catalog_requests(objective)
    rows_by_id: dict[str, dict[str, Any]] = {}

    def add_row(row: dict[str, Any], request: dict[str, Any]) -> None:
        capability_id = str(row.get("id") or "").strip()
        if (
            not capability_id
            or capability_id in rows_by_id
            or not _namespace_matches(capability_id, request)
        ):
            return
        if not plugin_harness.capability_authorized(
            capability_id,
            authority,
            plugin_context,
        ):
            return
        try:
            definition = registry.definition(capability_id)
            availability = registry.availability(
                tenant_id,
                capability_id,
                authority=authority,
            )
        except (LookupError, PermissionError):
            return
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

    for request in requests:
        preferred = tuple(str(item) for item in request.get("preferred") or ())
        domain_ids: list[str] = []

        # Resolve exact preferred IDs first so critical operations cannot be crowded
        # out by semantically adjacent capabilities.
        for preferred_id in preferred:
            for row in registry.search(
                tenant_id,
                preferred_id,
                authority=authority,
                limit=8,
            ):
                if str(row.get("id") or "").strip() != preferred_id:
                    continue
                before = set(rows_by_id)
                add_row(row, request)
                if preferred_id in rows_by_id and preferred_id not in before:
                    domain_ids.append(preferred_id)
                break

        candidates = list(
            registry.search(
                tenant_id,
                str(request.get("query") or objective)[:800],
                authority=authority,
                limit=20,
            )
        )
        preferred_rank = {value: index for index, value in enumerate(preferred)}
        candidates.sort(
            key=lambda row: (
                preferred_rank.get(str(row.get("id") or ""), len(preferred) + 1),
                0 if _namespace_matches(str(row.get("id") or ""), request) else 1,
                str(row.get("id") or ""),
            )
        )
        for row in candidates:
            capability_id = str(row.get("id") or "").strip()
            before = set(rows_by_id)
            add_row(row, request)
            if capability_id in rows_by_id and capability_id not in before:
                domain_ids.append(capability_id)
            if len(domain_ids) >= 6:
                break

    # If Gmail was explicitly requested but direct search truly is absent, offer the
    # federated context fallback rather than silently substituting it when Gmail works.
    wants_gmail = any(str(item.get("namespace") or "") == "gmail." for item in requests)
    if wants_gmail and "gmail.search" not in rows_by_id:
        fallback_request = {
            "query": "context search get email history",
            "namespace": "context.",
            "preferred": ("context.search", "context.get"),
        }
        for preferred_id in fallback_request["preferred"]:
            for row in registry.search(
                tenant_id,
                preferred_id,
                authority=authority,
                limit=8,
            ):
                if str(row.get("id") or "").strip() == preferred_id:
                    add_row(row, fallback_request)
                    break

    return list(rows_by_id.values())[:30]


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
