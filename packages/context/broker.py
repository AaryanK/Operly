"""Reference-first, surface-safe federated history retrieval for agent runtimes."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import os
from typing import Iterable

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.channel_models import ContextRecord
from packages.database.company_models import BusinessEventRecord
from packages.database.models import Message, TenantMember
from packages.database.principal_models import Principal, PrincipalConversation, PrincipalMessage
from packages.retrieval.semantic import SemanticDocument, SemanticTextIndex
from packages.security.permissions import resolve_workspace_permissions
from packages.security.surfaces import SurfaceKind


@dataclass(frozen=True, slots=True)
class ContextRef:
    id: str
    scope: str
    visibility: str
    kind: str
    description: str
    estimated_tokens: int
    score: float | None = None
    source: str = "context"

    def as_dict(self) -> dict:
        payload = {
            "ref": self.id,
            "source": self.source,
            "scope": self.scope,
            "visibility": self.visibility,
            "kind": self.kind,
            "description": self.description,
            "estimated_tokens": self.estimated_tokens,
        }
        if self.score is not None:
            payload["score"] = self.score
        return payload


@dataclass(frozen=True, slots=True)
class _Candidate:
    key: str
    source: str
    scope: str
    visibility: str
    kind: str
    description: str
    content: str
    payload: dict


class ContextBroker:
    """Authorize first, rank second, materialize only on request.

    On Personal surfaces this is a federated history boundary: Operly context,
    Personal conversations, authorized workspace/channel messages and authorized
    BusinessEvents are ranked together. Every workspace is permission-resolved
    independently before its records enter the candidate set. Context references are
    locators, never bearer tokens, so materialization repeats the same authorization.
    """

    _semantic_index = SemanticTextIndex(max_cached_documents=50_000)

    @classmethod
    def semantic_backend_name(cls) -> str:
        return cls._semantic_index.backend_name

    @classmethod
    def semantic_degraded_reason(cls) -> str | None:
        return cls._semantic_index.degraded_reason

    @staticmethod
    def _estimated_tokens(content: str) -> int:
        return max(1, (len(str(content or "")) + 2) // 3)

    @staticmethod
    def _candidate_limit(limit: int) -> int:
        try:
            configured = int(os.getenv("OPERLY_CONTEXT_SEMANTIC_CANDIDATES", "750"))
        except ValueError:
            configured = 750
        return max(max(32, int(limit) * 8), min(configured, 2_000))

    @staticmethod
    def _json(value: str | None) -> dict:
        try:
            payload = json.loads(value or "{}")
        except (TypeError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @classmethod
    def _allowed_predicate(
        cls,
        *,
        tenant_id: str,
        user_id: str | None,
        conversation_id: str | None,
        authority: set[str],
        surface: SurfaceKind | str,
    ):
        """Legacy single-workspace predicate retained as a security contract seam.

        Federated Personal retrieval now resolves every workspace independently before
        calling ``_context_allowed_predicate``. Existing callers and regressions still
        use this helper to prove the original single-workspace fail-closed behavior,
        so keep it explicit rather than weakening those tests or fabricating federation
        state synchronously.
        """
        surface_kind = SurfaceKind.coerce(surface)
        current_authority = set(authority)
        clauses = []
        if "context:tenant:read" in current_authority:
            clauses.append(
                and_(
                    ContextRecord.scope_type == "tenant",
                    ContextRecord.visibility == "shared",
                    ContextRecord.tenant_id == tenant_id,
                )
            )
        if "context:conversation:read" in current_authority and conversation_id:
            clauses.append(
                and_(
                    ContextRecord.scope_type == "conversation",
                    ContextRecord.visibility == "shared",
                    ContextRecord.tenant_id == tenant_id,
                    ContextRecord.conversation_id == conversation_id,
                )
            )
            if user_id and surface_kind.allows_private_conversation:
                clauses.append(
                    and_(
                        ContextRecord.scope_type == "conversation",
                        ContextRecord.visibility == "private",
                        ContextRecord.owner_user_id == user_id,
                        ContextRecord.tenant_id == tenant_id,
                        ContextRecord.conversation_id == conversation_id,
                    )
                )
        if user_id and "context:human:read" in current_authority:
            if surface_kind.allows_personal_global:
                clauses.append(
                    and_(
                        ContextRecord.scope_type == "human",
                        ContextRecord.visibility == "private",
                        ContextRecord.owner_user_id == user_id,
                        or_(
                            ContextRecord.tenant_id.is_(None),
                            ContextRecord.tenant_id == tenant_id,
                        ),
                    )
                )
            elif surface_kind.allows_personal_workspace:
                clauses.append(
                    and_(
                        ContextRecord.scope_type == "human",
                        ContextRecord.visibility == "private",
                        ContextRecord.owner_user_id == user_id,
                        ContextRecord.tenant_id == tenant_id,
                    )
                )
        return or_(*clauses) if clauses else None

    @classmethod
    async def _workspace_permissions(
        cls,
        db: AsyncSession,
        *,
        tenant_id: str,
        user_id: str | None,
        authority: set[str],
        surface: SurfaceKind,
    ) -> dict[str, set[str]]:
        if not user_id or not surface.allows_personal_global:
            return {tenant_id: set(authority)} if tenant_id else {}
        memberships = list(
            (
                await db.scalars(
                    select(TenantMember).where(TenantMember.user_id == user_id)
                )
            ).all()
        )
        resolved: dict[str, set[str]] = {}
        for membership in memberships:
            resolved[membership.tenant_id] = await resolve_workspace_permissions(
                db,
                tenant_id=membership.tenant_id,
                role=membership.role,
            )
        return resolved

    @classmethod
    def _context_allowed_predicate(
        cls,
        *,
        tenant_id: str,
        user_id: str | None,
        conversation_id: str | None,
        authority: set[str],
        surface: SurfaceKind,
        workspace_permissions: dict[str, set[str]],
    ):
        clauses = []
        if surface.allows_personal_global and user_id:
            workspace_ids = list(workspace_permissions)
            if "context:human:read" in authority:
                human_tenant_clause = ContextRecord.tenant_id.is_(None)
                if workspace_ids:
                    human_tenant_clause = or_(
                        ContextRecord.tenant_id.is_(None),
                        ContextRecord.tenant_id.in_(workspace_ids),
                    )
                clauses.append(
                    and_(
                        ContextRecord.scope_type == "human",
                        ContextRecord.visibility == "private",
                        ContextRecord.owner_user_id == user_id,
                        human_tenant_clause,
                    )
                )
            tenant_read_ids = [
                workspace_id
                for workspace_id, permissions in workspace_permissions.items()
                if "context:tenant:read" in permissions
            ]
            if tenant_read_ids:
                clauses.append(
                    and_(
                        ContextRecord.scope_type == "tenant",
                        ContextRecord.visibility == "shared",
                        ContextRecord.tenant_id.in_(tenant_read_ids),
                    )
                )
            conversation_read_ids = [
                workspace_id
                for workspace_id, permissions in workspace_permissions.items()
                if "context:conversation:read" in permissions
            ]
            if conversation_id and conversation_read_ids:
                clauses.append(
                    and_(
                        ContextRecord.scope_type == "conversation",
                        ContextRecord.visibility == "shared",
                        ContextRecord.tenant_id.in_(conversation_read_ids),
                        ContextRecord.conversation_id == conversation_id,
                    )
                )
            if conversation_id and surface.allows_private_conversation:
                clauses.append(
                    and_(
                        ContextRecord.scope_type == "conversation",
                        ContextRecord.visibility == "private",
                        ContextRecord.owner_user_id == user_id,
                        ContextRecord.conversation_id == conversation_id,
                        or_(
                            ContextRecord.tenant_id.is_(None),
                            ContextRecord.tenant_id.in_(workspace_ids),
                        ) if workspace_ids else ContextRecord.tenant_id.is_(None),
                    )
                )
            return or_(*clauses) if clauses else None

        if "context:tenant:read" in authority:
            clauses.append(
                and_(
                    ContextRecord.scope_type == "tenant",
                    ContextRecord.visibility == "shared",
                    ContextRecord.tenant_id == tenant_id,
                )
            )
        if "context:conversation:read" in authority and conversation_id:
            clauses.append(
                and_(
                    ContextRecord.scope_type == "conversation",
                    ContextRecord.visibility == "shared",
                    ContextRecord.tenant_id == tenant_id,
                    ContextRecord.conversation_id == conversation_id,
                )
            )
            if user_id and surface.allows_private_conversation:
                clauses.append(
                    and_(
                        ContextRecord.scope_type == "conversation",
                        ContextRecord.visibility == "private",
                        ContextRecord.owner_user_id == user_id,
                        ContextRecord.tenant_id == tenant_id,
                        ContextRecord.conversation_id == conversation_id,
                    )
                )
        if user_id and "context:human:read" in authority:
            if surface.allows_personal_global:
                clauses.append(
                    and_(
                        ContextRecord.scope_type == "human",
                        ContextRecord.visibility == "private",
                        ContextRecord.owner_user_id == user_id,
                        or_(
                            ContextRecord.tenant_id.is_(None),
                            ContextRecord.tenant_id == tenant_id,
                        ),
                    )
                )
            elif surface.allows_personal_workspace:
                clauses.append(
                    and_(
                        ContextRecord.scope_type == "human",
                        ContextRecord.visibility == "private",
                        ContextRecord.owner_user_id == user_id,
                        ContextRecord.tenant_id == tenant_id,
                    )
                )
        return or_(*clauses) if clauses else None

    @staticmethod
    def _ref(candidate: _Candidate, score: float | None = None) -> ContextRef:
        return ContextRef(
            id=candidate.key,
            source=candidate.source,
            scope=candidate.scope,
            visibility=candidate.visibility,
            kind=candidate.kind,
            description=candidate.description,
            estimated_tokens=ContextBroker._estimated_tokens(candidate.content),
            score=round(float(score), 6) if score is not None else None,
        )

    @classmethod
    async def _collect_candidates(
        cls,
        db: AsyncSession,
        *,
        tenant_id: str,
        user_id: str | None,
        conversation_id: str | None,
        authority: set[str],
        surface: SurfaceKind,
        limit: int,
        requested_refs: set[str] | None = None,
    ) -> list[_Candidate]:
        workspace_permissions = await cls._workspace_permissions(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            authority=authority,
            surface=surface,
        )
        candidates: list[_Candidate] = []

        allowed = cls._context_allowed_predicate(
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            authority=authority,
            surface=surface,
            workspace_permissions=workspace_permissions,
        )
        if allowed is not None:
            query = select(ContextRecord).where(allowed)
            if requested_refs is not None:
                context_ids = {
                    ref.split(":", 1)[1] if ref.startswith("context:") else ref
                    for ref in requested_refs
                    if ":" not in ref or ref.startswith("context:")
                }
                if context_ids:
                    query = query.where(ContextRecord.id.in_(context_ids))
                else:
                    query = query.where(ContextRecord.id == "")
            else:
                query = query.order_by(ContextRecord.updated_at.desc()).limit(limit)
            rows = list((await db.scalars(query)).all())
            for row in rows:
                preview = " ".join(str(row.content or "").split())[:140]
                description = f"{row.scope_type}:{row.kind}"
                if preview:
                    description += f" — {preview}"
                candidates.append(
                    _Candidate(
                        key=str(row.id),
                        source="context",
                        scope=str(row.scope_type),
                        visibility=str(row.visibility),
                        kind=str(row.kind),
                        description=description,
                        content=str(row.content or ""),
                        payload={
                            "ref": str(row.id),
                            "source": "context",
                            "scope": row.scope_type,
                            "visibility": row.visibility,
                            "kind": row.kind,
                            "content": row.content,
                        },
                    )
                )

        # Personal conversation history is attached to all runtime principals that
        # resolve to this AppUser. This intentionally spans legacy `user` and newer
        # `human` principals without merging their authority records.
        if user_id and surface.allows_personal_global:
            query = (
                select(PrincipalMessage, PrincipalConversation)
                .join(
                    PrincipalConversation,
                    PrincipalConversation.id == PrincipalMessage.conversation_id,
                )
                .join(Principal, Principal.id == PrincipalConversation.principal_id)
                .where(Principal.user_id == user_id)
            )
            if requested_refs is not None:
                message_ids = {
                    ref.split(":", 1)[1]
                    for ref in requested_refs
                    if ref.startswith("principal_message:")
                }
                if message_ids:
                    query = query.where(PrincipalMessage.id.in_(message_ids))
                else:
                    query = query.where(PrincipalMessage.id == "")
            else:
                query = query.order_by(PrincipalMessage.created_at.desc()).limit(limit)
            rows = list((await db.execute(query)).all())
            for row, conversation in rows:
                content = str(row.content or "")
                candidates.append(
                    _Candidate(
                        key=f"principal_message:{row.id}",
                        source="personal_conversation",
                        scope="personal",
                        visibility="private",
                        kind=f"conversation_{row.role}",
                        description=(
                            f"personal conversation ({conversation.provider}) — "
                            f"{' '.join(content.split())[:140]}"
                        ),
                        content=content,
                        payload={
                            "ref": f"principal_message:{row.id}",
                            "source": "personal_conversation",
                            "scope": "personal",
                            "visibility": "private",
                            "kind": f"conversation_{row.role}",
                            "provider": conversation.provider,
                            "conversation_id": conversation.external_conversation_id,
                            "role": row.role,
                            "content": content,
                            "created_at": row.created_at.isoformat(),
                        },
                    )
                )

        readable_message_workspaces = [
            workspace_id
            for workspace_id, permissions in workspace_permissions.items()
            if "messages:read" in permissions
        ]
        if readable_message_workspaces:
            query = select(Message).where(Message.tenant_id.in_(readable_message_workspaces))
            if requested_refs is not None:
                message_ids = {
                    ref.split(":", 1)[1]
                    for ref in requested_refs
                    if ref.startswith("workspace_message:")
                }
                if message_ids:
                    query = query.where(Message.id.in_(message_ids))
                else:
                    query = query.where(Message.id == "")
            else:
                query = query.order_by(Message.created_at.desc()).limit(limit)
            rows = list((await db.scalars(query)).all())
            for row in rows:
                content = str(row.content or "")
                candidates.append(
                    _Candidate(
                        key=f"workspace_message:{row.id}",
                        source="workspace_message",
                        scope=f"workspace:{row.tenant_id}",
                        visibility="shared",
                        kind="message",
                        description=(
                            f"workspace message by {row.author_name} — "
                            f"{' '.join(content.split())[:140]}"
                        ),
                        content=content,
                        payload={
                            "ref": f"workspace_message:{row.id}",
                            "source": "workspace_message",
                            "scope": f"workspace:{row.tenant_id}",
                            "visibility": "shared",
                            "kind": "message",
                            "workspace_id": row.tenant_id,
                            "channel_id": str(row.channel_id),
                            "message_id": str(row.message_id),
                            "author_name": row.author_name,
                            "is_bot": bool(row.is_bot),
                            "content": content,
                            "created_at": row.created_at.isoformat(),
                        },
                    )
                )

        readable_event_workspaces = [
            workspace_id
            for workspace_id, permissions in workspace_permissions.items()
            if "operations:read" in permissions or "actions:read" in permissions
        ]
        event_scope = []
        if readable_event_workspaces:
            event_scope.append(
                and_(
                    BusinessEventRecord.scope_kind == "workspace",
                    BusinessEventRecord.tenant_id.in_(readable_event_workspaces),
                )
            )
        if user_id and surface.allows_personal_global:
            event_scope.append(
                and_(
                    BusinessEventRecord.scope_kind == "personal",
                    BusinessEventRecord.owner_user_id == user_id,
                )
            )
        if event_scope:
            query = select(BusinessEventRecord).where(or_(*event_scope))
            if requested_refs is not None:
                event_ids = {
                    ref.split(":", 1)[1]
                    for ref in requested_refs
                    if ref.startswith("business_event:")
                }
                if event_ids:
                    query = query.where(BusinessEventRecord.id.in_(event_ids))
                else:
                    query = query.where(BusinessEventRecord.id == "")
            else:
                query = query.order_by(BusinessEventRecord.occurred_at.desc()).limit(limit)
            rows = list((await db.scalars(query)).all())
            for row in rows:
                payload = cls._json(row.payload_json)
                searchable = json.dumps(payload, sort_keys=True, default=str)
                scope = (
                    f"workspace:{row.tenant_id}"
                    if row.scope_kind == "workspace"
                    else "personal"
                )
                candidates.append(
                    _Candidate(
                        key=f"business_event:{row.id}",
                        source="business_event",
                        scope=scope,
                        visibility="shared" if row.scope_kind == "workspace" else "private",
                        kind=row.event_type,
                        description=(
                            f"{row.event_type} via {row.source} — "
                            f"{' '.join(searchable.split())[:140]}"
                        ),
                        content=f"{row.event_type}\n{searchable}",
                        payload={
                            "ref": f"business_event:{row.id}",
                            "source": "business_event",
                            "scope": scope,
                            "visibility": "shared" if row.scope_kind == "workspace" else "private",
                            "kind": row.event_type,
                            "event_type": row.event_type,
                            "event_source": row.source,
                            "payload": payload,
                            "occurred_at": row.occurred_at.isoformat(),
                        },
                    )
                )

        return candidates

    @classmethod
    async def search(
        cls,
        db: AsyncSession,
        *,
        tenant_id: str,
        user_id: str | None,
        conversation_id: str | None,
        authority: set[str],
        surface: SurfaceKind | str,
        query: str,
        limit: int = 8,
    ) -> list[ContextRef]:
        """Return compact refs ranked within the complete authorized history universe."""
        surface_kind = SurfaceKind.coerce(surface)
        wanted = max(1, min(int(limit), 20))
        candidates = await cls._collect_candidates(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            authority=set(authority),
            surface=surface_kind,
            limit=cls._candidate_limit(wanted),
        )
        if not candidates:
            return []

        clean_query = " ".join(str(query or "").split()).strip()
        if not clean_query:
            return [cls._ref(candidate) for candidate in candidates[:wanted]]

        documents = [
            SemanticDocument(
                key=candidate.key,
                text=(
                    f"source:{candidate.source} scope:{candidate.scope} "
                    f"kind:{candidate.kind} visibility:{candidate.visibility}\n"
                    f"{candidate.content}"
                ),
            )
            for candidate in candidates
        ]
        matches = await asyncio.to_thread(
            cls._semantic_index.rank,
            documents,
            clean_query,
            limit=min(wanted, len(documents)),
        )
        by_key = {candidate.key: candidate for candidate in candidates}
        return [
            cls._ref(by_key[match.key], score=match.score)
            for match in matches
            if match.key in by_key
        ]

    @classmethod
    async def materialize(
        cls,
        db: AsyncSession,
        *,
        refs: Iterable[str],
        tenant_id: str,
        user_id: str | None,
        conversation_id: str | None,
        authority: set[str],
        surface: SurfaceKind | str,
    ) -> list[dict]:
        ids = [str(item).strip() for item in refs if str(item).strip()][:12]
        if not ids:
            return []
        candidates = await cls._collect_candidates(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            authority=set(authority),
            surface=SurfaceKind.coerce(surface),
            limit=max(32, len(ids) * 4),
            requested_refs=set(ids),
        )
        by_key = {candidate.key: candidate for candidate in candidates}
        # ContextRecord refs historically used bare UUIDs. Accept the explicit
        # `context:` form too without changing the refs existing agents persist.
        for candidate in candidates:
            if candidate.source == "context":
                by_key[f"context:{candidate.key}"] = candidate
        output = []
        for ref in ids:
            candidate = by_key.get(ref)
            if candidate is None:
                continue
            payload = dict(candidate.payload)
            payload["estimated_tokens"] = cls._estimated_tokens(candidate.content)
            output.append(payload)
        return output
