from __future__ import annotations

import asyncio
import os

from packages.workspace_modules.integrations.discord.client import bot, bot_status


class DiscordBotLifecycle:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None

    @property
    def configured(self) -> bool:
        return bool(os.getenv("DISCORD_BOT_TOKEN", "").strip())

    @property
    def enabled(self) -> bool:
        flag = os.getenv("OPERLY_DISCORD_BOT_ENABLED", "true").strip().lower()
        return self.configured and flag not in {"0", "false", "off", "no"}

    async def start(self) -> None:
        if not self.enabled:
            return
        if self._task is not None and not self._task.done():
            return
        import packages.workspace_modules.integrations.discord.bot  # noqa: F401
        token = os.environ["DISCORD_BOT_TOKEN"].strip()
        if bot.is_closed():
            raise RuntimeError("Discord bot client was closed and cannot be restarted")
        self._task = asyncio.create_task(bot.start(token), name="operly-deterministic-discord-bot")
        await asyncio.sleep(0)

    async def stop(self) -> None:
        task = self._task
        try:
            if not bot.is_closed():
                await bot.close()
        finally:
            if task is not None and not task.done():
                task.cancel()
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
            self._task = None

    def status(self) -> dict:
        result = bot_status()
        result.update({"configured": self.configured, "enabled": self.enabled, "task_running": bool(self._task and not self._task.done())})
        if self._task is not None and self._task.done() and not self._task.cancelled():
            error = self._task.exception()
            result["last_error_type"] = type(error).__name__ if error else None
        else:
            result["last_error_type"] = None
        return result


discord_bot_lifecycle = DiscordBotLifecycle()
