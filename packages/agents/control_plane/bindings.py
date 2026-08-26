"""Bindings from the factory control plane to Operly's existing authority primitives."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from sqlalchemy import desc, select

from packages.context.broker import ContextBroker
from packages.database.agent_models import AgentMessage
from packages.database.db import session_scope
from packages.database.scope_models import ConversationArtifact
from packages.security.execution_context import ExecutionContext


_CONVERSATION_REF_PREFIX = "agent-message:"
_CONVERSATION_ARTIFACT_REF_PREFIX = "conversation-artifact:"
_CONVERSATION_INTENT_MARKERS = frozenset(
    {
        "conversation",
        "earlier",
        "previous",
        "prior",
        "above",
        "again",
        "referent",
        "recent message",
        "what we discussed",
        "what i said",
        "what you said",
    }
)
_ATTACHMENT_INTENT_MARKERS = frozenset(
    {
        "attachment",
        "attached",
        "uploaded",
        "file",
        "image",
        "document",
        "report",
        "spreadsheet",
        "pdf",
        "that file",
        "this file",
    }
)


def _normalized(value: str) -> str:
    return " ".join(str(value or "").lower().split())


def _wants_conversation(query: str) -> bool:
    text = _normalized(query)
    return any(marker in text for marker in _CONVERSATION_INTENT_MARKERS)


def _wants_attachments(query: str) -> bool:
    text = _normalized(query)
    return any(marker in text for marker in _ATTACHMENT_INTENT_MARKERS)


@dataclass(frozen=True, slots=True)
class AuthorizedContextBindings:
    """Context callbacks bound to one already-resolved ExecutionContext.

    Search and materialization both reapply the same authority/surface predicate. A
    ContextRef therefore remains only a locator; it never becomes a bearer token.
    Recent chat history is *not* inherited automatically: a stage must explicitly ask
    for prior/recent conversation context before message refs become candidates.
    Retained conversation attachments follow the same rule and remain scoped to this
    exact shared workspace conversation.
    """

    execution: ExecutionContext
    tenant_id: str
    user_id: str | None
    conversation_id: str | None

    @property
    def _can_read_conversation(self) -> bool:
        return bool(
            self.conversation_id
            and "context:conversation:read" in set(self.execution.permissions)
        )

    async def _conversation_refs(self, db, *, limit: int) -> list[dict[str, Any]]:
        if not self._can_read_conversation:
            return []
        rows = list(
            (
                await db.scalars(
                    select(AgentMessage)
                    .where(
                        AgentMessage.tenant_id == self.tenant_id,
                        AgentMessage.conversation_id == self.conversation_id,
                        AgentMessage.role.in_(["user", "assistant"]),
                    )
                    .order_by(desc(AgentMessage.created_at))
                    .limit(max(1, min(int(limit), 8)))
                )
            ).all()
        )
        output = []
        for row in rows:
            preview = " ".join(str(row.content or "").split())[:160]
            output.append(
                {
                    "ref": f"{_CONVERSATION_REF_PREFIX}{row.id}",
                    "scope": "conversation",
                    "visibility": "authorized_conversation_tail",
                    "kind": f"message:{row.role}",
                    "description": preview,
                    "estimated_tokens": max(1, len(str(row.content or "")) // 4),
                    "score": 1.0,
                }
            )
        return output

    async def _artifact_refs(self, db, *, limit: int) -> list[dict[str, Any]]:
        if not self._can_read_conversation:
            return []
        rows = list(
            (
                await db.scalars(
                    select(ConversationArtifact)
                    .where(
                        ConversationArtifact.tenant_id == self.tenant_id,
                        ConversationArtifact.conversation_id == self.conversation_id,
                        # Workspace/Guest Workspace conversations are shared. Private
                        # artifacts are never materialized through this binding.
                        ConversationArtifact.principal_id.is_(None),
                    )
                    .order_by(desc(ConversationArtifact.created_at))
                    .limit(max(1, min(int(limit), 8)))
                )
            ).all()
        )
        output: list[dict[str, Any]] = []
        for row in rows:
            output.append(
                {
                    "ref": f"{_CONVERSATION_ARTIFACT_REF_PREFIX}{row.id}",
                    "scope": "conversation",
                    "visibility": "authorized_shared_conversation_artifact",
                    "kind": str(row.artifact_kind or "conversation_artifact"),
                    "description": str(row.name or "Conversation artifact")[:160],
                    "estimated_tokens": max(1, len(str(row.content_json or "")) // 4),
                    "score": 1.1,
                }
            )
        return output

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
            output = [row.as_dict() for row in rows]
            if _wants_conversation(query):
                output = [
                    *(await self._conversation_refs(db, limit=min(limit, 6))),
                    *output,
                ]
            if _wants_attachments(query):
                output = [
                    *(await self._artifact_refs(db, limit=min(limit, 6))),
                    *output,
                ]
        seen: set[str] = set()
        deduped = []
        for item in output:
            ref = str(item.get("ref") or "")
            if not ref or ref in seen:
                continue
            seen.add(ref)
            deduped.append(item)
            if len(deduped) >= max(1, min(int(limit), 20)):
                break
        return deduped

    @staticmethod
    def _bounded_artifact_content(row: ConversationArtifact) -> dict[str, Any]:
        try:
            payload = json.loads(row.content_json or "{}")
        except (TypeError, json.JSONDecodeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        return {
            "objective": str(payload.get("objective") or "")[:2500],
            "attachments": (
                list(payload.get("attachments") or [])[:10]
                if isinstance(payload.get("attachments"), list)
                else []
            ),
            "derivedAnalysis": str(payload.get("analysis") or "")[:8000],
            "operationSummary": str(payload.get("operationSummary") or "")[:1200],
            "outputs": (
                list(payload.get("outputs") or [])[:10]
                if isinstance(payload.get("outputs"), list)
                else []
            ),
            "warnings": (
                [str(item)[:500] for item in list(payload.get("warnings") or [])[:10]]
                if isinstance(payload.get("warnings"), list)
                else []
            ),
        }

    async def materialize(self, refs: list[str]) -> list[dict[str, Any]]:
        message_ids = [
            str(ref)[len(_CONVERSATION_REF_PREFIX) :]
            for ref in refs
            if str(ref).startswith(_CONVERSATION_REF_PREFIX)
        ]
        artifact_ids = [
            str(ref)[len(_CONVERSATION_ARTIFACT_REF_PREFIX) :]
            for ref in refs
            if str(ref).startswith(_CONVERSATION_ARTIFACT_REF_PREFIX)
        ]
        context_refs = [
            ref
            for ref in refs
            if not str(ref).startswith(_CONVERSATION_REF_PREFIX)
            and not str(ref).startswith(_CONVERSATION_ARTIFACT_REF_PREFIX)
        ]
        output: list[dict[str, Any]] = []
        async with session_scope() as db:
            if context_refs:
                output.extend(
                    await ContextBroker.materialize(
                        db,
                        refs=context_refs,
                        tenant_id=self.tenant_id,
                        user_id=self.user_id,
                        conversation_id=self.conversation_id,
                        authority=set(self.execution.permissions),
                        surface=self.execution.surface,
                    )
                )
            if message_ids and self._can_read_conversation:
                rows = list(
                    (
                        await db.scalars(
                            select(AgentMessage).where(
                                AgentMessage.id.in_(message_ids[:8]),
                                AgentMessage.tenant_id == self.tenant_id,
                                AgentMessage.conversation_id == self.conversation_id,
                                AgentMessage.role.in_(["user", "assistant"]),
                            )
                        )
                    ).all()
                )
                by_id = {str(row.id): row for row in rows}
                for message_id in message_ids[:8]:
                    row = by_id.get(message_id)
                    if row is None:
                        continue
                    output.append(
                        {
                            "ref": f"{_CONVERSATION_REF_PREFIX}{row.id}",
                            "scope": "conversation",
                            "visibility": "authorized_conversation_tail",
                            "kind": f"message:{row.role}",
                            "content": str(row.content or "")[:6000],
                            "estimated_tokens": max(1, len(str(row.content or "")) // 4),
                        }
                    )
            if artifact_ids and self._can_read_conversation:
                rows = list(
                    (
                        await db.scalars(
                            select(ConversationArtifact).where(
                                ConversationArtifact.id.in_(artifact_ids[:8]),
                                ConversationArtifact.tenant_id == self.tenant_id,
                                ConversationArtifact.conversation_id == self.conversation_id,
                                ConversationArtifact.principal_id.is_(None),
                            )
                        )
                    ).all()
                )
                by_id = {str(row.id): row for row in rows}
                for artifact_id in artifact_ids[:8]:
                    row = by_id.get(artifact_id)
                    if row is None:
                        continue
                    content = self._bounded_artifact_content(row)
                    output.append(
                        {
                            "ref": f"{_CONVERSATION_ARTIFACT_REF_PREFIX}{row.id}",
                            "scope": "conversation",
                            "visibility": "authorized_shared_conversation_artifact",
                            "kind": str(row.artifact_kind or "conversation_artifact"),
                            "name": str(row.name or "Conversation artifact")[:200],
                            "content": content,
                            "estimated_tokens": max(
                                1,
                                len(json.dumps(content, ensure_ascii=False, default=str)) // 4,
                            ),
                        }
                    )
        by_ref = {str(item.get("ref") or ""): item for item in output}
        return [by_ref[ref] for ref in refs if ref in by_ref]


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
            self.session_view.expose(selected)
        return selected
