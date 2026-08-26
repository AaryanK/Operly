import json
from dataclasses import dataclass, field
from typing import Any

from packages.agents import AgentRunController
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
from packages.security.surfaces import (
    SurfaceKind,
    capability_surface_allowed,
    surface_from_legacy_metadata,
)


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

_DISCORD_CURRENT_CONTEXT = {
    "discord.context",
    "discord.read_recent_messages",
    "discord.send_message",
    "discord.add_reaction",
    "discord.create_thread",
}

# Keep boot exposure deliberately tiny. Everything else must be discovered and
# described before its exact schema reaches the model.
_DEFAULT_INITIAL_CAPABILITIES = frozenset({"runtime.context"})


@dataclass(slots=True)
class PluginInvocationContext:
    tenant_id: str
    user_id: str | None
    role: str
    objective: str
    channel: str = "web"
    metadata: dict[str, Any] = field(default_factory=dict)
    surface: SurfaceKind | str | None = None
    # A principal is broader than an AppUser: guest:<id>, user:<id>, service:<id>, ...
    # Existing callers may omit it and continue using user_id.
    principal_id: str | None = None

    def __post_init__(self) -> None:
        explicit = SurfaceKind.coerce(self.surface)
        self.surface = (
            explicit
            if explicit is not SurfaceKind.UNKNOWN
            else surface_from_legacy_metadata(self.channel, self.metadata)
        )
        if not self.principal_id and self.user_id:
            self.principal_id = f"user:{self.user_id}"


class PluginAgentHarness:
    """Agent-facing view over the one canonical capability invocation boundary.

    Capability visibility is session-scoped and progressive. Authorization/surface
    policy define the eligible universe, semantic discovery determines relevance,
    and exact schemas appear only after describe. Full members and Guest Workspace
    principals use the same registry/firewall; only their ExecutionContext differs.
    """

    def __init__(self, registry=None):
        self.registry = registry
        self._session_views: dict[str, SessionCapabilityView] = {}

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
            # Discord is the current channel capability provider and is not represented
            # by TenantConnector for a provisional Guest Workspace.
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

            connected = [
                row for row in candidates if row.enabled and row.status == "connected"
            ]
            if not connected:
                disabled = any(not row.enabled for row in candidates)
                reason = "connector_disabled" if disabled else "connector_disconnected"
                return {
                    "configured": False,
                    "healthy": (
                        False if any(row.status == "error" for row in candidates) else None
                    ),
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

            health_values = {
                str(row.health_status or "unknown").lower() for row in connected
            }
            healthy = (
                False
                if health_values & {"error", "failed", "unhealthy"}
                else True
                if health_values & {"healthy", "ok"}
                else None
            )
            last_error = next(
                (str(row.last_error)[:300] for row in connected if row.last_error),
                None,
            )
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

        return default_registry(None, config_resolver=config_resolver)

    def authority(self, role: str) -> set[str]:
        return default_permissions(role)

    async def execution_context_for(
        self,
        context: PluginInvocationContext,
    ) -> ExecutionContext | None:
        metadata = dict(context.metadata)
        if context.principal_id:
            metadata["principal_id"] = context.principal_id
            if context.principal_id.startswith("guest:"):
                metadata["_guest_principal_id"] = context.principal_id
        try:
            async with session_scope() as db:
                return await resolve_execution_context(
                    db,
                    workspace_id=context.tenant_id,
                    user_id=context.user_id,
                    channel=context.channel,
                    surface=context.surface,
                    conversation_id=(
                        str(context.metadata.get("_conversation_id") or "") or None
                    ),
                    metadata=metadata,
                    require_membership=True,
                )
        except ExecutionContextError:
            return None

    async def authority_for(self, context: PluginInvocationContext) -> set[str]:
        execution = await self.execution_context_for(context)
        return set(execution.permissions) if execution else set()

    @staticmethod
    def _is_private_surface(context: PluginInvocationContext) -> bool:
        return SurfaceKind.coerce(context.surface).allows_personal_global

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
        if not capability_surface_allowed(
            capability_id,
            SurfaceKind.coerce(context.surface),
        ):
            return False
        if capability_id in _DISCORD_CURRENT_CONTEXT and context.channel != "discord":
            return False
        return True

    @staticmethod
    def _session_key(context: PluginInvocationContext) -> str:
        conversation = str(context.metadata.get("_conversation_id") or "").strip()
        principal = str(context.principal_id or (f"user:{context.user_id}" if context.user_id else "anonymous"))
        surface = SurfaceKind.coerce(context.surface).value
        return ":".join(
            (
                context.tenant_id,
                principal,
                context.channel,
                surface,
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
        del context, limit
        return []

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
        surface_allowed = self.capability_authorized(
            definition.id,
            authority,
            context,
        )
        payload["surfaceHidden"] = not surface_allowed and not payload.get("permissionDenied")
        if payload["surfaceHidden"]:
            payload["available"] = False
            payload["reason"] = "surface_hidden"
            payload["nextAction"] = "Use this capability from an allowed private/workspace surface."
        try:
            view = await self.session_view_for(
                context,
                authority=authority,
                registry=registry,
            )
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
            payload["nextAction"] = payload.get("nextAction") or (
                "Discover/describe this capability in the current agent session."
            )
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
        runtime_metadata["_surface_kind"] = execution.surface.value
        runtime_metadata["surface"] = execution.surface.value
        runtime_metadata["principal_id"] = execution.principal_id or context.principal_id
        # This boundary knows the difference between the person who requested work and
        # the Operly agent that selected/executed a capability. Action/event layers can
        # persist this chain without asking the model to label itself.
        runtime_metadata.setdefault(
            "initiator_type",
            "user" if context.user_id else "guest",
        )
        runtime_metadata.setdefault(
            "initiator_id",
            context.principal_id or execution.principal_id,
        )
        runtime_metadata["executor_type"] = "agent"
        runtime_metadata["executor_id"] = "operly:business_agent"
        runtime_metadata["delegation_chain"] = [
            {
                "from": runtime_metadata.get("initiator_id"),
                "to": "operly:business_agent",
                "kind": "requested_action",
            }
        ]
        if name in {"capability.search", "capability.describe", "context.search", "context.get"}:
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

        async def invoke(name: str, arguments: dict, call_id: str | None):
            return await self.invoke(name, arguments, context, call_id=call_id)

        inference_metadata = {
            **dict(context.metadata),
            "tenant_id": context.tenant_id,
            "user_id": context.user_id,
            "principal_id": context.principal_id,
            "channel": context.channel,
            "surface": SurfaceKind.coerce(context.surface).value,
            "conversation_id": str(context.metadata.get("_conversation_id") or "") or None,
            "capability_stage": str(context.metadata.get("capability_stage") or "adaptive"),
        }
        return await AgentRunController(max_replans=1).run(
            objective=context.objective,
            model=client,
            messages=messages,
            schemas=schemas,
            invoke=invoke,
            max_steps=max_steps,
            inference_metadata=inference_metadata,
        )
