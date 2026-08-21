import os

from packages.connectors.discord import secure_runtime


async def start_embedded() -> None:
    """Run the workspace-safe Discord gateway on the current application's event loop."""
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN is missing")
    if secure_runtime.bot.is_closed():
        raise RuntimeError("Discord client was already closed and cannot be restarted")
    await secure_runtime.bot.start(token)


async def stop_embedded() -> None:
    if not secure_runtime.bot.is_closed():
        await secure_runtime.bot.close()
