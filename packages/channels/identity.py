import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.channel_models import (
    ChannelConversationState,
    ChannelInstallation,
    ExternalIdentity,
)
from packages.database.models import AppUser, DiscordGuild, Tenant, TenantMember


class IdentityLinkConflict(ValueError):
    pass


class IdentityService:
    @staticmethod
    async def resolve_external_identity(
        db: AsyncSession,
        *,
        provider: str,
        external_user_id: str,
    ) -> ExternalIdentity | None:
        return await db.scalar(
            select(ExternalIdentity).where(
                ExternalIdentity.provider == provider,
                ExternalIdentity.provider_subject == str(external_user_id),
            )
        )

    @classmethod
    async def link_external_identity(
        cls,
        db: AsyncSession,
        *,
        user_id: str,
        provider: str,
        external_user_id: str,
        display_name: str | None = None,
        metadata: dict | None = None,
    ) -> ExternalIdentity:
        user = await db.get(AppUser, user_id)
        if user is None or not user.active:
            raise IdentityLinkConflict("Operly user is unavailable")

        existing = await cls.resolve_external_identity(
            db,
            provider=provider,
            external_user_id=external_user_id,
        )
        if existing and existing.user_id != user_id:
            raise IdentityLinkConflict(
                "This external account is already linked to another Operly user"
            )

        if existing:
            existing.display_name = display_name or existing.display_name
            existing.metadata_json = json.dumps(metadata or {}, separators=(",", ":"))
            existing.verified_at = datetime.utcnow()
            await db.flush()
            return existing

        row = ExternalIdentity(
            user_id=user_id,
            provider=provider,
            provider_subject=str(external_user_id),
            display_name=display_name,
            metadata_json=json.dumps(metadata or {}, separators=(",", ":")),
            verified_at=datetime.utcnow(),
        )
        db.add(row)
        await db.flush()
        return row

    @staticmethod
    async def membership(
        db: AsyncSession,
        *,
        user_id: str | None,
        tenant_id: str,
    ) -> TenantMember | None:
        if not user_id:
            return None
        return await db.scalar(
            select(TenantMember).where(
                TenantMember.user_id == user_id,
                TenantMember.tenant_id == tenant_id,
            )
        )

    @staticmethod
    async def memberships(
        db: AsyncSession,
        *,
        user_id: str,
    ) -> list[tuple[TenantMember, Tenant]]:
        rows = (
            await db.execute(
                select(TenantMember, Tenant)
                .join(Tenant, Tenant.id == TenantMember.tenant_id)
                .where(TenantMember.user_id == user_id)
                .order_by(Tenant.name)
            )
        ).all()
        return list(rows)

    @staticmethod
    async def installation(
        db: AsyncSession,
        *,
        provider: str,
        external_space_id: str,
    ) -> ChannelInstallation | None:
        return await db.scalar(
            select(ChannelInstallation).where(
                ChannelInstallation.provider == provider,
                ChannelInstallation.external_space_id == str(external_space_id),
                ChannelInstallation.status == "connected",
            )
        )

    @classmethod
    async def ensure_installation(
        cls,
        db: AsyncSession,
        *,
        provider: str,
        external_space_id: str,
        display_name: str | None = None,
    ) -> ChannelInstallation:
        provider = str(provider).strip().lower()
        space_id = str(external_space_id).strip()
        existing = await cls.installation(
            db,
            provider=provider,
            external_space_id=space_id,
        )
        if existing:
            return existing

        # Preserve existing Discord guild tenancy while moving to the generic
        # channel-installation contract.
        if provider == "discord":
            try:
                legacy = await db.get(DiscordGuild, int(space_id))
            except ValueError:
                legacy = None
            if legacy:
                row = ChannelInstallation(
                    tenant_id=legacy.tenant_id,
                    provider=provider,
                    external_space_id=space_id,
                    display_name=display_name or legacy.guild_name,
                    provisional=False,
                    metadata_json=json.dumps(
                        {"legacy_discord_guild": True},
                        separators=(",", ":"),
                    ),
                )
                db.add(row)
                await db.flush()
                return row

        tenant = Tenant(
            name=(display_name or f"{provider.title()} workspace")[:200],
        )
        db.add(tenant)
        await db.flush()

        row = ChannelInstallation(
            tenant_id=tenant.id,
            provider=provider,
            external_space_id=space_id,
            display_name=(display_name or f"{provider.title()} workspace")[:200],
            provisional=True,
            metadata_json="{}",
        )
        db.add(row)

        # Keep DiscordGuild populated while older UI/reporting surfaces still read it.
        if provider == "discord":
            try:
                guild_id = int(space_id)
            except ValueError:
                guild_id = None
            if guild_id is not None and await db.get(DiscordGuild, guild_id) is None:
                db.add(
                    DiscordGuild(
                        guild_id=guild_id,
                        tenant_id=tenant.id,
                        guild_name=(display_name or f"Discord:{space_id}")[:200],
                    )
                )

        await db.flush()
        return row

    @staticmethod
    async def conversation_state(
        db: AsyncSession,
        *,
        provider: str,
        external_user_id: str,
        external_conversation_id: str,
    ) -> ChannelConversationState | None:
        return await db.scalar(
            select(ChannelConversationState).where(
                ChannelConversationState.provider == provider,
                ChannelConversationState.external_user_id == str(external_user_id),
                ChannelConversationState.external_conversation_id
                == str(external_conversation_id),
            )
        )

    @classmethod
    async def upsert_conversation_state(
        cls,
        db: AsyncSession,
        *,
        provider: str,
        external_user_id: str,
        external_conversation_id: str,
        user_id: str | None,
        active_tenant_id: str | None,
        agent_conversation_id: str | None = None,
        metadata: dict | None = None,
    ) -> ChannelConversationState:
        row = await cls.conversation_state(
            db,
            provider=provider,
            external_user_id=external_user_id,
            external_conversation_id=external_conversation_id,
        )
        if row is None:
            row = ChannelConversationState(
                provider=provider,
                external_user_id=str(external_user_id),
                external_conversation_id=str(external_conversation_id),
            )
            db.add(row)

        row.user_id = user_id
        row.active_tenant_id = active_tenant_id
        if agent_conversation_id:
            row.agent_conversation_id = agent_conversation_id
        if metadata is not None:
            row.metadata_json = json.dumps(metadata, separators=(",", ":"))
        row.last_seen_at = datetime.utcnow()
        await db.flush()
        return row
