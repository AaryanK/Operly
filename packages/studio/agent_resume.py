"""Recover owner-visible Studio runs after an application restart."""
from __future__ import annotations

from sqlalchemy import select

from packages.database.db import SessionFactory
from packages.database.studio_source_models import StudioAgentRun
from packages.studio.agent_runs import ACTIVE_STATES, launch_run, record_event
from packages.studio.model_latency_policy import apply_studio_model_latency_policy
from packages.studio.runtime_policy import apply_studio_runtime_policy
from packages.studio.terminal_recovery import apply_studio_terminal_recovery


async def resume_interrupted_studio_runs() -> int:
    """Install Studio policy, then requeue persisted active runs after restart."""
    # The shared coding harness remains strict for arbitrary software. Studio gets a
    # website-specific runtime/grounding/edit policy once all shared modules are fully
    # imported, before any new or resumed Studio run can launch. The latency policy is
    # applied after it so Studio's outer model deadline remains longer than the
    # provider request deadline instead of cancelling slow reasoning mid-response.
    apply_studio_runtime_policy()
    apply_studio_model_latency_policy()
    apply_studio_terminal_recovery()

    async with SessionFactory() as db:
        rows = list(
            (
                await db.scalars(
                    select(StudioAgentRun)
                    .where(StudioAgentRun.state.in_(sorted(ACTIVE_STATES)))
                    .order_by(StudioAgentRun.created_at.asc())
                )
            ).all()
        )
        if not rows:
            return 0
        for run in rows:
            run.state = "queued"
            run.started_at = None
            run.completed_at = None
            run.error_message = None
        await db.commit()

    for run in rows:
        await record_event(
            run.id,
            run.tenant_id,
            "resume",
            "Operly restarted while this Studio run was active. Resuming the persisted request with the same project context.",
            detail={"operation": run.operation},
        )
        launch_run(run.id)
    return len(rows)
