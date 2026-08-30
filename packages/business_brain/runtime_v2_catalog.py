"""Surface-neutral exact capability catalog narrowing for Agent Runtime v2.

The planner never receives the full registry. Application code selects a small,
authorized, availability-aware catalog by domain, then Runtime v2 can expose only
the exact schemas selected in the plan. Workspace and Personal surfaces share this
module so capability selection does not become another parallel orchestrator.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any


VisibilityPredicate = Callable[[str], bool]


def _mentions(lowered: str, *values: str) -> bool:
    return any(value in lowered for value in values)


def domain_catalog_requests(objective: str) -> list[dict[str, Any]]:
    """Return deterministic domain slices for one literal objective."""

    lowered = f" {str(objective or '').lower()} "
    requests: list[dict[str, Any]] = []
    if _mentions(lowered, "email", "gmail", "mail", "inbox"):
        requests.append(
            {
                "query": "gmail email search read message thread draft send",
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
                "query": "calendar list events read create schedule",
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
                "query": "task list create durable task reminder",
                "namespace": "task.",
                "alternate_namespaces": ("reminders.",),
                "preferred": tuple(task_preferred),
            }
        )
    if _mentions(lowered, "workspace", "workspaces", "organization", "tenant"):
        requests.append(
            {
                "query": "account workspace list resolve capabilities execute",
                "namespace": "account.",
                "alternate_namespaces": ("scope.", "workspace."),
                "preferred": ("account.list_workspaces",),
            }
        )
    if _mentions(lowered, "discord", "server", "channel"):
        requests.append({"query": "discord", "namespace": "discord.", "preferred": ()})
    if _mentions(lowered, "canva", "design"):
        requests.append({"query": "canva design", "namespace": "canva.", "preferred": ()})
    if _mentions(lowered, "attachment", "pdf", "spreadsheet", "uploaded file") or _mentions(
        lowered,
        "create a document",
        "read the file",
        "process the file",
        "inspect the file",
        "convert the file",
    ):
        requests.append(
            {
                "query": "files artifact attachment document spreadsheet convert",
                "namespace": "files.",
                "alternate_namespaces": ("artifact.",),
                "preferred": (),
            }
        )
    if _mentions(lowered, "website", "site", "web page"):
        requests.append({"query": "website", "namespace": "website.", "preferred": ()})
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
    if _mentions(lowered, "web search", "search the web", "public page", "website online", "url"):
        requests.append({"query": "public web read search url", "namespace": "web.", "preferred": ()})
    if _mentions(lowered, "deep reason", "stronger model", "another model"):
        requests.append({"query": "model invoke deep reason", "namespace": "model.", "preferred": ()})
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
    return [
        str(item)
        for item in list(schema.get("required") or [])[:12]
        if str(item).strip()
    ]


def _namespace_matches(capability_id: str, request: dict[str, Any]) -> bool:
    namespace = str(request.get("namespace") or "")
    alternates = tuple(str(item) for item in request.get("alternate_namespaces") or ())
    return not namespace or capability_id.startswith((namespace, *alternates))


async def compact_capability_catalog(
    *,
    objective: str,
    scope_id: str,
    authority: set[str],
    registry,
    visible: VisibilityPredicate,
    limit: int = 30,
) -> list[dict[str, Any]]:
    """Build a small authorized, availability-aware catalog without domain crowd-out."""

    requests = domain_catalog_requests(objective)
    rows_by_id: dict[str, dict[str, Any]] = {}

    def add_row(row: dict[str, Any], request: dict[str, Any]) -> None:
        capability_id = str(row.get("id") or "").strip()
        if (
            not capability_id
            or capability_id in rows_by_id
            or not _namespace_matches(capability_id, request)
            or not visible(capability_id)
        ):
            return
        try:
            definition = registry.definition(capability_id)
            availability = registry.availability(
                scope_id,
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
        for preferred_id in preferred:
            for row in registry.search(
                scope_id,
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
                scope_id,
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

    wants_gmail = any(str(item.get("namespace") or "") == "gmail." for item in requests)
    if wants_gmail and "gmail.search" not in rows_by_id:
        fallback_request = {
            "query": "context search get email history",
            "namespace": "context.",
            "preferred": ("context.search", "context.get"),
        }
        for preferred_id in fallback_request["preferred"]:
            for row in registry.search(
                scope_id,
                preferred_id,
                authority=authority,
                limit=8,
            ):
                if str(row.get("id") or "").strip() == preferred_id:
                    add_row(row, fallback_request)
                    break

    return list(rows_by_id.values())[: max(1, min(int(limit), 32))]
