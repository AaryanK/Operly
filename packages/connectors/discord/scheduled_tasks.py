from __future__ import annotations

from typing import Awaitable, Callable

import discord

from packages.tasks.runtime import resume_task_after_approval, run_task_job


Runner = Callable[[str], Awaitable[None]]


async def resolve_workflow_approval(
    db,
    *,
    tenant_id: str,
    approval_id: str,
    approved: bool,
    action_status: str,
) -> int:
    """Compatibility seam for callers that still import the Discord module.

    Durable Task approval state is now channel-agnostic and owned by packages.tasks.
    """
    del tenant_id
    return await resume_task_after_approval(
        db,
        approval_id,
        approved=bool(approved and str(action_status) == "VERIFIED"),
    )


async def run_harness_task_job(
    *,
    bot: discord.Client,
    scheduler,
    job_id: str,
    runner: Runner,
) -> None:
    """Legacy APScheduler entrypoint delegated to the generic Task dispatcher.

    Discord no longer owns Task execution or delivery. Atomic DB claiming inside
    ``run_task_job`` makes this safe while old APScheduler rows drain during migration.
    """
    del bot, scheduler, runner
    await run_task_job(job_id)
