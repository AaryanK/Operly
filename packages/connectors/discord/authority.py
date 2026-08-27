"""Translate live Discord authority into Operly Guest Workspace permissions.

This module is deliberately connector-owned: only the Discord adapter/runtime is
allowed to infer Discord permissions. The language model and arbitrary capability
arguments never participate in this translation.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DiscordAuthority:
    permissions: frozenset[str]
    is_admin: bool = False


async def resolve_discord_authority(metadata: dict) -> DiscordAuthority | None:
    guild_id = str(metadata.get("discord_guild_id") or metadata.get("external_space_id") or "").strip()
    channel_id = str(metadata.get("discord_channel_id") or "").strip()
    user_id = str(metadata.get("discord_user_id") or metadata.get("external_user_id") or "").strip()
    if not guild_id or not channel_id or not user_id:
        return None

    # The canonical connector client is transport state only; authorization remains
    # here and is derived from Discord's live permission state.
    from packages.connectors.discord.client import bot

    try:
        guild = bot.get_guild(int(guild_id))
        if guild is None:
            guild = await bot.fetch_guild(int(guild_id))
        member = guild.get_member(int(user_id))
        if member is None:
            member = await guild.fetch_member(int(user_id))
        channel = bot.get_channel(int(channel_id))
        if channel is None:
            channel = await bot.fetch_channel(int(channel_id))
    except Exception:
        return None

    try:
        channel_permissions = channel.permissions_for(member)
        guild_permissions = member.guild_permissions
    except Exception:
        return None

    allowed: set[str] = set()
    can_view = bool(getattr(channel_permissions, "view_channel", False))
    can_history = bool(getattr(channel_permissions, "read_message_history", False))
    if can_view and can_history:
        allowed.add("discord:read")
    if can_view and bool(getattr(channel_permissions, "send_messages", False)):
        allowed.add("discord:write")
    if can_view and bool(getattr(channel_permissions, "attach_files", False)):
        allowed.add("files:process")

    is_admin = bool(
        getattr(guild_permissions, "administrator", False)
        or getattr(guild_permissions, "manage_guild", False)
    )
    return DiscordAuthority(frozenset(allowed), is_admin=is_admin)
