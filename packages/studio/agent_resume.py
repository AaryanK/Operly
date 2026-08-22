"""Recover owner-visible Studio runs after an application restart."""
from __future__ import annotations

from sqlalchemy import select

from packages.database.db import SessionFactory
from packages.database.studio_source_models import StudioAgentRun
from packages.studio.agent_runs import ACTIVE_STATES, launch_run, record_event


async def resume_interrupted_studio_runs() -> int:
    """Requeue persisted active runs whose in-process worker disappeared on restart.

    Railway currently starts a single web process, so replaying the persisted active
    run set gives browser-visible work a real worker again instead of leaving a
    permanent ghost `running` state. The source-version persistence layer remains
    immutable/idempotent at the project level.
    """
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
