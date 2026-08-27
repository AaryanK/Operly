"""Canonical Discord client and envelope helpers.

This module owns only Discord transport state. It does not resolve Operly workspace
membership, permissions, attachments, tasks, or agent execution.
"""
from __future__ import annotations

import discord

from packages.channels.envelope import ChannelEnvelope


intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

bot = discord.Client(intents=intents)


def addressed_to_operly(message: discord.Message) -> bool:
    if message.guild is None:
        return True
    if bot.user and bot.user in message.mentions:
        return True
    if (
        message.reference
        and isinstance(message.reference.resolved, discord.Message)
        and bot.user
        and message.reference.resolved.author.id == bot.user.id
    ):
        return True
    return False


def clean_prompt(message: discord.Message) -> str:
    prompt = message.content or ""
    if bot.user:
        prompt = prompt.replace(f"<@{bot.user.id}>", "")
        prompt = prompt.replace(f"<@!{bot.user.id}>", "")
    return prompt.strip()


def envelope_for(message: discord.Message, prompt: str) -> ChannelEnvelope:
    return ChannelEnvelope(
        provider="discord",
        external_user_id=str(message.author.id),
        external_space_id=str(message.guild.id) if message.guild else None,
        external_conversation_id=str(message.channel.id),
        actor_name=message.author.display_name,
        text=prompt or "Analyze the supplied attachment.",
        space_name=message.guild.name if message.guild else None,
        is_direct=message.guild is None,
        metadata={
            "discord_guild_id": message.guild.id if message.guild else None,
            "discord_channel_id": message.channel.id,
            "discord_user_id": message.author.id,
            "external_message_id": str(message.id),
            "has_attachments": bool(message.attachments),
            "attachment_count": len(message.attachments),
        },
    )
