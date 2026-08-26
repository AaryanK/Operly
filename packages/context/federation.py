from __future__ import annotations

import asyncio
from dataclasses import dataclass

from sqlalchemy import select

from packages.capabilities.firewall import ActionBackedCapabilityFirewall, CapabilityInvocation
from packages.capabilities.registry import CapabilityRegistry
from packages.connectors.google_provider import GMAIL_READ_SCOPES
from packages.connectors.google_scope import connector_scopes
from packages.context.broker import ContextBroker, ContextRef
from packages.context.provider_history import PersonalGmailHistoryProvider
from packages.database.account_connector_models import AccountConnector
from packages.retrieval.semantic import SemanticDocument, SemanticTextIndex
from packages.security.execution_context import resolve_personal_execution_context
from packages.security.surfaces import SurfaceKind


@dataclass(frozen=True, slots=True)
class _FederatedRef:
    ref: ContextRef
    text: str


class FederatedHistoryService:
    """One authorized retrieval boundary across Operly and provider-owned history.

    ContextBroker remains the local source. External source adapters fan out only after
    the current human/surface has been resolved. Every provider adapter invokes its
    read through the canonical capability firewall and every materialization repeats
    the provider/account authorization check.
    """

    _ranker = SemanticTextIndex(max_cached_documents=10_000)
    _gmail_provider = PersonalGmailHistoryProvider()
    _gmail_registry = CapabilityRegistry()
    _gmail_registry.register(_gmail_provider)
    _gmail_firewall = ActionBackedCapabilityFirewall(_gmail_registry)

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

    @classmethod
    async def _gmail_connectors(cls, db, *, user_id: str) -> list[AccountConnector]:
        rows = list(
            (
                await db.scalars(
                    select(AccountConnector)
                    .where(
                        AccountConnector.user_id == user_id,
                        AccountConnector.provider == "google",
                        AccountConnector.enabled.is_(True),
                        AccountConnector.status == "connected",
                    )
                    .order_by(AccountConnector.created_at)
                )
            ).all()
        )
        return [row for row in rows if connector_scopes(row) & GMAIL_READ_SCOPES]

    @staticmethod
    def _gmail_ref(connector_id: str, message_id: str) -> str:
        return f"gmail_message:{connector_id}:{message_id}"

    @staticmethod
    def _parse_gmail_ref(value: str) -> tuple[str, str] | None:
        if not value.startswith("gmail_message:"):
            return None
        parts = value.split(":", 2)
        if len(parts) != 3 or not parts[1] or not parts[2]:
            return None
        return parts[1], parts[2]

    @classmethod
    async def _search_gmail(
        cls,
        runtime_context,
        *,
        user_id: str,
        surface: SurfaceKind,
        conversation_id: str | None,
        query: str,
        limit: int,
    ) -> list[_FederatedRef]:
        if not surface.allows_personal_global or not user_id or not query.strip():
            return []
        connectors = await cls._gmail_connectors(runtime_context.db, user_id=user_id)
        if not connectors:
            return []
        execution = await cls._personal_execution(
            runtime_context,
            user_id=user_id,
            surface=surface,
            conversation_id=conversation_id,
        )
        channel, metadata = cls._runtime_metadata(runtime_context, surface)
        per_account = max(1, min(10, max(3, int(limit))))

        async def search_one(connector: AccountConnector):
            try:
                result = await cls._gmail_firewall.invoke(
                    CapabilityInvocation(
                        capability_id="history.gmail.search_account",
                        arguments={
                            "connector_id": connector.id,
                            "query": query,
                            "limit": per_account,
                        },
                        objective=f"Retrieve authorized history relevant to: {query}",
                        rationale="Federated context search across an explicitly owned Gmail account",
                        expected_outcome="Compact Gmail references only",
                        channel=channel,
                        metadata=metadata,
                    ),
                    execution,
                )
            except Exception:
                # One unhealthy provider account must not erase authorized results from
                # other sources/accounts. The normal capability action still records a
                # provider failure when invocation reached the provider boundary.
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
            output: list[_FederatedRef] = []
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
                    id=cls._gmail_ref(connector.id, message_id),
                    source="gmail",
                    scope=f"provider:google:{connector.id}",
                    visibility="private",
                    kind="email",
                    description=description,
                    estimated_tokens=max(1, (len(snippet) + len(subject) + 2) // 3),
                )
                output.append(
                    _FederatedRef(
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

    @classmethod
    async def search(
        cls,
        runtime_context,
        *,
        tenant_id: str,
        user_id: str | None,
        conversation_id: str | None,
        authority: set[str],
        surface: SurfaceKind | str,
        query: str,
        limit: int = 8,
    ) -> list[ContextRef]:
        surface_kind = SurfaceKind.coerce(surface)
        wanted = max(1, min(int(limit), 20))
        local_refs = await ContextBroker.search(
            runtime_context.db,
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            authority=authority,
            surface=surface_kind,
            query=query,
            limit=20,
        )
        candidates = [
            _FederatedRef(
                ref=ref,
                text=(
                    f"source:{ref.source} scope:{ref.scope} kind:{ref.kind} "
                    f"visibility:{ref.visibility}\n{ref.description}"
                ),
            )
            for ref in local_refs
        ]
        if user_id and surface_kind.allows_personal_global and "messaging:read" in authority:
            candidates.extend(
                await cls._search_gmail(
                    runtime_context,
                    user_id=user_id,
                    surface=surface_kind,
                    conversation_id=conversation_id,
                    query=query,
                    limit=wanted,
                )
            )
        if not candidates:
            return []
        clean_query = " ".join(str(query or "").split()).strip()
        if not clean_query:
            return [item.ref for item in candidates[:wanted]]
        matches = await asyncio.to_thread(
            cls._ranker.rank,
            [SemanticDocument(key=str(index), text=item.text) for index, item in enumerate(candidates)],
            clean_query,
            limit=min(wanted, len(candidates)),
        )
        output = []
        for match in matches:
            try:
                item = candidates[int(match.key)]
            except (ValueError, IndexError):
                continue
            ref = item.ref
            output.append(
                ContextRef(
                    id=ref.id,
                    source=ref.source,
                    scope=ref.scope,
                    visibility=ref.visibility,
                    kind=ref.kind,
                    description=ref.description,
                    estimated_tokens=ref.estimated_tokens,
                    score=round(float(match.score), 6),
                )
            )
        return output

    @classmethod
    async def _materialize_gmail(
        cls,
        runtime_context,
        *,
        user_id: str,
        surface: SurfaceKind,
        conversation_id: str | None,
        requested: list[tuple[str, str, str]],
    ) -> dict[str, dict]:
        if not requested:
            return {}
        authorized = {
            connector.id: connector
            for connector in await cls._gmail_connectors(runtime_context.db, user_id=user_id)
        }
        if not authorized:
            return {}
        execution = await cls._personal_execution(
            runtime_context,
            user_id=user_id,
            surface=surface,
            conversation_id=conversation_id,
        )
        channel, metadata = cls._runtime_metadata(runtime_context, surface)

        async def read_one(original_ref: str, connector_id: str, message_id: str):
            if connector_id not in authorized:
                return original_ref, None
            try:
                result = await cls._gmail_firewall.invoke(
                    CapabilityInvocation(
                        capability_id="history.gmail.read_account_message",
                        arguments={
                            "connector_id": connector_id,
                            "message_id": message_id,
                        },
                        objective="Materialize an explicitly selected authorized Gmail history reference",
                        rationale="context.get requested this federated Gmail reference",
                        expected_outcome="One Gmail message from the same account that produced the reference",
                        channel=channel,
                        metadata=metadata,
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
            payload = {
                "ref": original_ref,
                "source": "gmail",
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
            return original_ref, payload

        rows = await asyncio.gather(
            *(read_one(original_ref, connector_id, message_id) for original_ref, connector_id, message_id in requested)
        )
        return {ref: payload for ref, payload in rows if payload is not None}

    @classmethod
    async def materialize(
        cls,
        runtime_context,
        *,
        refs: list[str],
        tenant_id: str,
        user_id: str | None,
        conversation_id: str | None,
        authority: set[str],
        surface: SurfaceKind | str,
    ) -> list[dict]:
        requested = [str(item).strip() for item in refs if str(item).strip()][:12]
        surface_kind = SurfaceKind.coerce(surface)
        gmail_requests: list[tuple[str, str, str]] = []
        local_refs: list[str] = []
        for ref in requested:
            parsed = cls._parse_gmail_ref(ref)
            if parsed:
                gmail_requests.append((ref, parsed[0], parsed[1]))
            else:
                local_refs.append(ref)

        local_rows = await ContextBroker.materialize(
            runtime_context.db,
            refs=local_refs,
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            authority=authority,
            surface=surface_kind,
        )
        by_ref = {str(row.get("ref") or ""): row for row in local_rows}
        if (
            gmail_requests
            and user_id
            and surface_kind.allows_personal_global
            and "messaging:read" in authority
        ):
            by_ref.update(
                await cls._materialize_gmail(
                    runtime_context,
                    user_id=user_id,
                    surface=surface_kind,
                    conversation_id=conversation_id,
                    requested=gmail_requests,
                )
            )
        return [by_ref[ref] for ref in requested if ref in by_ref]
