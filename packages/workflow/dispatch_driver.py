"""Reversible handoff of legacy workflow wake ownership to DragonZpyder.

Operly's existing database, Kernel authorization, leases, and worker execution
remain authoritative during the transition. This does not migrate definitions
or declare a dispatched run completed. No schedule is installed at import.
"""
from __future__ import annotations

import asyncio
import hmac
import os
from datetime import datetime, timezone


class DriverUnavailable(RuntimeError):
    pass


class DriverForbidden(RuntimeError):
    pass


class DriverBusy(RuntimeError):
    pass


def configured_driver() -> str:
    value = os.getenv("OPERLY_WORKFLOW_DISPATCH_DRIVER", "local").strip()
    if value not in {"local", "dragonzpyder"}:
        raise DriverUnavailable("invalid_workflow_dispatch_driver")
    if value == "dragonzpyder" and not _proof_secret():
        raise DriverUnavailable("workflow_handoff_secret_not_configured")
    return value


def _proof_secret() -> str:
    value = os.getenv("OPERLY_WORKFLOW_HANDOFF_SECRET", "")
    return value if 40 <= len(value) <= 512 and value.isascii() else ""


class WorkflowDispatchDriver:
    def __init__(self, scheduler, event_dispatcher):
        self.scheduler = scheduler
        self.events = event_dispatcher
        self._mode: str | None = None
        self._tick_lock = asyncio.Lock()
        self._last_remote_tick: str | None = None

    async def start(self) -> None:
        if self._mode is not None:
            return
        mode = configured_driver()
        if mode == "local":
            await self.scheduler.start()
            try:
                await self.events.start()
            except BaseException:
                await self.scheduler.stop()
                raise
        self._mode = mode

    async def stop(self) -> None:
        # Reject new remote ticks before draining the current dispatch operation.
        self._mode = None
        async with self._tick_lock:
            try:
                await self.events.stop()
            finally:
                await self.scheduler.stop()

    def status(self) -> dict:
        return {
            "driver": self._mode or "not_started",
            "legacy_polling_enabled": self._mode == "local",
            "last_remote_tick_utc": self._last_remote_tick,
            "execution_owner": "operly_compatibility_engine",
        }

    async def remote_tick(self, proof: str | None) -> dict:
        if self._mode != "dragonzpyder":
            raise DriverUnavailable("remote_workflow_driver_inactive")
        expected = _proof_secret()
        if (not expected or not isinstance(proof, str)
                or len(proof) != len(expected) or not proof.isascii()
                or not hmac.compare_digest(expected, proof)):
            raise DriverForbidden("workflow_driver_authentication_failed")
        if self._tick_lock.locked():
            raise DriverBusy("workflow_dispatch_in_progress")
        async with self._tick_lock:
            if self._mode != "dragonzpyder":
                raise DriverUnavailable("remote_workflow_driver_inactive")
            if (self.scheduler.status().get("running")
                    or self.events.status().get("running")):
                raise DriverUnavailable("local_workflow_driver_still_running")
            # Existing transactional event cursors and run leases enforce
            # cross-replica deduplication. Do not bypass scope authority.
            queued_events = await self.events.tick()
            await self.scheduler.tick()
            self._last_remote_tick = datetime.now(timezone.utc).isoformat()
            return {
                "driver": "dragonzpyder",
                "dispatch": "accepted",
                "queued_events": int(queued_events),
                "active_workers": int(self.scheduler.status()["active_workers"]),
                "completion_verified": False,
            }


from packages.workflow import workflow_event_dispatcher, workflow_scheduler

workflow_dispatch_driver = WorkflowDispatchDriver(
    workflow_scheduler, workflow_event_dispatcher,
)
