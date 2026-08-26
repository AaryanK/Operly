from __future__ import annotations

import asyncio
from dataclasses import dataclass

from packages.context.broker import ContextBroker, ContextRef
from packages.context.history_adapters import default_provider_history_registry
from packages.retrieval.semantic import SemanticDocument, SemanticTextIndex
from packages.security.surfaces import SurfaceKind


@dataclass(frozen=True, slots=True)
class _FederatedRef:
    ref: ContextRef
    text: str


class FederatedHistoryService:
    """One authorized retrieval boundary across Operly and provider-owned history.

    ContextBroker remains the local source. External provider history is discovered
    through ProviderHistoryRegistry, so this service does not need provider-specific
    search/materialization branches. Every adapter owns account discovery, governed
    provider invocation, ref parsing and reauthorization on materialization.
    """

    _ranker = SemanticTextIndex(max_cached_documents=10_000)
    _provider_registry = default_provider_history_registry()

    # Compatibility seams for the tests and callers introduced with the first Gmail
    # federation cutover. New code should register/use ProviderHistoryAdapter instead.
    _gmail_adapter = _provider_registry.get("google.gmail")
    _gmail_firewall = getattr(_gmail_adapter, "firewall", None)
    _calendar_adapter = _provider_registry.get("google.calendar")
    _calendar_firewall = getattr(_calendar_adapter, "firewall", None)

    @classmethod
    def provider_history_registry(cls):
        return cls._provider_registry

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
        authority_set = set(authority)
        wanted = max(1, min(int(limit), 20))

        local_refs = await ContextBroker.search(
            runtime_context.db,
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            authority=authority_set,
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

        adapters = cls._provider_registry.eligible(
            surface=surface_kind,
            authority=authority_set,
            user_id=user_id,
        )
        if user_id and adapters:
            groups = await asyncio.gather(
                *(
                    adapter.search(
                        runtime_context,
                        user_id=user_id,
                        surface=surface_kind,
                        conversation_id=conversation_id,
                        query=query,
                        limit=wanted,
                    )
                    for adapter in adapters
                )
            )
            candidates.extend(
                _FederatedRef(ref=hit.ref, text=hit.text)
                for group in groups
                for hit in group
            )

        if not candidates:
            return []
        clean_query = " ".join(str(query or "").split()).strip()
        if not clean_query:
            return [item.ref for item in candidates[:wanted]]

        matches = await asyncio.to_thread(
            cls._ranker.rank,
            [
                SemanticDocument(key=str(index), text=item.text)
                for index, item in enumerate(candidates)
            ],
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
        if not requested:
            return []

        surface_kind = SurfaceKind.coerce(surface)
        authority_set = set(authority)
        local_refs: list[str] = []
        grouped_provider_refs: dict[str, tuple[object, list[str]]] = {}

        for ref in requested:
            matching_adapter = next(
                (
                    adapter
                    for adapter in cls._provider_registry.all()
                    if adapter.matches_ref(ref)
                ),
                None,
            )
            if matching_adapter is None:
                local_refs.append(ref)
                continue
            if not matching_adapter.eligible(
                surface=surface_kind,
                authority=authority_set,
                user_id=user_id,
            ):
                # Provider refs are locators, not bearer tokens. A known provider ref
                # without current authority is dropped instead of falling through to a
                # local materializer that cannot validate the provider account.
                continue
            entry = grouped_provider_refs.setdefault(
                matching_adapter.id,
                (matching_adapter, []),
            )
            entry[1].append(ref)

        local_rows = await ContextBroker.materialize(
            runtime_context.db,
            refs=local_refs,
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            authority=authority_set,
            surface=surface_kind,
        )
        by_ref = {str(row.get("ref") or ""): row for row in local_rows}

        if user_id and grouped_provider_refs:
            groups = await asyncio.gather(
                *(
                    adapter.materialize(
                        runtime_context,
                        user_id=user_id,
                        surface=surface_kind,
                        conversation_id=conversation_id,
                        refs=provider_refs,
                    )
                    for adapter, provider_refs in grouped_provider_refs.values()
                )
            )
            for group in groups:
                by_ref.update(group)

        return [by_ref[ref] for ref in requested if ref in by_ref]
