import json
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.agent_models import AgentConversation
from packages.database.channel_models import ChannelConversationState
from packages.database.models import AppUser
from packages.database.principal_models import ExternalPrincipalBinding, Principal

GUEST_TTL_DAYS = 30


class PrincipalError(ValueError):
    pass


class PrincipalService:
    @staticmethod
    async def user_principal(db: AsyncSession, user_id: str) -> Principal:
        row = await db.scalar(
            select(Principal).where(
                Principal.kind == "user",
                Principal.user_id == user_id,
                Principal.status == "active",
            )
        )
        if row:
            return row
        user = await db.get(AppUser, user_id)
        if user is None or not user.active:
            raise PrincipalError("Operly user is unavailable")
        row = Principal(
            kind="user",
            user_id=user.id,
            display_name=user.display_name,
            status="active",
            metadata_json="{}",
        )
        db.add(row)
        await db.flush()
        return row

    @staticmethod
    async def external_principal(
        db: AsyncSession, *, provider: str, provider_subject: str
    ) -> tuple[Principal, ExternalPrincipalBinding] | None:
        binding = await db.scalar(
            select(ExternalPrincipalBinding).where(
                ExternalPrincipalBinding.provider == provider,
                ExternalPrincipalBinding.provider_subject == str(provider_subject),
            )
        )
        if binding is None:
            return None
        principal = await db.get(Principal, binding.principal_id)
        if principal is None:
            return None
        return principal, binding

    @classmethod
    async def resolve_or_create_guest(
        cls,
        db: AsyncSession,
        *,
        provider: str,
        provider_subject: str,
        display_name: str | None = None,
    ) -> Principal:
        existing = await cls.external_principal(
            db, provider=provider, provider_subject=provider_subject
        )
        if existing:
            principal, _ = existing
            return principal

        principal = Principal(
            kind="guest",
            display_name=display_name,
            status="active",
            expires_at=datetime.utcnow() + timedelta(days=GUEST_TTL_DAYS),
            metadata_json=json.dumps({"origin_provider": provider}, separators=(",", ":")),
        )
        db.add(principal)
        await db.flush()
        db.add(
            ExternalPrincipalBinding(
                principal_id=principal.id,
                provider=provider,
                provider_subject=str(provider_subject),
                display_name=display_name,
                verified=False,
                metadata_json="{}",
            )
        )
        await db.flush()
        return principal

    @classmethod
    async def claim_guest(
        cls,
        db: AsyncSession,
        *,
        guest_principal_id: str,
        user_id: str,
        provider: str,
        provider_subject: str,
    ) -> Principal:
        guest = await db.get(Principal, guest_principal_id)
        if guest is None or guest.kind != "guest" or guest.status != "active":
            raise PrincipalError("Guest session is unavailable")
        user_principal = await cls.user_principal(db, user_id)

        binding = await db.scalar(
            select(ExternalPrincipalBinding).where(
                ExternalPrincipalBinding.provider == provider,
                ExternalPrincipalBinding.provider_subject == str(provider_subject),
            )
        )
        if binding is None or binding.principal_id != guest.id:
            raise PrincipalError("External identity does not belong to this guest")

        binding.principal_id = user_principal.id
        binding.verified = True
        guest.status = "claimed"
        guest.claimed_by_user_id = user_id
        guest.claimed_at = datetime.utcnow()

        # Preserve channel continuity. Workspace authority is not inherited here;
        # the channel resolver will re-resolve real memberships after authentication.
        await db.execute(
            update(ChannelConversationState)
            .where(
                ChannelConversationState.provider == provider,
                ChannelConversationState.external_user_id == str(provider_subject),
            )
            .values(user_id=user_id)
        )

        # Personal/guest conversation IDs use a stable principal string. When a
        # guest is claimed, move those conversations to the authenticated principal.
        await db.execute(
            update(AgentConversation)
            .where(AgentConversation.principal_id == f"guest:{guest.id}")
            .values(principal_id=f"user:{user_id}")
        )
        await db.flush()
        return user_principal
