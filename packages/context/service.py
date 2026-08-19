import json
from dataclasses import dataclass

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.channel_models import ContextRecord


@dataclass(slots=True)
class LoadedContext:
    human: list[ContextRecord]
    tenant: list[ContextRecord]
    conversation: list[ContextRecord]

    def as_prompt(self) -> str:
        sections: list[str] = []
        if self.human:
            sections.append(
                "PRIVATE HUMAN CONTEXT (only for the current authenticated human):\n"
                + "\n".join(f"- {row.content}" for row in self.human)
            )
        if self.tenant:
            sections.append(
                "SHARED TENANT CONTEXT (visible to authorized members of this workspace):\n"
                + "\n".join(f"- {row.content}" for row in self.tenant)
            )
        if self.conversation:
            sections.append(
                "CURRENT CONVERSATION CONTEXT:\n"
                + "\n".join(f"- {row.content}" for row in self.conversation)
            )
        return "\n\n".join(sections)


class ContextScopeError(ValueError):
    pass


class ContextService:
    """Tenant-safe context storage shared by every Operly channel.

    The caller never supplies arbitrary scope identifiers through model-visible
    arguments. Providers bind tenant/user/conversation values from runtime context.
    """

    @staticmethod
    def _clean(content: str) -> str:
        value = " ".join(str(content or "").split()).strip()
        if not value:
            raise ContextScopeError("Context content is empty")
        return value[:8000]

    @classmethod
    async def remember_human(
        cls,
        db: AsyncSession,
        *,
        user_id: str,
        content: str,
        tenant_id: str | None = None,
        kind: str = "fact",
        channel_provider: str | None = None,
        channel_space_id: str | None = None,
        source_message_id: str | None = None,
        metadata: dict | None = None,
    ) -> ContextRecord:
        if not user_id:
            raise ContextScopeError("Human context requires a linked user")
        row = ContextRecord(
            scope_type="human",
            visibility="private",
            owner_user_id=user_id,
            tenant_id=tenant_id,
            kind=kind[:50] or "fact",
            content=cls._clean(content),
            channel_provider=channel_provider,
            channel_space_id=channel_space_id,
            source_message_id=source_message_id,
            metadata_json=json.dumps(metadata or {}, separators=(",", ":")),
        )
        db.add(row)
        await db.flush()
        return row

    @classmethod
    async def remember_tenant(
        cls,
        db: AsyncSession,
        *,
        tenant_id: str,
        content: str,
        kind: str = "fact",
        channel_provider: str | None = None,
        channel_space_id: str | None = None,
        source_message_id: str | None = None,
        metadata: dict | None = None,
    ) -> ContextRecord:
        if not tenant_id:
            raise ContextScopeError("Tenant context requires a tenant")
        row = ContextRecord(
            scope_type="tenant",
            visibility="shared",
            tenant_id=tenant_id,
            kind=kind[:50] or "fact",
            content=cls._clean(content),
            channel_provider=channel_provider,
            channel_space_id=channel_space_id,
            source_message_id=source_message_id,
            metadata_json=json.dumps(metadata or {}, separators=(",", ":")),
        )
        db.add(row)
        await db.flush()
        return row

    @classmethod
    async def remember_conversation(
        cls,
        db: AsyncSession,
        *,
        tenant_id: str,
        conversation_id: str,
        content: str,
        user_id: str | None,
        private: bool,
        kind: str = "fact",
        channel_provider: str | None = None,
        channel_space_id: str | None = None,
        source_message_id: str | None = None,
        metadata: dict | None = None,
    ) -> ContextRecord:
        if not tenant_id or not conversation_id:
            raise ContextScopeError("Conversation context requires tenant and conversation")
        if private and not user_id:
            raise ContextScopeError("Private conversation context requires a linked user")
        row = ContextRecord(
            scope_type="conversation",
            visibility="private" if private else "shared",
            tenant_id=tenant_id,
            owner_user_id=user_id if private else None,
            conversation_id=conversation_id,
            kind=kind[:50] or "fact",
            content=cls._clean(content),
            channel_provider=channel_provider,
            channel_space_id=channel_space_id,
            source_message_id=source_message_id,
            metadata_json=json.dumps(metadata or {}, separators=(",", ":")),
        )
        db.add(row)
        await db.flush()
        return row

    @staticmethod
    def _query_terms(query: str) -> list[str]:
        return [
            term.lower()
            for term in " ".join(str(query or "").split()).split(" ")
            if len(term) >= 3
        ][:8]

    @classmethod
    def _ranked_query(cls, statement, query: str):
        terms = cls._query_terms(query)
        if terms:
            statement = statement.where(
                or_(*[ContextRecord.content.ilike(f"%{term}%") for term in terms])
            )
        return statement.order_by(ContextRecord.updated_at.desc())

    @classmethod
    async def search_human(
        cls,
        db: AsyncSession,
        *,
        user_id: str,
        query: str,
        tenant_id: str | None = None,
        limit: int = 12,
    ) -> list[ContextRecord]:
        if not user_id:
            return []
        statement = select(ContextRecord).where(
            ContextRecord.scope_type == "human",
            ContextRecord.visibility == "private",
            ContextRecord.owner_user_id == user_id,
            or_(
                ContextRecord.tenant_id.is_(None),
                ContextRecord.tenant_id == tenant_id,
            )
            if tenant_id
            else ContextRecord.tenant_id.is_(None),
        )
        return (
            await db.scalars(cls._ranked_query(statement, query).limit(min(limit, 30)))
        ).all()

    @classmethod
    async def search_tenant(
        cls,
        db: AsyncSession,
        *,
        tenant_id: str,
        query: str,
        limit: int = 12,
    ) -> list[ContextRecord]:
        statement = select(ContextRecord).where(
            ContextRecord.scope_type == "tenant",
            ContextRecord.visibility == "shared",
            ContextRecord.tenant_id == tenant_id,
        )
        return (
            await db.scalars(cls._ranked_query(statement, query).limit(min(limit, 30)))
        ).all()

    @classmethod
    async def search_conversation(
        cls,
        db: AsyncSession,
        *,
        tenant_id: str,
        conversation_id: str,
        user_id: str | None,
        query: str,
        limit: int = 12,
    ) -> list[ContextRecord]:
        privacy_filter = ContextRecord.visibility == "shared"
        if user_id:
            privacy_filter = or_(
                ContextRecord.visibility == "shared",
                and_(
                    ContextRecord.visibility == "private",
                    ContextRecord.owner_user_id == user_id,
                ),
            )
        statement = select(ContextRecord).where(
            ContextRecord.scope_type == "conversation",
            ContextRecord.tenant_id == tenant_id,
            ContextRecord.conversation_id == conversation_id,
            privacy_filter,
        )
        return (
            await db.scalars(cls._ranked_query(statement, query).limit(min(limit, 30)))
        ).all()

    @staticmethod
    def _merge_preload(
        relevant: list[ContextRecord],
        recent: list[ContextRecord],
        limit: int,
    ) -> list[ContextRecord]:
        rows: list[ContextRecord] = []
        seen: set[str] = set()
        for row in [*relevant, *recent]:
            if row.id in seen:
                continue
            seen.add(row.id)
            rows.append(row)
            if len(rows) >= limit:
                break
        return list(reversed(rows))

    @classmethod
    async def load_for_agent(
        cls,
        db: AsyncSession,
        *,
        tenant_id: str,
        user_id: str | None,
        conversation_id: str,
        allow_tenant_context: bool,
        query: str = "",
        per_scope: int = 8,
    ) -> LoadedContext:
        relevant_limit = max(1, per_scope // 2)
        recent_limit = per_scope

        if user_id:
            human_relevant = await cls.search_human(
                db,
                user_id=user_id,
                tenant_id=tenant_id,
                query=query,
                limit=relevant_limit,
            )
            human_recent = await cls.search_human(
                db,
                user_id=user_id,
                tenant_id=tenant_id,
                query="",
                limit=recent_limit,
            )
            human = cls._merge_preload(human_relevant, human_recent, per_scope)
        else:
            human = []

        if allow_tenant_context:
            tenant_relevant = await cls.search_tenant(
                db,
                tenant_id=tenant_id,
                query=query,
                limit=relevant_limit,
            )
            tenant_recent = await cls.search_tenant(
                db,
                tenant_id=tenant_id,
                query="",
                limit=recent_limit,
            )
            tenant = cls._merge_preload(tenant_relevant, tenant_recent, per_scope)
        else:
            tenant = []

        conversation_relevant = await cls.search_conversation(
            db,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            user_id=user_id,
            query=query,
            limit=relevant_limit,
        )
        conversation_recent = await cls.search_conversation(
            db,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            user_id=user_id,
            query="",
            limit=recent_limit,
        )
        conversation = cls._merge_preload(
            conversation_relevant,
            conversation_recent,
            per_scope,
        )

        return LoadedContext(
            human=human,
            tenant=tenant,
            conversation=conversation,
        )
