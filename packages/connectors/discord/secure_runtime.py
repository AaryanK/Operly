"""Canonical Discord ingress/egress runtime.

Discord proves platform identity/authority and transports bounded bytes. Operly's
ChannelService, artifact ingress, AgentRuntime and capability firewall own scope,
authorization and execution. There is no second Discord-specific agent or attachment
runtime in this module.
"""
from __future__ import annotations

import os
from urllib.parse import quote

import discord
from sqlalchemy import select

from packages.artifacts.service import ArtifactScope
from packages.business_brain.attachments.formatter import split_discord_text
from packages.channels.attachment_ingress import ingest_channel_attachments
from packages.channels.identity import IdentityService
from packages.channels.linking import IdentityLinkService
from packages.channels.service import ChannelService
from packages.channels.space_bindings import ExternalSpaceBindingService, SpaceBindingError
from packages.connectors.discord.client import addressed_to_operly, bot, clean_prompt, envelope_for
from packages.connectors.discord.transport import collect_discord_attachments, send_discord_response
from packages.database.db import init_db, session_scope
from packages.database.models import Message
from packages.model_runtime import ModelInferenceError


TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
ALWAYS_LISTEN = os.getenv("OPERLY_DISCORD_ALWAYS_LISTEN", "false").lower() == "true"


async def server_tenant(message: discord.Message) -> str | None:
    """Return an existing installation scope without creating or selecting one."""
    if message.guild is None:
        return None
    async with session_scope() as db:
        installation = await IdentityService.installation(
            db,
            provider="discord",
            external_space_id=str(message.guild.id),
        )
        return installation.tenant_id if installation else None


async def store_message(
    message: discord.Message,
    tenant_id: str,
    content: str,
    *,
    is_bot: bool,
) -> None:
    """Persist shared-channel transcript records; authorization lives elsewhere."""
    async with session_scope() as db:
        existing = await db.scalar(select(Message).where(Message.message_id == message.id))
        if existing:
            return
        db.add(
            Message(
                tenant_id=tenant_id,
                guild_id=message.guild.id if message.guild else None,
                channel_id=message.channel.id,
                message_id=message.id,
                author_id=message.author.id,
                author_name=message.author.display_name,
                content=content,
                is_bot=is_bot,
            )
        )


async def send_chunks(message: discord.Message, text: str) -> discord.Message:
    chunks = split_discord_text(text)
    sent = await message.reply(
        chunks[0],
        mention_author=False,
        allowed_mentions=discord.AllowedMentions.none(),
    )
    for chunk in chunks[1:]:
        await message.channel.send(chunk, allowed_mentions=discord.AllowedMentions.none())
    return sent


async def create_channel_link(message: discord.Message) -> None:
    async with session_scope() as db:
        existing = await IdentityService.resolve_external_identity(
            db,
            provider="discord",
            external_user_id=str(message.author.id),
        )
        if existing:
            await message.reply("This Discord account is already linked to an Operly user.", mention_author=False)
            return
        challenge = await IdentityLinkService.create_from_channel(
            db,
            provider="discord",
            external_user_id=str(message.author.id),
            display_name=message.author.display_name,
        )
        await db.commit()
    link = f"{PUBLIC_BASE_URL}/settings?identity_link={quote(challenge.token or '', safe='')}"
    await message.reply(
        "Link this Discord identity to your Operly account here. "
        f"The link expires in 10 minutes:\n{link}",
        mention_author=False,
        allowed_mentions=discord.AllowedMentions.none(),
    )


async def consume_operly_link_code(message: discord.Message, code: str) -> None:
    async with session_scope() as db:
        try:
            await IdentityLinkService.claim_from_channel(
                db,
                provider="discord",
                external_user_id=str(message.author.id),
                code=code,
                display_name=message.author.display_name,
            )
        except ValueError as error:
            await message.reply(str(error), mention_author=False)
            return
        await db.commit()
    await message.reply("Discord is now linked to your Operly identity.", mention_author=False)


async def bind_current_discord_workspace(message: discord.Message, workspace_reference: str) -> None:
    if message.guild is None:
        await message.reply("Run this command inside the Discord server you want to bind.", mention_author=False)
        return
    external_admin = bool(message.author.guild_permissions.manage_guild)
    if not external_admin:
        await message.reply("Discord Manage Server permission is required to bind this server.", mention_author=False)
        return

    reference = " ".join(str(workspace_reference or "").split()).strip().casefold()
    if not reference:
        await message.reply("Use `!operly bind WORKSPACE`.", mention_author=False)
        return

    async with session_scope() as db:
        identity = await IdentityService.resolve_external_identity(
            db,
            provider="discord",
            external_user_id=str(message.author.id),
        )
        if not identity:
            await message.reply("Link your Discord identity first with `!operly link`.", mention_author=False)
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
                f"Could not resolve exactly one workspace. Available: {names}.",
                mention_author=False,
            )
            return
        _, tenant = matches[0]
        try:
            await ExternalSpaceBindingService.bind(
                db,
                provider="discord",
                external_space_id=str(message.guild.id),
                display_name=message.guild.name,
                user_id=identity.user_id,
                tenant_id=tenant.id,
                external_authority_verified=external_admin,
            )
        except SpaceBindingError as error:
            await message.reply(str(error), mention_author=False)
            return
        await db.commit()
    await message.reply(f"Bound this Discord server to `{tenant.name}`.", mention_author=False)


async def handle_operly_command(message: discord.Message) -> bool:
    raw = (message.content or "").strip()
    parts = raw.split()
    if not parts or parts[0].lower() != "!operly":
        return False
    command = parts[1].lower() if len(parts) > 1 else "help"
    if command == "link":
        if len(parts) >= 3:
            await consume_operly_link_code(message, parts[2])
        else:
            await create_channel_link(message)
        return True
    if command == "bind":
        workspace_reference = raw.split(None, 2)[2] if len(parts) >= 3 else ""
        await bind_current_discord_workspace(message, workspace_reference)
        return True
    if command == "claim":
        await message.reply(
            "`!operly claim` is retired. Use Guest Workspace access as-is, or explicitly bind with `!operly bind WORKSPACE`.",
            mention_author=False,
        )
        return True
    await message.reply(
        "Operly commands: `!operly link`, `!operly link CODE`, `!operly bind WORKSPACE`.",
        mention_author=False,
    )
    return True


def _log_channel_error(error: Exception) -> None:
    if isinstance(error, ModelInferenceError):
        print(
            "OPERLY channel-agent model error "
            f"provider={error.provider or 'unknown'} "
            f"model={error.model_id or 'unknown'} "
            f"classification={error.classification or 'unknown'} "
            f"retryable={bool(error.retryable)}"
        )
        return
    print(f"OPERLY channel-agent error category: {type(error).__name__}")


@bot.event
async def on_ready():
    await init_db()
    print(f"OPERLY channel adapter connected as {bot.user}")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if await handle_operly_command(message):
        return

    tenant_id = await server_tenant(message)
    stored_content = message.content or "[attachment]"
    if message.attachments:
        stored_content += " [attachments: " + ", ".join(item.filename for item in message.attachments) + "]"
    if tenant_id:
        await store_message(message, tenant_id, stored_content, is_bot=False)

    if not (ALWAYS_LISTEN or addressed_to_operly(message)):
        return

    prompt = clean_prompt(message)
    envelope = envelope_for(message, prompt)

    try:
        if message.attachments:
            async with session_scope() as db:
                resolved = await ChannelService.resolve(db, envelope)

            if envelope.is_direct:
                if not resolved.user_id:
                    await send_chunks(
                        message,
                        "Link your Discord identity first with `!operly link` before sending private files.",
                    )
                    return
                envelope.attachments = await collect_discord_attachments(message)
            elif resolved.tenant_id and resolved.allow_tenant_context:
                # Guest and full workspaces use the same authenticated connector
                # ingress. No membership-only Discord attachment preprocessor exists.
                envelope.attachments = await collect_discord_attachments(message)
                async with session_scope() as db:
                    await ingest_channel_attachments(
                        db,
                        envelope=envelope,
                        scope=ArtifactScope(
                            "workspace",
                            resolved.tenant_id,
                            tenant_id=resolved.tenant_id,
                        ),
                        created_by=resolved.user_id,
                    )
                    await db.commit()
            # If workspace authority is denied, do not download the bytes. The same
            # ChannelService call below returns the canonical authorization response.

        async with message.channel.typing():
            response = await ChannelService.handle(envelope)

        sent = await send_discord_response(message, response)
        if message.guild is not None and response.tenant_id:
            await store_message(
                sent,
                response.tenant_id,
                response.base_message or response.message,
                is_bot=True,
            )
    except Exception as error:
        _log_channel_error(error)
        await message.reply(
            "The AI request failed after Operly exhausted the available runtime path. Please retry once; the failure details are in the server trace.",
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )


def main() -> None:
    if not TOKEN:
        raise RuntimeError("DISCORD_BOT_TOKEN is missing")
    bot.run(TOKEN)


if __name__ == "__main__":
    main()
