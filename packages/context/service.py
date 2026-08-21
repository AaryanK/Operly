import json
from dataclasses import dataclass

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.channel_models import ContextRecord
from packages.database.models import AppUser, Tenant, TenantMember
from packages.security.temporal_context import resolve_temporal_context


@dataclass(slots=True)
class LoadedContext:
    human: list[ContextRecord]
    tenant: list[ContextRecord]
    conversation: list[ContextRecord]
    principal_name: str | None = None
    workspace_name: str | None = None
    workspace_role: str | None = None
    tenant_context_authorized: bool = False
    temporal_prompt: str | None = None

    def as_prompt(self) -> str:
        sections: list[str] = []
        if self.workspace_name:
            sections.append(
                "CURRENT OPERLY SESSION (application-controlled; not user-provided):\n"
                f"Authenticated actor: {self.principal_name or 'Linked Operly user'}\n"
                f"Workspace: {self.workspace_name}\n"
                f"Workspace role: {self.workspace_role or 'member'}\n"
                f"Workspace context authorized: {'yes' if self.tenant_context_authorized else 'no'}\n"
                "Use these values to resolve words such as I, me, my, we, our, and this workspace. "
                "Do not invent a different identity, role, or workspace."
            )
        if self.temporal_prompt:
            sections.append(self.temporal_prompt)
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
        """Load only query-relevant records plus trusted session identity and time.

        Recent records are deliberately not preloaded. If the model needs broader
        memory or workspace history it must use the authorized context tools,
        keeping retrieval behind the harness instead of prompt stuffing.
        """
        user = await db.get(AppUser, user_id) if user_id else None
        tenant_row = await db.get(Tenant, tenant_id)
        membership = None
        if user_id:
            membership = await db.scalar(
                select(TenantMember).where(
                    TenantMember.user_id == user_id,
                    TenantMember.tenant_id == tenant_id,
                )
            )

        temporal = await resolve_temporal_context(
            db,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        has_query = bool(cls._query_terms(query))
        limit = max(1, min(per_scope, 12))

        if has_query and user_id:
            human = await cls.search_human(
                db,
                user_id=user_id,
                tenant_id=tenant_id,
                query=query,
                limit=limit,
            )
        else:
            human = []

        if has_query and allow_tenant_context:
            tenant = await cls.search_tenant(
                db,
                tenant_id=tenant_id,
                query=query,
                limit=limit,
            )
        else:
            tenant = []

        if has_query:
            conversation = await cls.search_conversation(
                db,
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                user_id=user_id,
                query=query,
                limit=limit,
            )
        else:
            conversation = []

        return LoadedContext(
            human=human,
            tenant=tenant,
            conversation=conversation,
            principal_name=user.display_name if user else None,
            workspace_name=tenant_row.name if tenant_row else None,
            workspace_role=membership.role if membership else None,
            tenant_context_authorized=bool(allow_tenant_context and membership),
            temporal_prompt=temporal.as_prompt(),
        )
