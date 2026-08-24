"""Reference-first, surface-safe semantic context retrieval for agent runtimes."""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Iterable

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.channel_models import ContextRecord
from packages.retrieval.semantic import SemanticDocument, SemanticTextIndex
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

    def as_dict(self) -> dict:
        payload = {
            "ref": self.id,
            "scope": self.scope,
            "visibility": self.visibility,
            "kind": self.kind,
            "description": self.description,
            "estimated_tokens": self.estimated_tokens,
        }
        if self.score is not None:
            payload["score"] = self.score
        return payload


class ContextBroker:
    """Authorize first, semantically rank second, materialize only on request.

    Context references are locators, never bearer tokens. Every search candidate and
    every later materialization is rechecked against the trusted principal, surface,
    workspace and conversation scope.
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

    @classmethod
    def _ref(
        cls,
        row: ContextRecord,
        *,
        preview_chars: int = 140,
        score: float | None = None,
    ) -> ContextRef:
        preview = " ".join(str(row.content or "").split())[: max(0, preview_chars)]
        description = f"{row.scope_type}:{row.kind}"
        if preview:
            description += f" — {preview}"
        return ContextRef(
            id=str(row.id),
            scope=str(row.scope_type),
            visibility=str(row.visibility),
            kind=str(row.kind),
            description=description,
            estimated_tokens=cls._estimated_tokens(row.content),
            score=round(float(score), 6) if score is not None else None,
        )

    @staticmethod
    def _allowed_predicate(
        *,
        tenant_id: str,
        user_id: str | None,
        conversation_id: str | None,
        authority: set[str],
        surface: SurfaceKind,
    ):
        clauses = []
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
    def _candidate_limit(limit: int) -> int:
        try:
            configured = int(os.getenv("OPERLY_CONTEXT_SEMANTIC_CANDIDATES", "750"))
        except ValueError:
            configured = 750
        return max(max(32, int(limit) * 8), min(configured, 2_000))

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
        """Return compact refs ranked only within the authorized context universe."""
        surface_kind = SurfaceKind.coerce(surface)
        allowed = cls._allowed_predicate(
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            authority=authority,
            surface=surface_kind,
        )
        if allowed is None:
            return []

        wanted = max(1, min(int(limit), 20))
        candidate_limit = cls._candidate_limit(wanted)
        rows = (
            await db.scalars(
                select(ContextRecord)
                .where(allowed)
                .order_by(ContextRecord.updated_at.desc())
                .limit(candidate_limit)
            )
        ).all()
        if not rows:
            return []

        clean_query = " ".join(str(query or "").split()).strip()
        if not clean_query:
            return [cls._ref(row) for row in rows[:wanted]]

        documents = [
            SemanticDocument(
                key=str(row.id),
                text=(
                    f"scope:{row.scope_type} kind:{row.kind} visibility:{row.visibility}\n"
                    f"{row.content}"
                ),
            )
            for row in rows
        ]
        matches = cls._semantic_index.rank(
            documents,
            clean_query,
            limit=min(wanted, len(documents)),
        )
        by_id = {str(row.id): row for row in rows}
        return [
            cls._ref(by_id[match.key], score=match.score)
            for match in matches
            if match.key in by_id
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
        surface_kind = SurfaceKind.coerce(surface)
        allowed = cls._allowed_predicate(
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            authority=authority,
            surface=surface_kind,
        )
        if allowed is None:
            return []
        rows = (
            await db.scalars(
                select(ContextRecord).where(ContextRecord.id.in_(ids), allowed)
            )
        ).all()
        by_id = {str(row.id): row for row in rows}
        output = []
        for ref in ids:
            row = by_id.get(ref)
            if row is None:
                continue
            output.append(
                {
                    "ref": ref,
                    "scope": row.scope_type,
                    "visibility": row.visibility,
                    "kind": row.kind,
                    "content": row.content,
                    "estimated_tokens": cls._estimated_tokens(row.content),
                }
            )
        return output
