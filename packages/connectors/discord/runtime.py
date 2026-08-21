import asyncio
import os

from packages.connectors.discord import secure_runtime
from packages.connectors.discord.slash_commands import build_tree

_tree = build_tree(secure_runtime.bot)


async def _sync_commands() -> None:
    await secure_runtime.bot.wait_until_ready()
    try:
        await _tree.sync()
        print("OPERLY Discord application commands synchronized")
    except Exception as error:
        # Command synchronization failure must not take down the text gateway.
        print(f"OPERLY Discord command sync error category: {type(error).__name__}")


async def start_embedded() -> None:
    """Run the workspace-safe Discord gateway on the current application's event loop."""
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN is missing")
    if secure_runtime.bot.is_closed():
        raise RuntimeError("Discord client was already closed and cannot be restarted")
    sync_task = asyncio.create_task(_sync_commands())
    try:
        await secure_runtime.bot.start(token)
    finally:
        if not sync_task.done():
            sync_task.cancel()


async def stop_embedded() -> None:
    if not secure_runtime.bot.is_closed():
        await secure_runtime.bot.close()
