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


async def _sync_durable_wakeups() -> None:
    """Feed DB-created Task wakeups into the existing APScheduler instance.

    Business/plugin events may be emitted in another application process. They only
    persist/update ScheduledJob rows; this adapter periodically asks the existing
    Discord scheduler to discover new pending rows. There is still one scheduler and
    one durable wake-up representation.
    """
    await secure_runtime.bot.wait_until_ready()
    from packages.connectors.discord import bot_shared as legacy

    while not secure_runtime.bot.is_closed():
        try:
            await legacy.schedule_new_pending_jobs()
        except Exception as error:
            print(f"OPERLY durable wakeup sync error category: {type(error).__name__}")
        await asyncio.sleep(5)


async def start_embedded() -> None:
    """Run the workspace-safe Discord gateway on the current application's event loop."""
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN is missing")
    if secure_runtime.bot.is_closed():
        raise RuntimeError("Discord client was already closed and cannot be restarted")
    sync_task = asyncio.create_task(_sync_commands())
    wakeup_task = asyncio.create_task(
        _sync_durable_wakeups(),
        name="operly-durable-task-wakeup-sync",
    )
    try:
        await secure_runtime.bot.start(token)
    finally:
        for task in (sync_task, wakeup_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(sync_task, wakeup_task, return_exceptions=True)


async def stop_embedded() -> None:
    if not secure_runtime.bot.is_closed():
        await secure_runtime.bot.close()
