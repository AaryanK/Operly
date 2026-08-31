from __future__ import annotations

import json
import os

import discord
from sqlalchemy import select

from packages.database.channel_models import ChannelInstallation, ExternalIdentity
from packages.database.db import session_scope
from packages.database.models import AuthIdentity, Tenant, TenantMember
from packages.security.permissions import resolve_workspace_permissions
from packages.workspace_modules.integrations.discord.client import bot

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")


async def _linked_operly_user_id(discord_user_id: int) -> str | None:
    subject = str(discord_user_id)
    async with session_scope() as db:
        external = await db.scalar(select(ExternalIdentity).where(ExternalIdentity.provider == "discord", ExternalIdentity.provider_subject == subject))
        if external:
            return external.user_id
        auth = await db.scalar(select(AuthIdentity).where(AuthIdentity.provider == "discord", AuthIdentity.provider_subject == subject))
        return auth.user_id if auth else None


async def _installation(message: discord.Message) -> ChannelInstallation | None:
    if message.guild is None:
        return None
    async with session_scope() as db:
        return await db.scalar(select(ChannelInstallation).where(ChannelInstallation.provider == "discord", ChannelInstallation.external_space_id == str(message.guild.id), ChannelInstallation.status == "connected"))


async def _bind_workspace(message: discord.Message, reference: str) -> None:
    if message.guild is None:
        await message.reply("Run `!operly bind WORKSPACE` inside the Discord server.")
        return
    if not bool(message.author.guild_permissions.manage_guild):
        await message.reply("Discord Manage Server permission is required to bind this server.")
        return
    user_id = await _linked_operly_user_id(message.author.id)
    if not user_id:
        await message.reply("Your Discord identity is not linked to an Operly account yet. " f"Open {PUBLIC_BASE_URL}/login and connect/sign in with Discord first.")
        return
    normalized = " ".join(str(reference or "").split()).strip().casefold()
    if not normalized:
        await message.reply("Use `!operly bind WORKSPACE`, for example `!operly bind My Business`.")
        return
    async with session_scope() as db:
        memberships = (await db.execute(select(TenantMember, Tenant).join(Tenant, Tenant.id == TenantMember.tenant_id).where(TenantMember.user_id == user_id))).all()
        matches = [(member, tenant) for member, tenant in memberships if tenant.name.casefold() == normalized or bool(tenant.slug and tenant.slug.casefold() == normalized)]
        if len(matches) != 1:
            names = ", ".join(tenant.name for _, tenant in memberships) or "none"
            await message.reply(f"Could not resolve exactly one workspace. Your Operly workspaces: {names}.")
            return
        member, tenant = matches[0]
        permissions = await resolve_workspace_permissions(db, tenant_id=tenant.id, role=member.role)
        if "workspace:channels:manage" not in permissions:
            await message.reply("Your Operly role does not have permission to bind external channels.")
            return
        row = await db.scalar(select(ChannelInstallation).where(ChannelInstallation.provider == "discord", ChannelInstallation.external_space_id == str(message.guild.id)))
        metadata = json.dumps({"bound_by_user_id": user_id, "discord_guild_id": str(message.guild.id), "source": "deterministic_discord_bot"}, separators=(",", ":"), sort_keys=True)
        if row is None:
            row = ChannelInstallation(tenant_id=tenant.id, provider="discord", external_space_id=str(message.guild.id), display_name=message.guild.name, provisional=False, status="connected", metadata_json=metadata)
            db.add(row)
        else:
            row.tenant_id = tenant.id
            row.display_name = message.guild.name
            row.provisional = False
            row.status = "connected"
            row.metadata_json = metadata
        await db.commit()
    await message.reply(f"This Discord server is now bound to the `{tenant.name}` Operly workspace. Deterministic Discord tools are available; AI chat is still disabled.")


async def _handle_command(message: discord.Message) -> bool:
    raw = (message.content or "").strip()
    if not raw.lower().startswith("!operly"):
        return False
    parts = raw.split()
    command = parts[1].lower() if len(parts) > 1 else "help"
    if command == "help":
        await message.reply("Operly deterministic bot commands: `!operly status`, `!operly link`, `!operly bind WORKSPACE`, `!operly help`. AI chat is not enabled yet.")
        return True
    if command == "status":
        row = await _installation(message)
        if row:
            await message.reply(f"Operly bot is online. This server is bound to workspace `{row.tenant_id}`. AI chat is disabled.")
        else:
            await message.reply("Operly bot is online. This server is not bound to a workspace yet. Use `!operly bind WORKSPACE`. AI chat is disabled.")
        return True
    if command == "link":
        linked = await _linked_operly_user_id(message.author.id)
        if linked:
            await message.reply("This Discord identity is already linked to an Operly account.")
        else:
            await message.reply(f"Open {PUBLIC_BASE_URL}/login and connect/sign in with Discord, then return here.")
        return True
    if command == "bind":
        reference = raw.split(None, 2)[2] if len(parts) >= 3 else ""
        await _bind_workspace(message, reference)
        return True
    await message.reply("Unknown command. Use `!operly help`.")
    return True


def _addressed(message: discord.Message) -> bool:
    if message.guild is None:
        return True
    return bool(bot.user and bot.user in message.mentions)


@bot.event
async def on_ready() -> None:
    print(f"OPERLY deterministic Discord bot connected as {bot.user}")


@bot.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot:
        return
    if await _handle_command(message):
        return
    if _addressed(message):
        await message.reply("Operly's Discord connector is online, but AI chat is intentionally disabled. Use `!operly help` for deterministic commands.", mention_author=False, allowed_mentions=discord.AllowedMentions.none())
