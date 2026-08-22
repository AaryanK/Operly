import json
from dataclasses import dataclass, field
from typing import Any

from packages.capabilities.defaults import default_registry
from packages.capabilities.firewall import (
    ActionBackedCapabilityFirewall,
    CapabilityInvocation,
)
from packages.capabilities.session_view import SessionCapabilityView
from packages.database.db import session_scope
from packages.security.execution_context import (
    ExecutionContext,
    ExecutionContextError,
    resolve_execution_context,
)
from packages.security.permissions import DEFAULT_ROLE_AUTHORITY, default_permissions


ROLE_AUTHORITY = DEFAULT_ROLE_AUTHORITY


_PRIVATE_CONNECTOR_AUTHORITY = {
    "gmail.search": "gmail:read",
    "gmail.read_message": "gmail:read",
    "gmail.modify_labels": "gmail:write",
    "gmail.create_draft": "gmail:draft",
    "gmail.list_drafts": "gmail:draft",
    "gmail.get_draft": "gmail:draft",
    "gmail.update_draft": "gmail:draft",
    "gmail.send_draft": "messaging:send",
    "gmail.delete_draft": "gmail:draft",
}

_PERSONAL_ONLY_PREFIXES = ("account.",)
_DISCORD_CURRENT_CONTEXT = {
    "discord.context",
    "discord.read_recent_messages",
    "discord.send_message",
    "discord.add_reaction",
    "discord.create_thread",
}

# Migration-safe starter set. Everything else is discoverable through the permanent
# capability kernel instead of being injected into every model turn. These are
# general observation/context operations, not vendor-specific tools.
_DEFAULT_INITIAL_CAPABILITIES = frozenset(
    {
        "company.read_state",
        "company.search_events",
        "runtime.context",
        "context.human.search",
        "context.private_workspace_search",
        "context.tenant.search",
        "context.conversation.search",
        "solution.inspect",
    }
)


@dataclass(slots=True)
class PluginInvocationContext:
    tenant_id: str
    user_id: str | None
    role: str
    objective: str
    channel: str = "web"
    metadata: dict[str, Any] = field(default_factory=dict)


class PluginAgentHarness:
    """Agent-facing capability view over one canonical invocation boundary.

    Authorization details intentionally remain compatible with the current system.
    Consequential execution is delegated to CapabilityFirewall so future auth work
    does not require rewriting every agent/transport surface again.

    Capability visibility is session-scoped and progressive. The model starts with
    a small kernel plus general observation tools, then uses capability.search and
    capability.describe to expose exact schemas as needed. Discovery never grants
    permission; execution still crosses the firewall.
    """

    def __init__(self, registry=None):
        self.registry = registry
        self._session_views: dict[str, SessionCapabilityView] = {}

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
                    enabled_external.update(
                        {
                            "gmail.modify_labels",
                            "gmail.create_draft",
                            "gmail.list_drafts",
                            "gmail.get_draft",
                            "gmail.update_draft",
                            "gmail.send_draft",
                            "gmail.delete_draft",
                        }
                    )
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
        return default_permissions(role)

    async def execution_context_for(
        self,
        context: PluginInvocationContext,
    ) -> ExecutionContext | None:
        if not context.user_id:
            return None
        try:
            async with session_scope() as db:
                return await resolve_execution_context(
                    db,
                    workspace_id=context.tenant_id,
                    user_id=context.user_id,
                    channel=context.channel,
                    conversation_id=str(context.metadata.get("_conversation_id") or "") or None,
                    metadata=context.metadata,
                    require_membership=True,
                )
        except ExecutionContextError:
            return None

    async def authority_for(self, context: PluginInvocationContext) -> set[str]:
        execution = await self.execution_context_for(context)
        return set(execution.permissions) if execution else set()

    @staticmethod
    def _is_private_surface(context: PluginInvocationContext) -> bool:
        if context.channel == "discord":
            return bool(context.metadata.get("is_direct"))
        return not bool(context.metadata.get("shared_surface"))

    @classmethod
    def capability_authorized(
        cls,
        capability_id: str,
        authority: set[str],
        context: PluginInvocationContext | None = None,
    ) -> bool:
        required = _PRIVATE_CONNECTOR_AUTHORITY.get(capability_id)
        if required is not None and required not in authority:
            return False
        if context is None:
            return True
        if capability_id.startswith(_PERSONAL_ONLY_PREFIXES) and not cls._is_private_surface(context):
            return False
        if capability_id in _DISCORD_CURRENT_CONTEXT and context.channel != "discord":
            return False
        return True

    @staticmethod
    def _session_key(context: PluginInvocationContext) -> str:
        conversation = str(context.metadata.get("_conversation_id") or "").strip()
        principal = str(context.user_id or "anonymous")
        # A real conversation ID is preferred. The fallback still scopes transient
        # exposure to one principal/workspace/channel tuple instead of global state.
        return ":".join(
            (
                context.tenant_id,
                principal,
                context.channel,
                conversation or "ephemeral",
            )
        )

    async def session_view_for(
        self,
        context: PluginInvocationContext,
        *,
        authority: set[str] | None = None,
        registry=None,
    ) -> SessionCapabilityView:
        authority = set(authority) if authority is not None else await self.authority_for(context)
        registry = registry or await self.registry_for(context)
        key = self._session_key(context)
        existing = self._session_views.get(key)

        if existing is not None and existing.tenant_id == context.tenant_id:
            # Registry snapshots are intentionally refreshed so connector/plugin
            # availability can change during a conversation. Preserve the session's
            # discovered IDs while swapping in the fresh registry and authority.
            # schemas() re-validates every exposed ID, so a revoked permission or
            # disconnected plugin immediately disappears without broadening access.
            existing.registry = registry
            existing.authority = authority
            existing.visible_predicate = lambda capability_id: self.capability_authorized(
                capability_id,
                authority,
                context,
            )
            return existing

        view = SessionCapabilityView(
            registry,
            context.tenant_id,
            authority,
            visible_predicate=lambda capability_id: self.capability_authorized(
                capability_id,
                authority,
                context,
            ),
            initial_ids=_DEFAULT_INITIAL_CAPABILITIES,
        )
        self._session_views[key] = view
        return view

    async def schemas(self, context: PluginInvocationContext) -> list[dict[str, Any]]:
        authority = await self.authority_for(context)
        if not authority:
            return []
        registry = await self.registry_for(context)
        view = await self.session_view_for(
            context,
            authority=authority,
            registry=registry,
        )
        return view.schemas()

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
        execution = await self.execution_context_for(context)
        if execution is None:
            return {"ok": False, "error": "Unknown or unauthorized plugin"}
        authority = set(execution.permissions)
        if not authority or not self.capability_authorized(name, authority, context):
            return {"ok": False, "error": "Unknown or unauthorized plugin"}

        registry = await self.registry_for(context)
        view = await self.session_view_for(
            context,
            authority=authority,
            registry=registry,
        )
        if name not in view.exposed_ids or not view._visible(name):
            return {
                "ok": False,
                "error": "Capability is not exposed in this model session; discover and describe it first",
            }

        try:
            definition = registry.definition(name)
        except LookupError:
            return {"ok": False, "error": "Unknown or unauthorized plugin"}

        clean_arguments = dict(arguments)
        rationale = str(
            clean_arguments.pop("_rationale", "")
            or f"Model selected {name} for the current objective"
        )[:2000]
        expected = str(
            clean_arguments.pop("_expected_outcome", "") or definition.description
        )[:2000]

        # Discovery metadata needs the current authority set but cannot derive it
        # from model-visible arguments. Pass it only in runtime invocation metadata.
        runtime_metadata = dict(context.metadata)
        if name in {"capability.search", "capability.describe"}:
            runtime_metadata["authority"] = sorted(authority)

        firewall = ActionBackedCapabilityFirewall(registry)
        result = await firewall.invoke(
            CapabilityInvocation(
                capability_id=name,
                arguments=clean_arguments,
                objective=context.objective,
                rationale=rationale,
                expected_outcome=expected,
                call_id=call_id,
                channel=context.channel,
                metadata=runtime_metadata,
            ),
            execution,
        )
        payload = result.as_dict()
        view.observe(name, payload)
        return payload

    async def run_session(
        self,
        client,
        messages: list[dict[str, Any]],
        context: PluginInvocationContext,
        max_steps: int = 8,
    ):
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
