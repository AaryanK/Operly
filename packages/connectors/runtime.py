import asyncio
import os
from dataclasses import dataclass, field
from typing import Awaitable, Callable


@dataclass
class ConnectorRuntime:
    """Own long-lived channel adapters inside the Operly application process.

    The runtime is opt-in so production can switch atomically from a legacy
    dedicated connector worker to the embedded process without running two
    Discord gateway sessions at once.
    """

    mode: str | None = None
    _tasks: list[asyncio.Task] = field(default_factory=list, init=False)
    _stoppers: list[Callable[[], Awaitable[None]]] = field(default_factory=list, init=False)

    @property
    def enabled(self) -> bool:
        value = (self.mode or os.getenv("OPERLY_CONNECTOR_RUNTIME", "off")).strip().lower()
        return value in {"embedded", "on", "true", "1"}

    @staticmethod
    def _report_task_exit(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is None:
            print(f"OPERLY connector task exited: {task.get_name()}")
        else:
            print(
                "OPERLY connector task failed: "
                f"{task.get_name()} ({type(error).__name__}: {error})"
            )

    async def start(self) -> None:
        if not self.enabled:
            print("OPERLY connector runtime disabled")
            return

        if os.getenv("DISCORD_BOT_TOKEN", "").strip():
            from packages.connectors.discord.runtime import start_embedded, stop_embedded

            task = asyncio.create_task(start_embedded(), name="operly-discord-adapter")
            task.add_done_callback(self._report_task_exit)
            self._tasks.append(task)
            self._stoppers.append(stop_embedded)
            await asyncio.sleep(0)
            print("OPERLY embedded connector runtime started: discord")
        else:
            print("OPERLY embedded connector runtime started with no long-lived adapters")

    async def stop(self) -> None:
        for stopper in reversed(self._stoppers):
            try:
                await stopper()
            except Exception as error:
                print(f"OPERLY connector shutdown error: {type(error).__name__}")

        for task in self._tasks:
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._stoppers.clear()


connector_runtime = ConnectorRuntime()
