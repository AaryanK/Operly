"""Trusted surface/audience policy shared by capabilities and context retrieval."""
from __future__ import annotations

from enum import StrEnum
from typing import Mapping, Any


class SurfaceKind(StrEnum):
    """Application-controlled interaction surface.

    UNKNOWN is intentionally restrictive for personal/private data. Callers should
    set a concrete surface at ingress; legacy metadata is only a compatibility bridge
    and never treats a missing value as private.
    """

    UNKNOWN = "unknown"
    PERSONAL_PRIVATE = "personal_private"
    WORKSPACE_SHARED = "workspace_shared"
    WORKSPACE_PRIVATE = "workspace_private"
    DISCORD_DM = "discord_dm"
    DISCORD_GUILD = "discord_guild"
    SYSTEM_TASK = "system_task"

    @classmethod
    def coerce(cls, value: object) -> "SurfaceKind":
        if isinstance(value, cls):
            return value
        clean = str(value or "").strip().lower()
        try:
            return cls(clean)
        except ValueError:
            return cls.UNKNOWN

    @property
    def allows_personal_global(self) -> bool:
        return self in {self.PERSONAL_PRIVATE, self.DISCORD_DM}

    @property
    def allows_personal_workspace(self) -> bool:
        return self in {
            self.PERSONAL_PRIVATE,
            self.DISCORD_DM,
            self.WORKSPACE_PRIVATE,
        }

    @property
    def allows_private_conversation(self) -> bool:
        return self in {
            self.PERSONAL_PRIVATE,
            self.DISCORD_DM,
            self.WORKSPACE_PRIVATE,
        }

    @property
    def is_shared(self) -> bool:
        return self in {self.WORKSPACE_SHARED, self.DISCORD_GUILD}


def surface_from_legacy_metadata(
    channel: str,
    metadata: Mapping[str, Any] | None,
) -> SurfaceKind:
    """Conservative bridge while ingress call sites migrate to explicit surfaces.

    Missing web metadata is UNKNOWN, never private. Discord is safe to derive from
    its connector-provided DM/guild bit. For web, only an explicit direct +
    non-shared pair is accepted as the legacy personal surface.
    """

    data = metadata or {}
    explicit = SurfaceKind.coerce(data.get("_surface_kind"))
    if explicit is not SurfaceKind.UNKNOWN:
        return explicit

    normalized_channel = str(channel or "").strip().lower()
    if normalized_channel == "discord":
        return SurfaceKind.DISCORD_DM if data.get("is_direct") is True else SurfaceKind.DISCORD_GUILD

    if data.get("shared_surface") is True:
        return SurfaceKind.WORKSPACE_SHARED
    if data.get("shared_surface") is False and data.get("is_direct") is True:
        return SurfaceKind.PERSONAL_PRIVATE
    return SurfaceKind.UNKNOWN


def capability_surface_allowed(capability_id: str, surface: SurfaceKind | str) -> bool:
    """Return whether a capability may be visible on this surface.

    This is a visibility/privacy gate only. Workspace permissions, connector scopes,
    approvals, and the canonical firewall remain independently authoritative.
    """

    kind = SurfaceKind.coerce(surface)
    capability = str(capability_id or "").strip().lower()

    if capability.startswith("account.") or capability.startswith("context.human."):
        return kind.allows_personal_global
    if capability.startswith("context.private_workspace_"):
        return kind.allows_personal_workspace
    if capability == "context.conversation.remember_private":
        return kind.allows_private_conversation
    return True
