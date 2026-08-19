import json
from dataclasses import dataclass, field
from typing import Any

from packages.actions.service import ActionService
from packages.capabilities.defaults import default_registry
from packages.database.db import session_scope


ROLE_AUTHORITY = {
    "owner": {
        "company:read",
        "research:read",
        "analytics:read",
        "crm:read",
        "crm:write",
        "website:read",
        "website:write",
        "messaging:draft",
        "messaging:curate",
        "messaging:read",
        "messaging:write",
        "messaging:send",
        "gmail:read",
        "gmail:write",
        "gmail:draft",
        "calendar:read",
        "calendar:write",
        "solution:read",
        "solution:generate",
        "solution:write",
        "tasks:read",
        "tasks:write",
        "memory:read",
        "memory:write",
        "messages:read",
        "catalog:write",
        "inventory:write",
        "orders:write",
        "quotes:write",
        "operations:read",
        "operations:write",
        "reminders:write",
        "context:human:read",
        "context:human:write",
        "context:tenant:read",
        "context:tenant:write",
        "context:conversation:read",
        "context:conversation:write",
    },
    "manager": {
        "company:read",
        "research:read",
        "analytics:read",
        "crm:read",
        "crm:write",
        "website:read",
        "website:write",
        "messaging:draft",
        "messaging:curate",
        "messaging:read",
        "messaging:write",
        "messaging:send",
        "gmail:read",
        "gmail:write",
        "gmail:draft",
        "calendar:read",
        "calendar:write",
        "solution:read",
        "tasks:read",
        "tasks:write",
        "memory:read",
        "memory:write",
        "messages:read",
        "catalog:write",
        "inventory:write",
        "orders:write",
        "quotes:write",
        "operations:read",
        "operations:write",
        "reminders:write",
        "context:human:read",
        "context:human:write",
        "context:tenant:read",
        "context:tenant:write",
        "context:conversation:read",
        "context:conversation:write",
    },
    "agent": {
        "company:read",
        "research:read",
        "analytics:read",
        "crm:read",
        "crm:write",
        "website:read",
        "messaging:draft",
        "messaging:curate",
        "messaging:read",
        "gmail:read",
        "gmail:draft",
        "calendar:read",
        "solution:read",
        "tasks:read",
        "tasks:write",
        "memory:read",
        "memory:write",
        "messages:read",
        "operations:read",
        "reminders:write",
        "context:human:read",
        "context:human:write",
        "context:tenant:read",
        "context:tenant:write",
        "context:conversation:read",
        "context:conversation:write",
    },
    "employee": {
        "company:read",
        "analytics:read",
        "crm:read",
        "website:read",
        "solution:read",
        "tasks:read",
        "messages:read",
        "memory:read",
        "messaging:read",
        "context:human:read",
        "context:human:write",
        "context:tenant:read",
        "context:conversation:read",
        "context:conversation:write",
    },
}


@dataclass(slots=True)
class PluginInvocationContext:
    tenant_id: str
    user_id: str | None
    role: str
    objective: str
    channel: str = "web"
    metadata: dict[str, Any] = field(default_factory=dict)


class PluginAgentHarness:
    """Single execution authority between the reasoning model and plugins."""

    def __init__(self, registry=None):
        self.registry = registry

    async def registry_for(self, context: PluginInvocationContext):
        if self.registry:
            return self.registry

        from sqlalchemy import select

        from packages.connectors.google_provider import (
            CALENDAR,
            CALENDAR_FREEBUSY,
            CALENDAR_LIST_READONLY,
            GMAIL_MODIFY,
            GMAIL_READONLY,
            GMAIL_SEND,
        )
        from packages.database.connector_models import TenantConnector

        enabled_external: set[str] = set()
        async with session_scope() as db:
            rows = (
                await db.scalars(
                    select(TenantConnector).where(
                        TenantConnector.tenant_id == context.tenant_id,
                        TenantConnector.enabled.is_(True),
                        TenantConnector.status == "connected",
                    )
                )
            ).all()
            for row in rows:
                scopes = set(json.loads(row.granted_scopes_json or "[]"))
                if scopes & {GMAIL_SEND, GMAIL_MODIFY}:
                    enabled_external.update({"messaging.send", "gmail.send_email"})
                if scopes & {GMAIL_READONLY, GMAIL_MODIFY}:
                    enabled_external.update({"gmail.search", "gmail.read_message"})
                if GMAIL_MODIFY in scopes:
                    enabled_external.update({"gmail.modify_labels", "gmail.create_draft"})
                if CALENDAR in scopes:
                    enabled_external.update(
                        {
                            "calendar.create_event",
                            "calendar.list_events",
                            "calendar.update_event",
                            "calendar.delete_event",
                        }
                    )
                if CALENDAR_FREEBUSY in scopes:
                    enabled_external.add("calendar.freebusy")
                if CALENDAR_LIST_READONLY in scopes:
                    enabled_external.add("calendar.list_calendars")

        return default_registry(enabled_external)

    def authority(self, role: str) -> set[str]:
        # Unknown channel roles are intentionally deny-by-default. A Discord or
        # future connector participant must resolve to a real TenantMember role
        # before model-visible capabilities become available.
        return set(ROLE_AUTHORITY.get(role, set()))

    async def schemas(self, context: PluginInvocationContext) -> list[dict[str, Any]]:
        registry = await self.registry_for(context)
        return [
            item.model_tool_schema()
            for item in registry.metadata(
                context.tenant_id,
                authority=self.authority(context.role),
            )
        ]

    def handles(self, name: str) -> bool:
        if self.registry:
            return any(item.id == name for item in self.registry.definitions())
        return name.count(".") == 1

    async def invoke(
        self,
        name: str,
        arguments: dict[str, Any],
        context: PluginInvocationContext,
        *,
        call_id: str | None = None,
    ) -> dict[str, Any]:
        authority = self.authority(context.role)
        registry = await self.registry_for(context)
        arguments = dict(arguments)

        async with session_scope() as db:
            service = ActionService(
                db,
                registry,
                authority=authority,
                actor_id=context.user_id,
            )
            try:
                definition = next(
                    item
                    for item in registry.metadata(
                        context.tenant_id,
                        authority=authority,
                    )
                    if item.id == name
                )
            except StopIteration:
                return {"ok": False, "error": "Unknown or unauthorized plugin"}

            rationale = str(
                arguments.pop("_rationale", "")
                or f"Model selected {name} for the current objective"
            )[:2000]
            expected = str(
                arguments.pop("_expected_outcome", "") or definition.description
            )[:2000]

            try:
                action = await service.propose(
                    tenant_id=context.tenant_id,
                    objective=context.objective,
                    capability=name,
                    arguments=arguments,
                    rationale=rationale,
                    expected_outcome=expected,
                    risk_level=definition.risk_level,
                    causation_id=call_id,
                    idempotency_key=(
                        f"{context.tenant_id}:{call_id}" if call_id else None
                    ),
                    runtime_context={
                        "channel": context.channel,
                        "metadata": dict(context.metadata),
                    },
                )
            except (ValueError, PermissionError, LookupError) as error:
                return {"ok": False, "error": str(error)}

            await db.commit()
            result = json.loads(action.result_json or "{}")
            return {
                "ok": action.status in {"VERIFIED", "WAITING_APPROVAL"},
                "action_id": action.id,
                "plugin": name,
                "status": action.status,
                "approval_id": action.approval_id,
                "observation": result.get("evidence", {}),
                "verification": json.loads(action.verification_json or "{}"),
            }

    async def run_session(
        self,
        client,
        messages: list[dict[str, Any]],
        context: PluginInvocationContext,
        max_steps: int = 8,
    ):
        """Reusable adaptive model loop; observations remain in one session."""
        trace = []
        for _ in range(max_steps):
            message = await client.chat(messages, await self.schemas(context))
            messages.append(message)
            calls = message.get("tool_calls") or []
            if not calls:
                return {
                    "message": message.get("content") or "Done.",
                    "trace": trace,
                    "messages": messages,
                }

            for call in calls:
                function = call.get("function") or {}
                arguments = function.get("arguments") or {}
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        arguments = {}
                if not isinstance(arguments, dict):
                    arguments = {}

                result = await self.invoke(
                    str(function.get("name") or ""),
                    arguments,
                    context,
                    call_id=str(call.get("id") or "") or None,
                )
                trace.append({"plugin": function.get("name"), "observation": result})
                messages.append(
                    {
                        "role": "tool",
                        "tool_name": function.get("name"),
                        "content": json.dumps(result, default=str),
                    }
                )

        return {
            "message": "Stopped at the safe plugin-call limit.",
            "trace": trace,
            "messages": messages,
        }
