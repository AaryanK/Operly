from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.channel_models import ChannelInstallation
from packages.kernel.contracts import CapabilitySpec
from packages.security.execution_context import ExecutionContext
from packages.workspace_modules.integrations.discord.client import bot


async def workspace_discord_installations(db: AsyncSession, workspace_id: str) -> list[ChannelInstallation]:
    rows = (await db.scalars(select(ChannelInstallation).where(
        ChannelInstallation.tenant_id == workspace_id,
        ChannelInstallation.provider == "discord",
        ChannelInstallation.status == "connected",
    ))).all()
    return list(rows)


async def installation_for_guild(db: AsyncSession, workspace_id: str, guild_id: int | str) -> ChannelInstallation | None:
    return await db.scalar(select(ChannelInstallation).where(
        ChannelInstallation.tenant_id == workspace_id,
        ChannelInstallation.provider == "discord",
        ChannelInstallation.external_space_id == str(guild_id),
        ChannelInstallation.status == "connected",
    ))


async def authorized_channel(db: AsyncSession, workspace_id: str, channel_id: int | str, *, require: str):
    try:
        channel_int = int(channel_id)
    except (TypeError, ValueError) as error:
        raise ValueError("channel_id must be a Discord snowflake") from error
    channel = bot.get_channel(channel_int)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_int)
        except Exception as error:
            raise LookupError("Discord channel is unavailable to the Operly bot") from error
    guild = getattr(channel, "guild", None)
    if guild is None:
        raise PermissionError("Workspace Discord tools can only target installed server channels")
    installation = await installation_for_guild(db, workspace_id, guild.id)
    if installation is None:
        raise PermissionError("Discord channel does not belong to this Operly workspace")
    member = guild.me
    if member is None and bot.user is not None:
        try:
            member = await guild.fetch_member(bot.user.id)
        except Exception as error:
            raise PermissionError("Could not resolve the Operly bot's Discord authority") from error
    if member is None:
        raise PermissionError("Operly bot is not a member of the installed Discord server")
    permissions = channel.permissions_for(member)
    required = {
        "read": ("view_channel", "read_message_history"),
        "send": ("view_channel", "send_messages"),
        "reaction": ("view_channel", "read_message_history", "add_reactions"),
        "thread": ("view_channel", "send_messages", "create_public_threads"),
    }.get(require)
    if required is None:
        raise ValueError(f"Unknown Discord platform permission check: {require}")
    if not all(bool(getattr(permissions, name, False)) for name in required):
        raise PermissionError("The Operly bot does not have the Discord permissions required for this operation")
    return channel, installation, permissions


class AvailableWorkspaceDiscordProvider:
    def __init__(self):
        from packages.workspace_modules.integrations.discord.provider import WorkspaceDiscordProvider
        self._provider = WorkspaceDiscordProvider()

    async def execute(self, *args, **kwargs):
        return await self._provider.execute(*args, **kwargs)

    async def is_available(self, db: AsyncSession, *, context: ExecutionContext, capability: CapabilitySpec) -> bool:
        if not context.workspace_id:
            return False
        if capability.id in {"discord.bot.status", "discord.installations.list"}:
            return True
        if not bot.is_ready():
            return False
        return bool(await workspace_discord_installations(db, context.workspace_id))
