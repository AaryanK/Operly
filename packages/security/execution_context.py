from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.models import AppUser, Tenant, TenantMember
from packages.security.guest_workspace import resolve_guest_workspace_authority
from packages.security.permissions import resolve_workspace_permissions
from packages.security.surfaces import SurfaceKind, surface_from_legacy_metadata


class ScopeKind(StrEnum):
    """Trusted authority namespace for one Operly operation."""

    PERSONAL = "personal"
    WORKSPACE = "workspace"


# Personal authority is intentionally explicit rather than treating the account owner
# like a workspace owner. Resource/connector resolvers still decide which account-owned
# objects are available; these permissions only describe the operations the private
# account surface may request through governed providers.
PERSONAL_EXECUTION_PERMISSIONS = frozenset(
    {
        "workspace:read",
        "tasks:read",
        "tasks:write",
        "model:invoke",
        "context:human:read",
        "context:human:write",
        # Private artifact/computer operations remain account-scoped and the
        # Agent Computer has no production credentials or outbound network.
        "files:process",
        "computer:execute",
        # Account-owned Google operations. These permissions do not grant access to a
        # Google account by themselves: the scoped connector resolver still requires
        # a live AccountConnector owned by this same user with matching OAuth scopes.
        "messaging:read",
        "messaging:send",
        "messaging:write",
        "messaging:draft",
        "gmail:draft",
        "calendar:read",
        "calendar:write",
    }
)


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    """Application-resolved security principal for one Operly operation.

    ``workspace_id`` is populated only for workspace authority. A Personal operation
    keeps it ``None``; an optional workspace focus is stored separately and never
    becomes authority merely because the UI or conversation selected it.

    Guest Workspaces deliberately remain ``ScopeKind.WORKSPACE`` so events, workflows,
    actions and resource ownership use the same workspace namespace. ``workspace_mode``
    records whether that authority came from a full Operly membership or from a
    provisional external-platform installation.
    """

    workspace_id: str | None
    user_id: str | None
    membership_id: str | None
    role: str
    permissions: frozenset[str]
    channel: str
    surface: SurfaceKind = SurfaceKind.UNKNOWN
    conversation_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    scope_kind: ScopeKind = ScopeKind.WORKSPACE
    focus_workspace_id: str | None = None
    principal_id: str | None = None
    workspace_mode: str = "full"

    @property
    def is_member(self) -> bool:
        return self.membership_id is not None

    @property
    def is_personal(self) -> bool:
        return self.scope_kind is ScopeKind.PERSONAL

    @property
    def is_workspace(self) -> bool:
        return self.scope_kind is ScopeKind.WORKSPACE

    @property
    def is_guest_workspace(self) -> bool:
        return self.is_workspace and self.workspace_mode == "guest"

    @property
    def scope_id(self) -> str | None:
        if self.is_personal:
            return f"personal:{self.user_id}" if self.user_id else None
        return self.workspace_id

    def can(self, permission: str) -> bool:
        # Workspace owner remains the existing root-authority shortcut only for a
        # full workspace membership. Guest admins are always restricted to their
        # explicitly resolved effective permission set.
        return (
            self.is_workspace
            and not self.is_guest_workspace
            and self.role == "owner"
        ) or permission in self.permissions


class ExecutionContextError(PermissionError):
    pass


def _surface_kind(
    *,
    channel: str,
    surface: SurfaceKind | str | None,
    metadata: dict[str, Any] | None,
) -> SurfaceKind:
    value = SurfaceKind.coerce(surface)
    if value is SurfaceKind.UNKNOWN:
        value = surface_from_legacy_metadata(channel, metadata)
    return value


def _external_space_id(channel: str, metadata: dict[str, Any]) -> str | None:
    value = str(metadata.get("external_space_id") or "").strip()
    if value:
        return value
    channel_key = str(channel or "").strip().lower()
    aliases = {
        "discord": "discord_guild_id",
        "slack": "slack_team_id",
        "whatsapp": "whatsapp_group_id",
    }
    key = aliases.get(channel_key)
    if not key:
        return None
    value = str(metadata.get(key) or "").strip()
    return value or None


async def resolve_personal_execution_context(
    db: AsyncSession,
    *,
    user_id: str,
    channel: str,
    surface: SurfaceKind | str | None = None,
    conversation_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    focus_workspace_id: str | None = None,
    permissions: set[str] | frozenset[str] | None = None,
) -> ExecutionContext:
    """Resolve the authenticated person's private authority without inventing a tenant.

    ``focus_workspace_id`` is only a disambiguation/context hint. When present it is
    revalidated against current membership, but it never populates ``workspace_id``
    and never grants workspace permissions. Any actual workspace operation must still
    acquire a separate workspace ``ExecutionContext`` through
    :func:`resolve_execution_context`.
    """

    user = await db.get(AppUser, user_id)
    if user is None or not user.active:
        raise ExecutionContextError("Operly user is unavailable")

    resolved_focus = None
    if focus_workspace_id:
        workspace = await db.get(Tenant, focus_workspace_id)
        membership = (
            await db.scalar(
                select(TenantMember).where(
                    TenantMember.tenant_id == focus_workspace_id,
                    TenantMember.user_id == user_id,
                )
            )
            if workspace is not None
            else None
        )
        if workspace is None or membership is None:
            raise ExecutionContextError("Workspace focus is unavailable")
        resolved_focus = workspace.id

    return ExecutionContext(
        workspace_id=None,
        user_id=user.id,
        membership_id=None,
        role="personal_owner",
        permissions=frozenset(
            PERSONAL_EXECUTION_PERMISSIONS if permissions is None else permissions
        ),
        channel=str(channel or "unknown"),
        surface=_surface_kind(channel=channel, surface=surface, metadata=metadata),
        conversation_id=conversation_id,
        metadata=dict(metadata or {}),
        scope_kind=ScopeKind.PERSONAL,
        focus_workspace_id=resolved_focus,
        principal_id=f"user:{user.id}",
        workspace_mode="personal",
    )


async def resolve_execution_context(
    db: AsyncSession,
    *,
    workspace_id: str,
    user_id: str | None,
    channel: str,
    surface: SurfaceKind | str | None = None,
    conversation_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    require_membership: bool = True,
) -> ExecutionContext:
    """Resolve full-workspace or Guest-Workspace authority from trusted state.

    A full Operly Workspace still requires a current ``TenantMember``. A provisional
    external-platform installation may instead resolve Guest Workspace authority from
    the source space, its trusted adapter permissions, administrator policy and the
    Operly guest ceiling.  A non-member can therefore use a Guest Workspace without
    gaining any authority over a claimed/full workspace.

    Role and permission values are never accepted from the model. Membership, guest
    installation state and policy are revalidated on every execution boundary.
    """
    workspace = await db.get(Tenant, workspace_id)
    if workspace is None:
        raise ExecutionContextError("Workspace is unavailable")

    request_metadata = dict(metadata or {})
    user = await db.get(AppUser, user_id) if user_id else None
    if user_id and (user is None or not user.active):
        raise ExecutionContextError("Operly user is unavailable")

    membership = None
    if user_id:
        membership = await db.scalar(
            select(TenantMember).where(
                TenantMember.tenant_id == workspace_id,
                TenantMember.user_id == user_id,
            )
        )

    role = "guest"
    permissions: set[str] = set()
    principal_id = f"user:{user_id}" if user_id else None
    workspace_mode = "full"

    if membership is not None:
        role = membership.role
        permissions = await resolve_workspace_permissions(
            db,
            tenant_id=workspace_id,
            role=role,
        )
    else:
        external_space_id = _external_space_id(channel, request_metadata)
        guest_principal = str(
            request_metadata.get("_guest_principal_id")
            or request_metadata.get("principal_id")
            or principal_id
            or ""
        ).strip()
        guest = None
        if external_space_id and guest_principal:
            guest = await resolve_guest_workspace_authority(
                db,
                workspace_id=workspace_id,
                provider=channel,
                external_space_id=external_space_id,
                principal_id=guest_principal,
                interaction_metadata=request_metadata,
            )
        if guest is not None:
            role = guest.role
            permissions = set(guest.effective_permissions)
            principal_id = guest.principal_id
            workspace_mode = "guest"
            request_metadata["guest_workspace"] = True
            request_metadata["guest_installation_id"] = guest.installation_id
            request_metadata["guest_platform_permissions"] = sorted(
                guest.platform_permissions
            )
            request_metadata["guest_effective_permissions"] = sorted(
                guest.effective_permissions
            )
        elif require_membership:
            raise ExecutionContextError("User is not a member of this workspace")

    return ExecutionContext(
        workspace_id=workspace_id,
        user_id=user_id,
        membership_id=membership.id if membership is not None else None,
        role=role,
        permissions=frozenset(permissions),
        channel=str(channel or "unknown"),
        surface=_surface_kind(
            channel=channel,
            surface=surface,
            metadata=request_metadata,
        ),
        conversation_id=conversation_id,
        metadata=request_metadata,
        scope_kind=ScopeKind.WORKSPACE,
        focus_workspace_id=workspace_id,
        principal_id=principal_id,
        workspace_mode=workspace_mode,
    )
