from __future__ import annotations

import discord


def _intents() -> discord.Intents:
    intents = discord.Intents.default()
    intents.guilds = True
    intents.message_content = True
    return intents


bot = discord.Client(intents=_intents())


def bot_status() -> dict:
    user = bot.user
    return {
        "ready": bool(bot.is_ready()),
        "closed": bool(bot.is_closed()),
        "user_id": str(user.id) if user else None,
        "username": str(user) if user else None,
        "guild_count": len(bot.guilds),
    }
