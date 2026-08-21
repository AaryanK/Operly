from sqlalchemy.ext.asyncio import AsyncSession

from packages.channels.identity import IdentityService
from packages.database.channel_models import ChannelInstallation
from packages.security.permissions import resolve_workspace_permissions


class SpaceBindingError(ValueError):
    pass


class ExternalSpaceBindingService:
    @staticmethod
    async def bind(
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
        membership = await IdentityService.membership(
            db,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        if membership is None:
            raise SpaceBindingError("Operly workspace membership is required")
        permissions = await resolve_workspace_permissions(
            db,
            tenant_id=tenant_id,
            role=membership.role,
        )
        if membership.role != "owner" and "workspace:channels:manage" not in permissions:
            raise SpaceBindingError("Operly role cannot manage workspace channels")

        existing = await IdentityService.installation(
            db,
            provider=provider,
            external_space_id=str(external_space_id),
        )
        if existing and existing.tenant_id != tenant_id and not existing.provisional:
            raise SpaceBindingError("External space is already bound to another workspace")
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
        await db.flush()
        return existing

    @staticmethod
    async def unbind(
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
        membership = await IdentityService.membership(
            db,
            user_id=user_id,
            tenant_id=installation.tenant_id,
        )
        if membership is None:
            raise SpaceBindingError("Operly workspace membership is required")
        permissions = await resolve_workspace_permissions(
            db,
            tenant_id=installation.tenant_id,
            role=membership.role,
        )
        if membership.role != "owner" and "workspace:channels:manage" not in permissions:
            raise SpaceBindingError("Operly role cannot manage workspace channels")
        installation.status = "disconnected"
        await db.flush()
