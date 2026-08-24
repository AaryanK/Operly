from __future__ import annotations

from dataclasses import dataclass

import discord

from packages.tasks.delivery import TaskDeliveryError


def _chunks(text: str, limit: int = 1900) -> list[str]:
    value = str(text or "").strip()
    if not value:
        return []
    output: list[str] = []
    while value:
        if len(value) <= limit:
            output.append(value)
            break
        split_at = value.rfind("\n", 0, limit)
        if split_at < limit // 2:
            split_at = value.rfind(" ", 0, limit)
        if split_at < limit // 2:
            split_at = limit
        output.append(value[:split_at].strip())
        value = value[split_at:].strip()
    return [item for item in output if item]


@dataclass(slots=True)
class DiscordTaskDeliveryAdapter:
    providers: tuple[str, ...] = ("discord",)

    async def deliver(self, target: dict, message: str) -> None:
        from packages.connectors.discord import secure_runtime

        bot = secure_runtime.bot
        if bot.is_closed():
            raise TaskDeliveryError("discord_delivery_client_closed")
        kind = str(target.get("kind") or "channel")
        chunks = _chunks(message)
        if not chunks:
            return
        if kind == "dm":
            raw_user_id = target.get("external_user_id")
            if raw_user_id is None:
                raise TaskDeliveryError("discord_delivery_user_missing")
            user_id = int(raw_user_id)
            user = bot.get_user(user_id) or await bot.fetch_user(user_id)
            for chunk in chunks:
                await user.send(chunk)
            return

        raw_channel_id = target.get("external_conversation_id")
        if raw_channel_id is None:
            raise TaskDeliveryError("discord_delivery_channel_missing")
        channel_id = int(raw_channel_id)
        channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
        for chunk in chunks:
            await channel.send(
                chunk,
                allowed_mentions=discord.AllowedMentions.none(),
            )
