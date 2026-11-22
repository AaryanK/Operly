"""Compatibility wake transport from DragonZpyder Hatchet to Operly's leased workflow engine.

The native Hatchet worker owns the clock; all workflow definitions, approvals,
permissions, action execution and run history stay governed by Operly until
separately verified and migrated. Never infer completion from dispatch counts.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hmac
import os


class DriverUnavailable(RuntimeError):
    pass


class DriverForbidden(RuntimeError):
    pass


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
        if self._mode != "hatchet":
            raise DriverUnavailable("remote_workflow_driver_inactive")
        expected = _proof_secret()
        if (not expected or not isinstance(proof, str)
                or len(proof) != len(expected) or not proof.isascii()
                or not hmac.compare_digest(expected, proof)):
            raise DriverForbidden("workflow_driver_authentication_failed")
        if self._tick_lock.locked():
            return {"state": "busy", "claimed_runs": 0, "event_runs_queued": 0}
        async with self._tick_lock:
            if self._mode != "hatchet":
                raise DriverUnavailable("remote_workflow_driver_inactive")
            if self.scheduler.status().get("running") or self.events.status().get("running"):
                raise DriverUnavailable("local_workflow_driver_still_running")
            # HA event cursors, DB leases and Kernel authority remain unchanged.
            events = await self.events.tick()
            claims = await self.scheduler.tick()
            self._last_remote_tick = datetime.now(timezone.utc).isoformat()
            if type(events) is not int or not 0 <= events:
                raise DriverUnavailable("invalid_event_dispatcher_count")
            if type(claims) is not int or not 0 <= claims <= 25:
                raise DriverUnavailable("invalid_workflow_claim_count")
            return {"state": "dispatched", "claimed_runs": claims, "event_runs_queued": events}


def _proof_secret() -> str:
    value = os.getenv("OPERLY_HATCHET_WORKFLOW_DISPATCH_SECRET", "")
    return value if 40 <= len(value) <= 512 and value.isascii() else ""


def configured_driver() -> str:
    value = os.getenv("OPERLY_WORKFLOW_DRIVER", "local").strip()
    if value not in {"local", "hatchet"}:
        raise DriverUnavailable("invalid_workflow_dispatch_driver")
    if value == "hatchet" and not _proof_secret():
        raise DriverUnavailable("workflow_handoff_secret_not_configured")
    return value


from packages.workflow import workflow_event_dispatcher, workflow_scheduler

workflow_dispatch_driver = WorkflowDispatchDriver(
    workflow_scheduler, workflow_event_dispatcher,
)
