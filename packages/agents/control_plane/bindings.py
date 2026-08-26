"""Bindings from the factory control plane to Operly's existing authority primitives."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

from packages.context.broker import ContextBroker
from packages.database.db import session_scope
from packages.security.execution_context import ExecutionContext


@dataclass(frozen=True, slots=True)
class AuthorizedContextBindings:
    """Context callbacks bound to one already-resolved ExecutionContext.

    Search and materialization both reapply the same authority/surface predicate. A
    ContextRef therefore remains only a locator; it never becomes a bearer token.
    """

    execution: ExecutionContext
    tenant_id: str
    user_id: str | None
    conversation_id: str | None

    async def search(self, query: str, limit: int) -> list[dict[str, Any]]:
        async with session_scope() as db:
            rows = await ContextBroker.search(
                db,
                tenant_id=self.tenant_id,
                user_id=self.user_id,
                conversation_id=self.conversation_id,
                authority=set(self.execution.permissions),
                surface=self.execution.surface,
                query=query,
                limit=limit,
            )
        return [row.as_dict() for row in rows]

    async def materialize(self, refs: list[str]) -> list[dict[str, Any]]:
        async with session_scope() as db:
            return await ContextBroker.materialize(
                db,
                refs=refs,
                tenant_id=self.tenant_id,
                user_id=self.user_id,
                conversation_id=self.conversation_id,
                authority=set(self.execution.permissions),
                surface=self.execution.surface,
            )


class FactoryCapabilityIntentResolver:
    """Resolve plain-language stage intents into exact authorized capability IDs.

    This is application-side discovery. It does not grant permissions and it never
    accepts IDs supplied by the model as authority. The optional session view is
    expanded only for capabilities that the registry says are installed, configured,
    healthy and authorized for this execution scope.
    """

    def __init__(
        self,
        *,
        registry,
        scope_id: str,
        authority: set[str],
        visible_predicate: Callable[[str], bool] | None = None,
        session_view=None,
        max_per_intent: int = 3,
        max_total: int = 16,
    ) -> None:
        self.registry = registry
        self.scope_id = scope_id
        self.authority = set(authority)
        self.visible_predicate = visible_predicate
        self.session_view = session_view
        self.max_per_intent = max(1, min(int(max_per_intent), 8))
        self.max_total = max(1, min(int(max_total), 32))

    def _allowed(self, capability_id: str) -> bool:
        if self.visible_predicate is not None and not self.visible_predicate(capability_id):
            return False
        try:
            availability = self.registry.availability(
                self.scope_id,
                capability_id,
                authority=self.authority,
            )
        except (LookupError, PermissionError):
            return False
        return bool(availability.available)

    async def __call__(self, intents: Iterable[str]) -> list[str]:
        selected: list[str] = []
        seen: set[str] = set()
        for intent in list(intents)[:8]:
            clean = " ".join(str(intent or "").split()).strip()
            if not clean:
                continue
            rows = self.registry.search(
                self.scope_id,
                clean,
                authority=self.authority,
                limit=max(self.max_per_intent * 3, 6),
            )
            added = 0
            for row in rows:
                capability_id = str(row.get("id") or "").strip()
                if not capability_id or capability_id in seen or not self._allowed(capability_id):
                    continue
                seen.add(capability_id)
                selected.append(capability_id)
                added += 1
                if added >= self.max_per_intent or len(selected) >= self.max_total:
                    break
            if len(selected) >= self.max_total:
                break
        if selected and self.session_view is not None:
            # Exposure is not authority: SessionCapabilityView.expose rechecks the
            # registry/authority predicate before any exact schema reaches a worker.
            self.session_view.expose(selected)
        return selected
