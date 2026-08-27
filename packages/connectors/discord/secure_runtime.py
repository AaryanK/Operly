"""Workspace-safe Discord event surface for the embedded Operly runtime.

Discord authentication now uses the web OAuth sign-in flow. This module keeps
server binding explicit and routes every Discord message through the canonical
channel runtime without retaining a second identity-pairing mechanism.
"""

import os

import discord

from sqlalchemy import select

from packages.channels.identity import IdentityService
from packages.channels.service import ChannelService
from packages.connectors.discord import bot_shared as legacy
from packages.connectors.discord.artifact_delivery import send_discord_response
from packages.database.channel_models import ChannelInstallation
from packages.database.db import session_scope
from packages.database.models import DiscordGuild
from packages.security.permissions import resolve_workspace_permissions


bot = legacy.bot
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")


async def server_tenant(message: discord.Message) -> str | None:
    if message.guild is None:
        return None
    async with session_scope() as db:
        installation = await IdentityService.installation(
            db,
            provider="discord",
            external_space_id=str(message.guild.id),
        )
        return installation.tenant_id if installation else None


async def direct_discord_sign_in(message: discord.Message) -> None:
    async with session_scope() as db:
        existing = await IdentityService.resolve_external_identity(
            db,
            provider="discord",
            external_user_id=str(message.author.id),
        )
    if existing:
        await message.reply(
            "This Discord account is already connected to your Operly identity. "
            "You can keep talking to Operly here.",
            mention_author=False,
        )
        return
    await message.reply(
        "Discord pairing codes have been retired. Sign in to Operly with Discord here, "
        "then return to this conversation:\n"
        f"{PUBLIC_BASE_URL}/login",
        mention_author=False,
        allowed_mentions=discord.AllowedMentions.none(),
    )


async def bind_current_discord_workspace(
    message: discord.Message,
    workspace_reference: str,
) -> None:
    if message.guild is None:
        await message.reply(
            "Run this command inside the Discord server you want to bind.",
            mention_author=False,
        )
        return
    if not message.author.guild_permissions.manage_guild:
        await message.reply(
            "Discord Manage Server permission is required to bind this server.",
            mention_author=False,
        )
        return

    reference = " ".join(str(workspace_reference or "").split()).strip().casefold()
    if not reference:
        await message.reply(
            "Use `!operly bind WORKSPACE`, for example `!operly bind ANHITRA`.",
            mention_author=False,
        )
        return

    async with session_scope() as db:
        identity = await IdentityService.resolve_external_identity(
            db,
            provider="discord",
            external_user_id=str(message.author.id),
        )
        if not identity:
            await message.reply(
                "Sign in to Operly with Discord first, then run this command again. "
                f"{PUBLIC_BASE_URL}/login",
                mention_author=False,
            )
            return

        memberships = await IdentityService.memberships(db, user_id=identity.user_id)
        matches = [
            (membership, tenant)
            for membership, tenant in memberships
            if tenant.name.casefold() == reference
            or bool(tenant.slug and tenant.slug.casefold() == reference)
        ]
        if len(matches) != 1:
            names = ", ".join(tenant.name for _, tenant in memberships) or "none"
            await message.reply(
                "I could not resolve exactly one workspace by that name. "
                f"Your Operly workspaces: {names}.",
                mention_author=False,
            )
            return

        membership, tenant = matches[0]
        permissions = await resolve_workspace_permissions(
            db,
            tenant_id=tenant.id,
            role=membership.role,
        )
        if membership.role != "owner" and "workspace:channels:manage" not in permissions:
            await message.reply(
                "Your Operly role cannot bind external channels to that workspace.",
                mention_author=False,
            )
            return

        installation = await IdentityService.installation(
            db,
            provider="discord",
            external_space_id=str(message.guild.id),
        )
        if (
            installation is not None
            and installation.tenant_id != tenant.id
            and not installation.provisional
        ):
            await message.reply(
                "This Discord server is already bound to another Operly workspace. "
                "Disconnect it from Operly Settings before rebinding.",
                mention_author=False,
            )
            return

        if installation is None:
            installation = ChannelInstallation(
                tenant_id=tenant.id,
                provider="discord",
                external_space_id=str(message.guild.id),
                display_name=message.guild.name[:200],
                provisional=False,
                status="connected",
                metadata_json="{}",
            )
            db.add(installation)
        else:
            installation.tenant_id = tenant.id
            installation.display_name = message.guild.name[:200]
            installation.provisional = False
            installation.status = "connected"

        legacy_guild = await db.get(DiscordGuild, message.guild.id)
        if legacy_guild is None:
            db.add(
                DiscordGuild(
                    guild_id=message.guild.id,
                    tenant_id=tenant.id,
                    guild_name=message.guild.name[:200],
                    enabled=True,
                )
            )
        else:
            legacy_guild.tenant_id = tenant.id
            legacy_guild.guild_name = message.guild.name[:200]
            legacy_guild.enabled = True

        await db.commit()

    await message.reply(
        f"This Discord server is now bound to the `{tenant.name}` Operly workspace.",
        mention_author=False,
    )


async def handle_operly_command(message: discord.Message) -> bool:
    raw = (message.content or "").strip()
    parts = raw.split()
    if not parts or parts[0].lower() != "!operly":
        return False
    command = parts[1].lower() if len(parts) > 1 else "help"
    if command == "link":
        await direct_discord_sign_in(message)
        return True
    if command == "bind":
        workspace_reference = raw.split(None, 2)[2] if len(parts) >= 3 else ""
        await bind_current_discord_workspace(message, workspace_reference)
        return True
    if command == "claim":
        await message.reply(
            "`!operly claim` no longer creates a workspace from a Discord server. "
            "Create/select the workspace in Operly, then use `!operly bind WORKSPACE`.",
            mention_author=False,
        )
        return True
    await message.reply(
        "Operly commands: `!operly link` (opens Sign in with Discord) and "
        "`!operly bind WORKSPACE`.",
        mention_author=False,
    )
    return True


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    if await handle_operly_command(message):
        return

    tenant_id = await server_tenant(message)
    stored_content = message.content or "[attachment]"
    if message.attachments:
        stored_content += " [attachments: " + ", ".join(
            attachment.filename for attachment in message.attachments
        ) + "]"

    if tenant_id:
        await legacy.store_message(message, tenant_id, stored_content, is_bot=False)

    if not legacy.addressed_to_operly(message):
        return

    prompt = legacy.clean_prompt(message)
    envelope = legacy.envelope_for(message, prompt)
    response = await ChannelService.handle(envelope)
    await send_discord_response(message, response)


async def main() -> None:
    await bot.start(legacy.TOKEN)
