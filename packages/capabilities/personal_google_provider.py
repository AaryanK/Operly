from __future__ import annotations

import json

from sqlalchemy import select

from packages.capabilities.calendar_semantics_provider import CalendarSemanticsProvider
from packages.capabilities.contracts import CapabilityResult
from packages.capabilities.gmail_draft_provider import GmailDraftLifecycleProvider
from packages.capabilities.gmail_read_provider import GmailReadProvider
from packages.capabilities.providers import BaseProvider
from packages.capabilities.registry import CapabilityRegistry
from packages.connectors.google_provider import (
    GMAIL_MODIFY,
    GMAIL_READONLY,
    GMAIL_SEND,
    GmailProvider,
    GoogleCalendarProvider,
)
from packages.database.account_connector_models import AccountConnector


# Personal scope deliberately excludes workspace/business operations such as
# messaging.send, which dereferences a workspace Lead. The same canonical Gmail and
# Calendar provider implementations own the operations below once the call crosses
# the Personal firewall.
PERSONAL_GOOGLE_CAPABILITY_IDS = frozenset(
    {
        "gmail.send_email",
        "gmail.search",
        "gmail.read_message",
        "gmail.modify_labels",
        "gmail.create_draft",
        "gmail.read_thread",
        "gmail.list_attachments",
        "gmail.read_attachment",
        "gmail.list_drafts",
        "gmail.get_draft",
        "gmail.update_draft",
        "gmail.send_draft",
        "gmail.delete_draft",
        "calendar.create_event",
        "calendar.list_events",
        "calendar.freebusy",
        "calendar.list_calendars",
        "calendar.update_event",
        "calendar.delete_event",
        "calendar.assess_deadline_conflicts",
    }
)


def _underlying_providers():
    return (
        GmailProvider(),
        GmailReadProvider(),
        GmailDraftLifecycleProvider(),
        GoogleCalendarProvider(),
        CalendarSemanticsProvider(),
    )


class PersonalGoogleCapabilityProvider(BaseProvider):
    """Model-visible Personal catalog whose execution always crosses the firewall."""

    name = "personal_google_catalog"
    _providers = _underlying_providers()
    capabilities = tuple(
        definition
        for provider in _providers
        for definition in provider.capabilities
        if definition.id in PERSONAL_GOOGLE_CAPABILITY_IDS
    )

    def supports(self, capability_name: str) -> bool:
        return any(
            definition.id == capability_name or definition.name == capability_name
            for definition in self.capabilities
        )

    async def execute(self, context, capability_name, arguments):
        # PersonalAgentService intercepts this provider and invokes the canonical
        # ActionBackedCapabilityFirewall. Reaching this method means a caller tried to
        # bypass that bridge, so fail closed rather than invoking Google directly.
        return CapabilityResult(
            False,
            False,
            {"reason": "personal_google_requires_governed_firewall"},
        )

    async def verify(self, context, capability_name, arguments, result):
        return result

    async def registry_for(self, db, *, user_id: str) -> CapabilityRegistry:
        rows = list(
            (
                await db.scalars(
                    select(AccountConnector)
                    .where(
                        AccountConnector.user_id == user_id,
                        AccountConnector.provider == "google",
                    )
                    .order_by(AccountConnector.created_at)
                )
            ).all()
        )

        connected = [row for row in rows if row.enabled and row.status == "connected"]
        union_scopes: set[str] = set()
        for row in connected:
            try:
                union_scopes.update(json.loads(row.granted_scopes_json or "[]"))
            except (TypeError, json.JSONDecodeError):
                pass

        def enabled_resolver(_scope_id, definition):
            return definition.id in PERSONAL_GOOGLE_CAPABILITY_IDS

        def config_resolver(_scope_id, definition):
            if definition.id not in PERSONAL_GOOGLE_CAPABILITY_IDS:
                return {
                    "configured": False,
                    "healthy": None,
                    "reason": "personal_scope_unsupported",
                    "retryable": False,
                }
            if not rows:
                return {
                    "configured": False,
                    "healthy": None,
                    "missing_connector": "google",
                    "reason": "connector_missing",
                    "next_action": "Connect Google to Personal AI.",
                    "retryable": False,
                }
            if not connected:
                disabled = any(not row.enabled for row in rows)
                return {
                    "configured": False,
                    "healthy": None,
                    "missing_connector": "google",
                    "reason": "connector_disabled" if disabled else "connector_disconnected",
                    "next_action": "Enable or reconnect your Personal Google account.",
                    "retryable": not disabled,
                }

            required = set(definition.credential_scopes or ())
            satisfied = not required or required.issubset(union_scopes)
            if definition.id in {"gmail.send_email"}:
                required = {GMAIL_SEND}
                satisfied = bool(union_scopes & {GMAIL_SEND, GMAIL_MODIFY})
            elif definition.id in {
                "gmail.search",
                "gmail.read_message",
                "gmail.read_thread",
                "gmail.list_attachments",
                "gmail.read_attachment",
            }:
                required = {GMAIL_READONLY}
                satisfied = bool(union_scopes & {GMAIL_READONLY, GMAIL_MODIFY})

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
                    "next_action": "Retry after Google recovers or reconnect your Personal Google account.",
                    "retryable": True,
                    "provider_error": last_error,
                }
            if missing_scopes:
                return {
                    "configured": False,
                    "healthy": healthy,
                    "missing_scopes": missing_scopes,
                    "reason": "oauth_scope_missing",
                    "next_action": "Reconnect Personal Google and grant the required permission tier.",
                    "retryable": False,
                }
            return {"configured": True, "healthy": healthy, "retryable": False}

        registry = CapabilityRegistry(
            enabled_resolver=enabled_resolver,
            config_resolver=config_resolver,
        )
        for provider in self._providers:
            registry.register(provider)
        return registry
