import json

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.channels.identity import IdentityService
from packages.database.channel_models import ChannelInstallation
from packages.database.models import TenantMember
from packages.security.permissions import resolve_workspace_permissions


class SpaceBindingError(ValueError):
    pass


class ExternalSpaceBindingService:
    @staticmethod
    async def _can_manage_workspace(
        db: AsyncSession,
        *,
        user_id: str,
        tenant_id: str,
    ) -> bool:
        membership = await IdentityService.membership(
            db,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        if membership is None:
            return False
        permissions = await resolve_workspace_permissions(
            db,
            tenant_id=tenant_id,
            role=membership.role,
        )
        return membership.role == "owner" or "workspace:channels:manage" in permissions

    @staticmethod
    async def _is_reclaimable_legacy_binding(
        db: AsyncSession,
        installation: ChannelInstallation,
    ) -> bool:
        if installation.provisional:
            return True
        try:
            metadata = json.loads(installation.metadata_json or "{}")
        except (TypeError, ValueError):
            metadata = {}
        if not metadata.get("legacy_discord_guild"):
            return False
        members = await db.scalar(
            select(func.count(TenantMember.id)).where(
                TenantMember.tenant_id == installation.tenant_id
            )
        )
        # Old pre-workspace Discord projections sometimes point at an orphan tenant
        # with no human membership. A verified external administrator may reclaim
        # only that orphaned compatibility binding into a workspace they can manage.
        return int(members or 0) == 0

    @classmethod
    async def bind(
        cls,
        db: AsyncSession,
        *,
        provider: str,
        external_space_id: str,
        display_name: str,
        user_id: str,
        tenant_id: str,
        external_authority_verified: bool,
    ) -> ChannelInstallation:
        if not external_authority_verified:
            raise SpaceBindingError("External platform authority is required")
        if not await cls._can_manage_workspace(
            db,
            user_id=user_id,
            tenant_id=tenant_id,
        ):
            raise SpaceBindingError("Operly role cannot manage target workspace channels")

        existing = await IdentityService.installation(
            db,
            provider=provider,
            external_space_id=str(external_space_id),
        )
        if existing and existing.tenant_id != tenant_id and not existing.provisional:
            source_authorized = await cls._can_manage_workspace(
                db,
                user_id=user_id,
                tenant_id=existing.tenant_id,
            )
            reclaimable_legacy = await cls._is_reclaimable_legacy_binding(db, existing)
            if not source_authorized and not reclaimable_legacy:
                raise SpaceBindingError(
                    "External space is already bound to another workspace that you cannot manage"
                )

        if existing is None:
            existing = ChannelInstallation(
                tenant_id=tenant_id,
                provider=provider,
                external_space_id=str(external_space_id),
                display_name=display_name[:200],
                provisional=False,
                status="connected",
                metadata_json="{}",
            )
            db.add(existing)
        else:
            existing.tenant_id = tenant_id
            existing.display_name = display_name[:200]
            existing.provisional = False
            existing.status = "connected"
            try:
                metadata = json.loads(existing.metadata_json or "{}")
            except (TypeError, ValueError):
                metadata = {}
            metadata["rebound_under_workspace_security"] = True
            existing.metadata_json = json.dumps(metadata, separators=(",", ":"))
        await db.flush()
        return existing

    @classmethod
    async def unbind(
        cls,
        db: AsyncSession,
        *,
        provider: str,
        external_space_id: str,
        user_id: str,
        external_authority_verified: bool,
    ) -> None:
        if not external_authority_verified:
            raise SpaceBindingError("External platform authority is required")
        installation = await IdentityService.installation(
            db,
            provider=provider,
            external_space_id=str(external_space_id),
        )
        if installation is None:
            return
        if not await cls._can_manage_workspace(
            db,
            user_id=user_id,
            tenant_id=installation.tenant_id,
        ):
            raise SpaceBindingError("Operly role cannot manage workspace channels")
        installation.status = "disconnected"
        await db.flush()
