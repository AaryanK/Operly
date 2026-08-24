"""Reference-first, surface-safe context retrieval for agent runtimes."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.context.service import ContextService
from packages.database.channel_models import ContextRecord
from packages.security.surfaces import SurfaceKind


@dataclass(frozen=True, slots=True)
class ContextRef:
    id: str
    scope: str
    visibility: str
    kind: str
    description: str
    estimated_tokens: int

    def as_dict(self) -> dict:
        return {
            "ref": self.id,
            "scope": self.scope,
            "visibility": self.visibility,
            "kind": self.kind,
            "description": self.description,
            "estimated_tokens": self.estimated_tokens,
        }


class ContextBroker:
    """Search/materialize context without making references into bearer tokens.

    Every search and every materialization reconstructs the allowed record predicate
    from trusted principal/workspace/surface/permission inputs. Possessing a context
    id never grants access to the referenced content.
    """

    @staticmethod
    def _estimated_tokens(content: str) -> int:
        # Cheap conservative estimate used for routing/budgeting, not billing.
        return max(1, (len(str(content or "")) + 2) // 3)

    @classmethod
    def _ref(cls, row: ContextRecord, *, preview_chars: int = 180) -> ContextRef:
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

        statement = select(ContextRecord).where(allowed)
        statement = ContextService._ranked_query(statement, query).limit(
            max(1, min(int(limit), 20))
        )
        rows = (await db.scalars(statement)).all()
        return [cls._ref(row) for row in rows]

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
                select(ContextRecord).where(
                    ContextRecord.id.in_(ids),
                    allowed,
                )
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
