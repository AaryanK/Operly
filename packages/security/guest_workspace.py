"""Fail-closed authority for provisional external-platform workspaces.

A provisional ChannelInstallation is Operly's Guest Workspace projection of an
existing Discord/Slack/WhatsApp/etc. space.  It is still a workspace for event,
workflow and capability scoping, but it does not inherit the full Operly workspace
permission universe.

Effective guest authority is the intersection of:
1. what the current source-platform interaction is allowed to do,
2. the guest-workspace administrator policy stored by Operly, and
3. Operly's provider-specific guest ceiling.

The model never participates in this calculation.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.channel_models import ChannelInstallation
from packages.security.permissions import KNOWN_PERMISSIONS


_GUEST_COMMON_BASELINE = frozenset(
    {
        "model:invoke",
        "workspace:read",
        "messages:read",
        "actions:read",
        "tasks:read",
        "tasks:write",
        "context:tenant:read",
        "context:conversation:read",
        "context:conversation:write",
    }
)

_PROVIDER_BASELINES: dict[str, frozenset[str]] = {
    "discord": _GUEST_COMMON_BASELINE | {"discord:read", "discord:write"},
    "slack": _GUEST_COMMON_BASELINE | {"messaging:read", "messaging:write"},
    "whatsapp": _GUEST_COMMON_BASELINE | {"messaging:read", "messaging:write"},
}

# A Guest Workspace deliberately cannot discover arbitrary CRM, Gmail, Calendar,
# website, software-generation, computer, or connector authority merely because
# those capabilities exist elsewhere in Operly.  A full workspace claim/binding is
# the boundary for those domains.
_GUEST_COMMON_CEILING = frozenset(
    {
        "model:invoke",
        "workspace:read",
        "workspace:settings:manage",
        "workspace:channels:manage",
        "messages:read",
        "actions:read",
        "tasks:read",
        "tasks:write",
        "reminders:write",
        "context:tenant:read",
        "context:tenant:write",
        "context:conversation:read",
        "context:conversation:write",
        "files:process",
    }
)

_PROVIDER_CEILINGS: dict[str, frozenset[str]] = {
    "discord": _GUEST_COMMON_CEILING | {"discord:read", "discord:write"},
    "slack": _GUEST_COMMON_CEILING | {"messaging:read", "messaging:write"},
    "whatsapp": _GUEST_COMMON_CEILING | {"messaging:read", "messaging:write"},
}

_ADMIN_ADDITIONS = frozenset(
    {
        "workspace:settings:manage",
        "workspace:channels:manage",
        "context:tenant:write",
    }
)


def _clean_permissions(values: Iterable[Any] | None) -> set[str]:
    if values is None:
        return set()
    return {
        str(value).strip()
        for value in values
        if str(value).strip() in KNOWN_PERMISSIONS
    }


def _loads(value: str | None) -> dict[str, Any]:
    try:
        payload = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


@dataclass(frozen=True, slots=True)
class GuestWorkspaceAuthority:
    workspace_id: str
    installation_id: str
    provider: str
    external_space_id: str
    principal_id: str
    role: str
    platform_permissions: frozenset[str]
    policy_permissions: frozenset[str]
    effective_permissions: frozenset[str]
    platform_admin: bool = False


async def resolve_guest_workspace_authority(
    db: AsyncSession,
    *,
    workspace_id: str,
    provider: str,
    external_space_id: str,
    principal_id: str,
    interaction_metadata: dict[str, Any] | None = None,
) -> GuestWorkspaceAuthority | None:
    """Resolve one principal's authority in an auto-created Guest Workspace.

    ``_operly_platform_permissions`` and ``_operly_platform_admin`` are trusted
    adapter fields.  When an adapter has not implemented a richer platform mapping
    yet, the provider baseline is intentionally narrow.  The baseline never includes
    file processing; file access therefore requires an explicit trusted platform
    permission and cannot be inferred by the model.
    """
    provider_key = str(provider or "").strip().lower()
    space_id = str(external_space_id or "").strip()
    principal_key = str(principal_id or "").strip()
    if not provider_key or not space_id or not principal_key:
        return None

    installation = await db.scalar(
        select(ChannelInstallation).where(
            ChannelInstallation.tenant_id == workspace_id,
            ChannelInstallation.provider == provider_key,
            ChannelInstallation.external_space_id == space_id,
            ChannelInstallation.status == "connected",
            ChannelInstallation.provisional.is_(True),
        )
    )
    if installation is None:
        return None

    metadata = dict(interaction_metadata or {})
    platform_admin = metadata.get("_operly_platform_admin") is True
    supplied = metadata.get("_operly_platform_permissions")
    if isinstance(supplied, (list, tuple, set, frozenset)):
        platform_permissions = _clean_permissions(supplied)
    else:
        platform_permissions = set(_PROVIDER_BASELINES.get(provider_key, _GUEST_COMMON_BASELINE))

    if platform_admin:
        platform_permissions |= set(_ADMIN_ADDITIONS)

    ceiling = set(_PROVIDER_CEILINGS.get(provider_key, _GUEST_COMMON_CEILING))
    platform_permissions &= ceiling

    installation_metadata = _loads(installation.metadata_json)
    policy = installation_metadata.get("guest_policy")
    policy = policy if isinstance(policy, dict) else {}
    allow = policy.get("allow")
    deny = _clean_permissions(policy.get("deny") if isinstance(policy.get("deny"), list) else [])

    if isinstance(allow, list):
        policy_permissions = _clean_permissions(allow) & ceiling
    else:
        # No explicit allow-list means the platform authority is the maximum; admin
        # policy can still remove individual permissions through deny.
        policy_permissions = set(ceiling)

    effective = (platform_permissions & policy_permissions) - deny
    return GuestWorkspaceAuthority(
        workspace_id=workspace_id,
        installation_id=installation.id,
        provider=provider_key,
        external_space_id=space_id,
        principal_id=principal_key,
        role="guest_admin" if platform_admin else "guest",
        platform_permissions=frozenset(platform_permissions),
        policy_permissions=frozenset(policy_permissions - deny),
        effective_permissions=frozenset(effective),
        platform_admin=platform_admin,
    )


async def set_guest_workspace_policy(
    db: AsyncSession,
    *,
    installation_id: str,
    allow: Iterable[str] | None = None,
    deny: Iterable[str] | None = None,
) -> ChannelInstallation:
    """Persist a Guest Workspace policy after the caller proves admin authority.

    This function intentionally does not decide whether the caller is an admin; the
    ingress/capability boundary must establish that before calling it.  Keeping the
    mutation separate prevents request payloads or models from self-asserting admin.
    """
    installation = await db.get(ChannelInstallation, installation_id)
    if installation is None or not installation.provisional:
        raise LookupError("Guest workspace installation is unavailable")

    metadata = _loads(installation.metadata_json)
    policy: dict[str, Any] = {}
    if allow is not None:
        policy["allow"] = sorted(_clean_permissions(allow))
    policy["deny"] = sorted(_clean_permissions(deny))
    metadata["guest_workspace"] = True
    metadata["guest_policy"] = policy
    installation.metadata_json = json.dumps(metadata, separators=(",", ":"), sort_keys=True)
    await db.flush()
    return installation
