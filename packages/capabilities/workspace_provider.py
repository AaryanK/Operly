from __future__ import annotations

from sqlalchemy import select

from packages.capabilities.contracts import ApprovalPolicy, CapabilityDefinition, CapabilityResult
from packages.capabilities.providers import BaseProvider
from packages.database.models import Memory, Task
from packages.workspace.service import WorkspaceService


class WorkspaceProvider(BaseProvider):
    name = "operly_workspace"
    capabilities = (
        CapabilityDefinition(
            "tasks.list",
            "tasks_list",
            "List business tasks for the current tenant.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            {"type": "object"},
            risk_level="read_only",
            permissions=("tasks:read",),
            approval_policy=ApprovalPolicy.AUTO,
        ),
        CapabilityDefinition(
            "tasks.create",
            "tasks_create",
            "Create an internal business task.",
            {
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="low",
            permissions=("tasks:write",),
            reversible=True,
        ),
        CapabilityDefinition(
            "tasks.complete",
            "tasks_complete",
            "Mark a business task complete using its ID or unambiguous prefix.",
            {
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="low",
            permissions=("tasks:write",),
            reversible=True,
        ),
        CapabilityDefinition(
            "memory.store",
            "memory_store",
            "Store a business fact only when the owner explicitly asks Operly to remember it.",
            {
                "type": "object",
                "properties": {"fact": {"type": "string"}},
                "required": ["fact"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="low",
            permissions=("memory:write",),
            reversible=True,
        ),
        CapabilityDefinition(
            "memory.search",
            "memory_search",
            "Search stored business memory for the current tenant.",
            {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="read_only",
            permissions=("memory:read",),
            approval_policy=ApprovalPolicy.AUTO,
        ),
        CapabilityDefinition(
            "messages.search",
            "messages_search",
            "Search stored channel messages for the current tenant.",
            {
                "type": "object",
                "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 20}},
                "required": ["query"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="read_only",
            permissions=("messages:read",),
            approval_policy=ApprovalPolicy.AUTO,
        ),
    )

    async def execute(self, context, capability_name, arguments):
        try:
            if capability_name == "tasks.list":
                rows = await WorkspaceService.list_tasks(context.db, context.tenant_id)
                return CapabilityResult(
                    True,
                    False,
                    {"tasks": [{"id": row.id, "title": row.title, "status": row.status} for row in rows[:25]]},
                )
            if capability_name == "tasks.create":
                row = await WorkspaceService.create_task(
                    context.db,
                    context.tenant_id,
                    title=str(arguments["title"]),
                )
                return CapabilityResult(True, True, {"task_id": row.id, "title": row.title}, row.id)
            if capability_name == "tasks.complete":
                row = await WorkspaceService.complete_task_prefix(
                    context.db,
                    context.tenant_id,
                    str(arguments["task_id"]),
                )
                return CapabilityResult(True, True, {"task_id": row.id, "status": row.status}, row.id)
            if capability_name == "memory.store":
                row = await WorkspaceService.create_memory(
                    context.db,
                    context.tenant_id,
                    kind="fact",
                    content=str(arguments["fact"]),
                )
                return CapabilityResult(True, True, {"memory_id": row.id}, row.id)
            if capability_name == "memory.search":
                rows = await WorkspaceService.search_memory(
                    context.db,
                    context.tenant_id,
                    str(arguments["query"]),
                )
                return CapabilityResult(
                    True,
                    False,
                    {
                        "matches": [
                            {"id": row.id, "kind": row.kind, "content": row.content[:1000]}
                            for row in rows
                        ]
                    },
                )
            if capability_name == "messages.search":
                rows = await WorkspaceService.list_messages(
                    context.db,
                    context.tenant_id,
                    search=str(arguments["query"]),
                    limit=int(arguments.get("limit", 20)),
                )
                return CapabilityResult(
                    True,
                    False,
                    {
                        "matches": [
                            {
                                "author": row.author_name,
                                "content": row.content[:900],
                                "created_at": row.created_at.isoformat(),
                            }
                            for row in rows
                        ]
                    },
                )
        except (LookupError, ValueError, TypeError) as error:
            return CapabilityResult(False, False, {"reason": str(error)})
        return CapabilityResult(False, False, {"reason": "unsupported_workspace_capability"})

    async def verify(self, context, capability_name, arguments, result):
        if not result.success:
            return CapabilityResult(False, result.changed, result.evidence, result.external_reference)
        if capability_name in {"tasks.list", "memory.search", "messages.search"}:
            return CapabilityResult(True, False, {"observation_available": True, **result.evidence})
        if not result.external_reference:
            return CapabilityResult(False, result.changed, {"reason": "verification_target_missing"})
        model = Memory if capability_name == "memory.store" else Task
        row = await context.db.scalar(
            select(model).where(model.id == result.external_reference, model.tenant_id == context.tenant_id)
        )
        return CapabilityResult(row is not None, result.changed, {"record_exists": row is not None, **result.evidence}, result.external_reference)
