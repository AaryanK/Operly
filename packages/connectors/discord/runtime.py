import os

from packages.connectors.discord import bot_shared


async def start_embedded() -> None:
    """Run the Discord gateway on the current application's event loop."""
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN is missing")
    if bot_shared.bot.is_closed():
        raise RuntimeError("Discord client was already closed and cannot be restarted")
    await bot_shared.bot.start(token)


async def stop_embedded() -> None:
    if not bot_shared.bot.is_closed():
        await bot_shared.bot.close()
