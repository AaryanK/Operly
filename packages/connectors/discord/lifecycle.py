"""Discord long-lived adapter lifecycle for the universal PluginRuntime."""
from __future__ import annotations

import asyncio
import os

from packages.plugins.runtime import PluginHealthResult


class DiscordPluginLifecycle:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None

    @property
    def enabled(self) -> bool:
        mode = os.getenv("OPERLY_CONNECTOR_RUNTIME", "off").strip().lower()
        return mode in {"embedded", "on", "true", "1"} and bool(
            os.getenv("DISCORD_BOT_TOKEN", "").strip()
        )

    async def install(self, context=None):
        return None

    async def start(self, context=None):
        if not self.enabled:
            return None
        if self._task is not None and not self._task.done():
            return None
        from packages.connectors.discord.runtime import start_embedded

        self._task = asyncio.create_task(
            start_embedded(),
            name="operly-plugin-discord-adapter",
        )
        await asyncio.sleep(0)
        return None

    async def health(self, context=None) -> PluginHealthResult:
        if not self.enabled:
            return PluginHealthResult(
                True,
                "disabled or unconfigured",
                {"enabled": False},
            )
        if self._task is None:
            return PluginHealthResult(
                False,
                "enabled but not started",
                {"enabled": True},
            )
        if self._task.done():
            if self._task.cancelled():
                return PluginHealthResult(False, "gateway task cancelled", {"enabled": True})
            error = self._task.exception()
            if error is not None:
                return PluginHealthResult(
                    False,
                    type(error).__name__,
                    {"enabled": True},
                )
            return PluginHealthResult(False, "gateway task exited", {"enabled": True})
        return PluginHealthResult(True, "gateway task running", {"enabled": True})

    async def stop(self, context=None):
        from packages.connectors.discord.runtime import stop_embedded

        try:
            await stop_embedded()
        finally:
            if self._task is not None and not self._task.done():
                self._task.cancel()
            if self._task is not None:
                await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def uninstall(self, context=None):
        await self.stop(context)


discord_plugin_lifecycle = DiscordPluginLifecycle()
