from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, unquote

from sqlalchemy import select

from packages.capabilities.firewall import ActionBackedCapabilityFirewall, CapabilityInvocation
from packages.capabilities.registry import CapabilityRegistry
from packages.connectors.google_provider import CALENDAR, GMAIL_READ_SCOPES
from packages.connectors.google_scope import connector_scopes
from packages.context.broker import ContextRef
from packages.context.provider_history import (
    PersonalGmailHistoryProvider,
    PersonalGoogleCalendarHistoryProvider,
)
from packages.database.account_connector_models import AccountConnector
from packages.security.execution_context import resolve_personal_execution_context
from packages.security.surfaces import SurfaceKind


@dataclass(frozen=True, slots=True)
class ProviderHistoryHit:
    ref: ContextRef
    text: str


class ProviderHistoryAdapter(ABC):
    """Standard extension contract for provider-owned history.

    Adapters discover only durable account-owned connectors, search through governed
    capability invocations, return opaque locator refs, and reauthorize/materialize
    those refs through the same capability boundary. Discovery never grants authority.
    """

    id: str
    source: str
    required_permissions: frozenset[str] = frozenset()

    def eligible(self, *, surface: SurfaceKind, authority: set[str], user_id: str | None) -> bool:
        return bool(
            user_id
            and surface.allows_personal_global
            and self.required_permissions.issubset(authority)
        )

    @abstractmethod
    def matches_ref(self, ref: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def search(
        self,
        runtime_context,
        *,
        user_id: str,
        surface: SurfaceKind,
        conversation_id: str | None,
        query: str,
        limit: int,
    ) -> list[ProviderHistoryHit]:
        raise NotImplementedError

    @abstractmethod
    async def materialize(
        self,
        runtime_context,
        *,
        user_id: str,
        surface: SurfaceKind,
        conversation_id: str | None,
        refs: list[str],
    ) -> dict[str, dict]:
        raise NotImplementedError


class ProviderHistoryRegistry:
    """Registry of external-history adapters consumed by FederatedHistoryService."""

    def __init__(self):
        self._adapters: dict[str, ProviderHistoryAdapter] = {}

    def register(self, adapter: ProviderHistoryAdapter, *, replace: bool = False) -> None:
        adapter_id = str(adapter.id or "").strip()
        if not adapter_id:
            raise ValueError("Provider history adapter id is required")
        if adapter_id in self._adapters and not replace:
            raise ValueError(f"Provider history adapter already registered: {adapter_id}")
        self._adapters[adapter_id] = adapter

    def get(self, adapter_id: str) -> ProviderHistoryAdapter | None:
        return self._adapters.get(str(adapter_id or "").strip())

    def all(self) -> tuple[ProviderHistoryAdapter, ...]:
        return tuple(self._adapters[key] for key in sorted(self._adapters))

    def eligible(
        self,
        *,
        surface: SurfaceKind,
        authority: set[str],
        user_id: str | None,
    ) -> tuple[ProviderHistoryAdapter, ...]:
        return tuple(
            adapter
            for adapter in self.all()
            if adapter.eligible(surface=surface, authority=authority, user_id=user_id)
        )

    def for_ref(
        self,
        ref: str,
        *,
        surface: SurfaceKind,
        authority: set[str],
        user_id: str | None,
    ) -> ProviderHistoryAdapter | None:
        for adapter in self.eligible(surface=surface, authority=authority, user_id=user_id):
            if adapter.matches_ref(ref):
                return adapter
        return None


class _PersonalAccountCapabilityHistoryAdapter(ProviderHistoryAdapter):
    provider_name: str
    connector_type: str | None = None
    ref_prefix: str

    def __init__(self, capability_provider):
        registry = CapabilityRegistry()
        registry.register(capability_provider)
        self.firewall = ActionBackedCapabilityFirewall(registry)

    @staticmethod
    def _runtime_metadata(runtime_context, surface: SurfaceKind) -> tuple[str, dict]:
        invocation = dict(getattr(runtime_context, "invocation", None) or {})
        metadata = dict(invocation.get("metadata") or {})
        metadata.setdefault("_surface_kind", surface.value)
        metadata.setdefault("surface", surface.value)
        metadata.setdefault("executor_type", "agent")
        metadata.setdefault("executor_id", "operly:federated_history")
        metadata.setdefault("mediation_mode", "ai")
        channel = str(invocation.get("channel") or metadata.get("origin_provider") or "operly")
        return channel, metadata

    @classmethod
    async def _personal_execution(
        cls,
        runtime_context,
        *,
        user_id: str,
        surface: SurfaceKind,
        conversation_id: str | None,
    ):
        channel, metadata = cls._runtime_metadata(runtime_context, surface)
        focus_workspace_id = str(
            metadata.get("focus_workspace_id")
            or metadata.get("selected_workspace_id")
            or ""
        ).strip() or None
        return await resolve_personal_execution_context(
            runtime_context.db,
            user_id=user_id,
            channel=channel,
            surface=surface,
            conversation_id=conversation_id,
            metadata=metadata,
            focus_workspace_id=focus_workspace_id,
        )

    async def _connectors(self, db, *, user_id: str) -> list[AccountConnector]:
        query = (
            select(AccountConnector)
            .where(
                AccountConnector.user_id == user_id,
                AccountConnector.provider == self.provider_name,
                AccountConnector.enabled.is_(True),
                AccountConnector.status == "connected",
            )
            .order_by(AccountConnector.created_at)
        )
        if self.connector_type:
            query = query.where(AccountConnector.connector_type == self.connector_type)
        rows = list((await db.scalars(query)).all())
        return [row for row in rows if self._connector_has_authority(row)]

    @abstractmethod
    def _connector_has_authority(self, connector: AccountConnector) -> bool:
        raise NotImplementedError

    def matches_ref(self, ref: str) -> bool:
        return str(ref or "").startswith(f"{self.ref_prefix}:")

    @staticmethod
    def _invoke_payload(
        *,
        capability_id: str,
        arguments: dict,
        query: str,
        channel: str,
        metadata: dict,
        materialize: bool = False,
    ) -> CapabilityInvocation:
        return CapabilityInvocation(
            capability_id=capability_id,
            arguments=arguments,
            objective=(
                "Materialize an explicitly selected authorized provider history reference"
                if materialize
                else f"Retrieve authorized history relevant to: {query}"
            ),
            rationale=(
                "context.get requested this federated provider reference"
                if materialize
                else "Federated context search across an explicitly owned provider account"
            ),
            expected_outcome=(
                "One provider record from the same account that produced the reference"
                if materialize
                else "Compact provider history references only"
            ),
            channel=channel,
            metadata=metadata,
        )


class GmailHistoryAdapter(_PersonalAccountCapabilityHistoryAdapter):
    id = "google.gmail"
    source = "gmail"
    provider_name = "google"
    connector_type = "google_workspace"
    ref_prefix = "gmail_message"
    required_permissions = frozenset({"messaging:read"})

    def __init__(self):
        super().__init__(PersonalGmailHistoryProvider())

    def _connector_has_authority(self, connector: AccountConnector) -> bool:
        return bool(connector_scopes(connector) & GMAIL_READ_SCOPES)

    @staticmethod
    def _ref(connector_id: str, message_id: str) -> str:
        return f"gmail_message:{connector_id}:{message_id}"

    @staticmethod
    def _parse_ref(value: str) -> tuple[str, str] | None:
        if not value.startswith("gmail_message:"):
            return None
        parts = value.split(":", 2)
        if len(parts) != 3 or not parts[1] or not parts[2]:
            return None
        return parts[1], parts[2]

    async def search(
        self,
        runtime_context,
        *,
        user_id: str,
        surface: SurfaceKind,
        conversation_id: str | None,
        query: str,
        limit: int,
    ) -> list[ProviderHistoryHit]:
        if not query.strip():
            return []
        connectors = await self._connectors(runtime_context.db, user_id=user_id)
        if not connectors:
            return []
        execution = await self._personal_execution(
            runtime_context,
            user_id=user_id,
            surface=surface,
            conversation_id=conversation_id,
        )
        channel, metadata = self._runtime_metadata(runtime_context, surface)
        per_account = max(1, min(10, max(3, int(limit))))

        async def search_one(connector: AccountConnector):
            try:
                result = await self.firewall.invoke(
                    self._invoke_payload(
                        capability_id="history.gmail.search_account",
                        arguments={
                            "connector_id": connector.id,
                            "query": query,
                            "limit": per_account,
                        },
                        query=query,
                        channel=channel,
                        metadata=metadata,
                    ),
                    execution,
                )
            except Exception:
                return []
            if not result.ok:
                return []
            observation = dict(result.observation or {})
            account_name = str(
                observation.get("account_display_name")
                or connector.display_name
                or connector.provider_account_id
                or "Google account"
            )
            output: list[ProviderHistoryHit] = []
            for message in observation.get("messages") or []:
                message_id = str(message.get("id") or "").strip()
                if not message_id:
                    continue
                subject = str(message.get("subject") or "(no subject)")
                sender = str(message.get("from") or "")
                snippet = " ".join(str(message.get("snippet") or "").split())[:500]
                description = f"Gmail · {account_name} · {subject}"
                if sender:
                    description += f" · from {sender}"
                if snippet:
                    description += f" — {snippet[:180]}"
                ref = ContextRef(
                    id=self._ref(connector.id, message_id),
                    source=self.source,
                    scope=f"provider:google:{connector.id}",
                    visibility="private",
                    kind="email",
                    description=description,
                    estimated_tokens=max(1, (len(snippet) + len(subject) + 2) // 3),
                )
                output.append(
                    ProviderHistoryHit(
                        ref=ref,
                        text=(
                            f"source:gmail account:{account_name} from:{sender} "
                            f"subject:{subject} date:{message.get('date') or ''}\n{snippet}"
                        ),
                    )
                )
            return output

        groups = await asyncio.gather(*(search_one(connector) for connector in connectors))
        return [item for group in groups for item in group]

    async def materialize(
        self,
        runtime_context,
        *,
        user_id: str,
        surface: SurfaceKind,
        conversation_id: str | None,
        refs: list[str],
    ) -> dict[str, dict]:
        parsed = [(ref, self._parse_ref(ref)) for ref in refs]
        requested = [(ref, value[0], value[1]) for ref, value in parsed if value]
        if not requested:
            return {}
        authorized = {
            connector.id: connector
            for connector in await self._connectors(runtime_context.db, user_id=user_id)
        }
        if not authorized:
            return {}
        execution = await self._personal_execution(
            runtime_context,
            user_id=user_id,
            surface=surface,
            conversation_id=conversation_id,
        )
        channel, metadata = self._runtime_metadata(runtime_context, surface)

        async def read_one(original_ref: str, connector_id: str, message_id: str):
            if connector_id not in authorized:
                return original_ref, None
            try:
                result = await self.firewall.invoke(
                    self._invoke_payload(
                        capability_id="history.gmail.read_account_message",
                        arguments={"connector_id": connector_id, "message_id": message_id},
                        query="",
                        channel=channel,
                        metadata=metadata,
                        materialize=True,
                    ),
                    execution,
                )
            except Exception:
                return original_ref, None
            if not result.ok:
                return original_ref, None
            observation = dict(result.observation or {})
            text = str(observation.get("text_body") or observation.get("snippet") or "")
            rich = str(observation.get("html_body") or "")
            return original_ref, {
                "ref": original_ref,
                "source": self.source,
                "scope": f"provider:google:{connector_id}",
                "visibility": "private",
                "kind": "email",
                "connector_id": connector_id,
                "provider_account_id": observation.get("provider_account_id"),
                "account_display_name": observation.get("account_display_name"),
                "message_id": observation.get("id"),
                "thread_id": observation.get("thread_id"),
                "from": observation.get("from"),
                "to": observation.get("to"),
                "cc": observation.get("cc"),
                "subject": observation.get("subject"),
                "date": observation.get("date"),
                "snippet": observation.get("snippet"),
                "text_body": text,
                "html_body": rich,
                "label_ids": observation.get("label_ids") or [],
                "estimated_tokens": max(1, (len(text) + len(rich) + 2) // 3),
            }

        rows = await asyncio.gather(
            *(read_one(original_ref, connector_id, message_id) for original_ref, connector_id, message_id in requested)
        )
        return {ref: payload for ref, payload in rows if payload is not None}


class GoogleCalendarHistoryAdapter(_PersonalAccountCapabilityHistoryAdapter):
    id = "google.calendar"
    source = "google_calendar"
    provider_name = "google"
    connector_type = "google_workspace"
    ref_prefix = "google_calendar_event"
    required_permissions = frozenset({"calendar:read"})

    def __init__(self):
        super().__init__(PersonalGoogleCalendarHistoryProvider())

    def _connector_has_authority(self, connector: AccountConnector) -> bool:
        return CALENDAR in connector_scopes(connector)

    @staticmethod
    def _ref(connector_id: str, calendar_id: str, event_id: str) -> str:
        return (
            f"google_calendar_event:{connector_id}:"
            f"{quote(calendar_id, safe='')}:{quote(event_id, safe='')}"
        )

    @staticmethod
    def _parse_ref(value: str) -> tuple[str, str, str] | None:
        if not value.startswith("google_calendar_event:"):
            return None
        parts = value.split(":", 3)
        if len(parts) != 4 or not all(parts[1:]):
            return None
        return parts[1], unquote(parts[2]), unquote(parts[3])

    @staticmethod
    def _history_window() -> tuple[str, str]:
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=3650)
        end = now + timedelta(days=1095)
        return (
            start.isoformat().replace("+00:00", "Z"),
            end.isoformat().replace("+00:00", "Z"),
        )

    async def search(
        self,
        runtime_context,
        *,
        user_id: str,
        surface: SurfaceKind,
        conversation_id: str | None,
        query: str,
        limit: int,
    ) -> list[ProviderHistoryHit]:
        if not query.strip():
            return []
        connectors = await self._connectors(runtime_context.db, user_id=user_id)
        if not connectors:
            return []
        execution = await self._personal_execution(
            runtime_context,
            user_id=user_id,
            surface=surface,
            conversation_id=conversation_id,
        )
        channel, metadata = self._runtime_metadata(runtime_context, surface)
        time_min, time_max = self._history_window()
        per_account = max(1, min(20, max(5, int(limit))))

        async def search_one(connector: AccountConnector):
            try:
                result = await self.firewall.invoke(
                    self._invoke_payload(
                        capability_id="history.calendar.search_account",
                        arguments={
                            "connector_id": connector.id,
                            "query": query,
                            "limit": per_account,
                            "time_min": time_min,
                            "time_max": time_max,
                        },
                        query=query,
                        channel=channel,
                        metadata=metadata,
                    ),
                    execution,
                )
            except Exception:
                return []
            if not result.ok:
                return []
            observation = dict(result.observation or {})
            account_name = str(
                observation.get("account_display_name")
                or connector.display_name
                or connector.provider_account_id
                or "Google account"
            )
            calendar_id = str(observation.get("calendar_id") or "primary")
            output: list[ProviderHistoryHit] = []
            for event in observation.get("events") or []:
                event_id = str(event.get("id") or "").strip()
                if not event_id:
                    continue
                summary = str(event.get("summary") or "(untitled event)")
                description_text = " ".join(str(event.get("description") or "").split())[:400]
                location = str(event.get("location") or "")
                start = event.get("start") or {}
                start_value = str(start.get("dateTime") or start.get("date") or "")
                description = f"Calendar · {account_name} · {summary}"
                if start_value:
                    description += f" · {start_value}"
                if location:
                    description += f" · {location}"
                if description_text:
                    description += f" — {description_text[:180]}"
                ref = ContextRef(
                    id=self._ref(connector.id, calendar_id, event_id),
                    source=self.source,
                    scope=f"provider:google:{connector.id}",
                    visibility="private",
                    kind="calendar_event",
                    description=description,
                    estimated_tokens=max(1, (len(summary) + len(description_text) + 2) // 3),
                )
                output.append(
                    ProviderHistoryHit(
                        ref=ref,
                        text=(
                            f"source:google_calendar account:{account_name} summary:{summary} "
                            f"start:{start_value} location:{location}\n{description_text}"
                        ),
                    )
                )
            return output

        groups = await asyncio.gather(*(search_one(connector) for connector in connectors))
        return [item for group in groups for item in group]

    async def materialize(
        self,
        runtime_context,
        *,
        user_id: str,
        surface: SurfaceKind,
        conversation_id: str | None,
        refs: list[str],
    ) -> dict[str, dict]:
        parsed = [(ref, self._parse_ref(ref)) for ref in refs]
        requested = [(ref, value[0], value[1], value[2]) for ref, value in parsed if value]
        if not requested:
            return {}
        authorized = {
            connector.id: connector
            for connector in await self._connectors(runtime_context.db, user_id=user_id)
        }
        if not authorized:
            return {}
        execution = await self._personal_execution(
            runtime_context,
            user_id=user_id,
            surface=surface,
            conversation_id=conversation_id,
        )
        channel, metadata = self._runtime_metadata(runtime_context, surface)

        async def read_one(original_ref: str, connector_id: str, calendar_id: str, event_id: str):
            if connector_id not in authorized:
                return original_ref, None
            try:
                result = await self.firewall.invoke(
                    self._invoke_payload(
                        capability_id="history.calendar.read_account_event",
                        arguments={
                            "connector_id": connector_id,
                            "calendar_id": calendar_id,
                            "event_id": event_id,
                        },
                        query="",
                        channel=channel,
                        metadata=metadata,
                        materialize=True,
                    ),
                    execution,
                )
            except Exception:
                return original_ref, None
            if not result.ok:
                return original_ref, None
            observation = dict(result.observation or {})
            description_text = str(observation.get("description") or "")
            return original_ref, {
                "ref": original_ref,
                "source": self.source,
                "scope": f"provider:google:{connector_id}",
                "visibility": "private",
                "kind": "calendar_event",
                "connector_id": connector_id,
                "provider_account_id": observation.get("provider_account_id"),
                "account_display_name": observation.get("account_display_name"),
                "calendar_id": observation.get("calendar_id"),
                "event_id": observation.get("id"),
                "summary": observation.get("summary"),
                "description": description_text,
                "location": observation.get("location"),
                "start": observation.get("start"),
                "end": observation.get("end"),
                "attendees": observation.get("attendees") or [],
                "status": observation.get("status"),
                "html_link": observation.get("html_link"),
                "hangout_link": observation.get("hangout_link"),
                "estimated_tokens": max(1, (len(description_text) + 2) // 3),
            }

        rows = await asyncio.gather(
            *(
                read_one(original_ref, connector_id, calendar_id, event_id)
                for original_ref, connector_id, calendar_id, event_id in requested
            )
        )
        return {ref: payload for ref, payload in rows if payload is not None}


_DEFAULT_PROVIDER_HISTORY_REGISTRY: ProviderHistoryRegistry | None = None


def default_provider_history_registry() -> ProviderHistoryRegistry:
    global _DEFAULT_PROVIDER_HISTORY_REGISTRY
    if _DEFAULT_PROVIDER_HISTORY_REGISTRY is None:
        registry = ProviderHistoryRegistry()
        registry.register(GmailHistoryAdapter())
        registry.register(GoogleCalendarHistoryAdapter())
        _DEFAULT_PROVIDER_HISTORY_REGISTRY = registry
    return _DEFAULT_PROVIDER_HISTORY_REGISTRY
