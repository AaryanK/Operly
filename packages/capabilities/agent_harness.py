import json
from dataclasses import dataclass, field
from typing import Any

from packages.agents import AgentRuntime
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
    "gmail.read_thread": "gmail:read",
    "gmail.list_attachments": "gmail:read",
    "gmail.read_attachment": "gmail:read",
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

    Capability visibility is session-scoped and progressive. The harness performs
    one bounded semantic preflight for each new objective so useful capabilities do
    not depend on the model remembering a discovery ceremony. Preflight only exposes
    schemas the caller is already authorized to see; execution still crosses the
    canonical firewall and approval boundary.
    """

    def __init__(self, registry=None):
        self.registry = registry
        self._session_views: dict[str, SessionCapabilityView] = {}
        self._preflight_objectives: dict[str, str] = {}
        self._preflight_diagnostics: dict[str, list[dict[str, Any]]] = {}

    async def registry_for(self, context: PluginInvocationContext):
        if self.registry:
            return self.registry

        from sqlalchemy import select
        from packages.connectors.google_provider import (
            GMAIL_MODIFY,
            GMAIL_READONLY,
            GMAIL_SEND,
        )
        from packages.database.connector_models import TenantConnector

        async with session_scope() as db:
            rows = list(
                (
                    await db.scalars(
                        select(TenantConnector).where(
                            TenantConnector.tenant_id == context.tenant_id,
                        )
                    )
                ).all()
            )

        by_provider: dict[str, list] = {}
        for row in rows:
            by_provider.setdefault(str(row.provider or "").lower(), []).append(row)

        def config_resolver(_tenant_id, definition):
            provider = str(definition.integration_provider or "").lower()
            if not provider:
                return {"configured": True, "healthy": None}
            candidates = by_provider.get(provider, [])
            if provider == "discord":
                return {"configured": True, "healthy": None}
            if not candidates:
                return {
                    "configured": False,
                    "healthy": None,
                    "missing_connector": provider,
                    "reason": "connector_missing",
                    "next_action": f"Connect {provider.title()} to this workspace.",
                    "retryable": False,
                }

            connected = [row for row in candidates if row.enabled and row.status == "connected"]
            if not connected:
                disabled = any(not row.enabled for row in candidates)
                reason = "connector_disabled" if disabled else "connector_disconnected"
                return {
                    "configured": False,
                    "healthy": False if any(row.status == "error" for row in candidates) else None,
                    "missing_connector": provider,
                    "reason": reason,
                    "next_action": "Enable or reconnect the integration for this workspace.",
                    "retryable": reason == "connector_disconnected",
                }

            union_scopes: set[str] = set()
            for row in connected:
                try:
                    union_scopes.update(json.loads(row.granted_scopes_json or "[]"))
                except (TypeError, json.JSONDecodeError):
                    pass

            required = set(definition.credential_scopes or ())
            satisfied = not required or required.issubset(union_scopes)
            if definition.id in {"messaging.send", "gmail.send_email"}:
                satisfied = bool(union_scopes & {GMAIL_SEND, GMAIL_MODIFY})
                required = {GMAIL_SEND}
            elif definition.id in {
                "gmail.search",
                "gmail.read_message",
                "gmail.read_thread",
                "gmail.list_attachments",
                "gmail.read_attachment",
            }:
                satisfied = bool(union_scopes & {GMAIL_READONLY, GMAIL_MODIFY})
                required = {GMAIL_READONLY}
            missing_scopes = [] if satisfied else sorted(required - union_scopes or required)

            health_values = {str(row.health_status or "unknown").lower() for row in connected}
            healthy = False if health_values & {"error", "failed", "unhealthy"} else True if health_values & {"healthy", "ok"} else None
            last_error = next((str(row.last_error)[:300] for row in connected if row.last_error), None)
            if healthy is False:
                return {
                    "configured": True,
                    "healthy": False,
                    "missing_scopes": missing_scopes,
                    "reason": "provider_unhealthy",
                    "next_action": "Retry after the provider recovers or reconnect the integration.",
                    "retryable": True,
                    "provider_error": last_error,
                }
            if missing_scopes:
                return {
                    "configured": False,
                    "healthy": healthy,
                    "missing_scopes": missing_scopes,
                    "reason": "oauth_scope_missing",
                    "next_action": "Reconnect the integration and grant the required OAuth scope.",
                    "retryable": False,
                }
            return {"configured": True, "healthy": healthy, "retryable": False}

        # Keep known capabilities registered even when their connector is missing.
        # Availability explains connector/scope/health state; progressive exposure
        # still prevents unusable capabilities from cluttering the model surface.
        return default_registry(None, config_resolver=config_resolver)

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

    async def preflight(
        self,
        context: PluginInvocationContext,
        *,
        limit: int = 6,
    ) -> list[dict[str, Any]]:
        """Bounded discovery owned by the harness, never by model initiative."""
        authority = await self.authority_for(context)
        if not authority:
            return []
        registry = await self.registry_for(context)
        view = await self.session_view_for(context, authority=authority, registry=registry)
        key = self._session_key(context)
        objective = str(context.objective or "").strip()
        if self._preflight_objectives.get(key) == objective:
            return list(self._preflight_diagnostics.get(key, ()))

        rows = registry.search(
            context.tenant_id,
            objective,
            authority=authority,
            limit=max(1, min(int(limit), 10)),
        )
        expose_ids = []
        diagnostics = []
        for row in rows:
            availability = row.get("availability") or {}
            diagnostics.append(
                {
                    "id": row.get("id"),
                    "availability": availability,
                }
            )
            if availability.get("available") is True and row.get("authorized") is not False:
                expose_ids.append(str(row.get("id") or ""))
        view.expose(expose_ids)
        self._preflight_objectives[key] = objective
        self._preflight_diagnostics[key] = diagnostics
        return list(diagnostics)

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
        await self.preflight(context)
        stage = str(context.metadata.get("capability_stage") or "adaptive")
        return view.schemas(stage=stage)

    async def availability(
        self,
        name: str,
        context: PluginInvocationContext,
    ) -> dict[str, Any]:
        registry = await self.registry_for(context)
        authority = await self.authority_for(context)
        try:
            definition = registry.definition(name)
        except LookupError:
            return {
                "available": False,
                "configured": False,
                "healthy": None,
                "missingScopes": [],
                "missingConnector": None,
                "permissionDenied": False,
                "surfaceHidden": False,
                "exposed": False,
                "retryable": False,
                "nextAction": "Use capability.search to discover an installed operation.",
                "reason": "not_registered",
            }
        payload = registry.availability(
            context.tenant_id,
            definition.id,
            authority=authority,
        ).as_dict()
        surface_allowed = self.capability_authorized(definition.id, authority, context)
        payload["surfaceHidden"] = not surface_allowed and not payload.get("permissionDenied")
        if payload["surfaceHidden"]:
            payload["available"] = False
            payload["reason"] = "surface_hidden"
            payload["nextAction"] = "Use this capability from an allowed private/workspace surface."
        try:
            view = await self.session_view_for(context, authority=authority, registry=registry)
            payload["exposed"] = definition.id in view.exposed_ids and view._visible(definition.id)
        except Exception:
            payload["exposed"] = False
        return payload

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
            return {
                "ok": False,
                "error": "Unknown or unauthorized plugin",
                "availability": {
                    "available": False,
                    "configured": True,
                    "healthy": None,
                    "missingScopes": [],
                    "missingConnector": None,
                    "permissionDenied": True,
                    "surfaceHidden": False,
                    "exposed": False,
                    "retryable": False,
                    "nextAction": "Use an authenticated workspace context with sufficient permission.",
                    "reason": "permission_denied",
                },
            }
        authority = set(execution.permissions)
        if not authority or not self.capability_authorized(name, authority, context):
            return {
                "ok": False,
                "error": "Unknown or unauthorized plugin",
                "availability": await self.availability(name, context),
            }

        registry = await self.registry_for(context)
        view = await self.session_view_for(
            context,
            authority=authority,
            registry=registry,
        )
        if name not in view.exposed_ids or not view._visible(name):
            payload = await self.availability(name, context)
            payload["available"] = False
            payload["exposed"] = False
            if payload.get("reason") in {None, "available"}:
                payload["reason"] = "not_exposed"
            payload["nextAction"] = payload.get("nextAction") or "Discover/describe this capability in the current agent session."
            return {
                "ok": False,
                "error": "Capability is not exposed in this model session; discover and describe it first",
                "availability": payload,
            }

        try:
            definition = registry.definition(name)
        except LookupError:
            return {
                "ok": False,
                "error": "Unknown or unauthorized plugin",
                "availability": await self.availability(name, context),
            }

        clean_arguments = dict(arguments)
        rationale = str(
            clean_arguments.pop("_rationale", "")
            or f"Model selected {name} for the current objective"
        )[:2000]
        expected = str(
            clean_arguments.pop("_expected_outcome", "") or definition.description
        )[:2000]

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
        payload.setdefault("availability", await self.availability(name, context))
        view.observe(name, payload)
        return payload

    async def run_session(
        self,
        client,
        messages: list[dict[str, Any]],
        context: PluginInvocationContext,
        max_steps: int = 8,
    ):
        async def schemas():
            return await self.schemas(context)

        async def invoke(name: str, arguments: dict[str, Any], call_id: str | None):
            return await self.invoke(name, arguments, context, call_id=call_id)

        inference_metadata = {
            **dict(context.metadata),
            "tenant_id": context.tenant_id,
            "user_id": context.user_id,
            "channel": context.channel,
            "conversation_id": str(context.metadata.get("_conversation_id") or "") or None,
            "capability_stage": str(context.metadata.get("capability_stage") or "adaptive"),
        }
        return await AgentRuntime(max_steps=max_steps).run(
            model=client,
            messages=messages,
            schemas=schemas,
            invoke=invoke,
            inference_metadata=inference_metadata,
        )
