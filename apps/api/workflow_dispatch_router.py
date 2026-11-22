"""Shared Hatchet dispatch boundary for existing Operly workflow records.

Hatchet owns the recurring wake. Operly owns business semantics, authority,
approvals, leases, and audit. No LLM, external tool grant, arbitrary run ID, or
business payload is accepted by this operator-only endpoint.

The existing local driver remains the default until a drained, tested cutover.
"""
from __future__ import annotations

import os
import secrets

from fastapi import APIRouter, Header, HTTPException
from sqlalchemy import text

from packages.database.db import DATABASE_URL, engine
from packages.workflow import workflow_event_dispatcher, workflow_scheduler

router = APIRouter(tags=["internal-workflow-dispatch"])
# A dedicated transaction-level lock namespaces the bounded dispatch tick across
# every Operly API replica. Existing workflow/run locks remain authoritative.
_PULSE_LOCK = 914276430193


def workflow_driver() -> str:
    driver = os.getenv("OPERLY_WORKFLOW_DRIVER", "local").strip().lower()
    if driver not in {"local", "hatchet"}:
        raise RuntimeError("Invalid workflow dispatch driver")
    return driver


def _secret() -> str:
    return os.getenv("OPERLY_HATCHET_WORKFLOW_DISPATCH_SECRET", "")


def validate_workflow_driver() -> str:
    driver = workflow_driver()
    if driver == "hatchet":
        if not DATABASE_URL.startswith("postgresql+"):
            raise RuntimeError("External workflow dispatch requires PostgreSQL")
        if len(_secret()) < 40:
            raise RuntimeError("Dedicated Hatchet dispatch secret required")
        if any(_secret() == os.getenv(key, "") for key in (
            "SESSION_SECRET", "AUTH_TOKEN_PEPPER", "OPERLY_MCP_TOKEN_SECRET",
            "OPERLY_DRAGONZPYDER_MCP_SERVICE_SECRET",
            "OPERLY_DRAGONZPYDER_PERSONAL_AGENT_TOKEN",
        )):
            raise RuntimeError("Hatchet dispatch secret must be independent")
    return driver


async def start_workflow_driver() -> str:
    driver = validate_workflow_driver()
    if driver == "local":
        await workflow_scheduler.start()
        await workflow_event_dispatcher.start()
    return driver


async def stop_workflow_driver(driver: str) -> None:
    if driver == "local":
        await workflow_event_dispatcher.stop()
        await workflow_scheduler.stop()


def _authorized(proof: str) -> bool:
    key = _secret()
    return len(key) >= 40 and secrets.compare_digest(key.encode("utf-8"), (proof or "").encode("utf-8"))


@router.get("/api/internal/dragonzpyder/workflow-pulse")
async def workflow_dispatch_readiness(
    proof: str = Header(default="", alias="X-DragonZpyder-Workflow-Dispatch"),
):
    if not _authorized(proof):
        raise HTTPException(401, "Workflow dispatch unauthorized")
    return {
        "driver": validate_workflow_driver(),
        "postgresql": DATABASE_URL.startswith("postgresql+"),
        "local_scheduler_running": workflow_scheduler.status()["running"],
        "local_event_dispatcher_running": workflow_event_dispatcher.status()["running"],
    }


@router.post("/api/internal/dragonzpyder/workflow-pulse")
async def workflow_dispatch_pulse(
    proof: str = Header(default="", alias="X-DragonZpyder-Workflow-Dispatch"),
):
    if not _authorized(proof):
        raise HTTPException(401, "Workflow dispatch unauthorized")
    if validate_workflow_driver() != "hatchet":
        raise HTTPException(409, "Local workflow driver remains active")
    # A mixed-version local process must be drained before enabling the
    # external driver. This guard additionally rejects local activity here.
    if (workflow_scheduler.status()["running"]
            or workflow_event_dispatcher.status()["running"]):
        raise HTTPException(409, "Local workflow loops are still running")
    # Hold an xact advisory lock on a separate connection until the batch is
    # done. An HTTP disconnect or process crash cannot leave a persistent lock.
    # Existing per-run leases protect uncertain side effects independently.
    async with engine.begin() as connection:
        acquired = await connection.scalar(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _PULSE_LOCK},
        )
        if not acquired:
            return {"state": "busy", "claimed_runs": 0, "events_processed": 0}
        events = await workflow_event_dispatcher.tick()
        count = await workflow_scheduler.dispatch_external_batch()
        return {"state": "dispatched", "claimed_runs": count,
                "events_processed": events}
