from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from uuid import uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.database.agent_runtime_models import AgentRuntimeRun, AgentRuntimeStep
from packages.database.db import SessionFactory
from packages.personal_modules.invitation_lifecycle import (
    invitation_post_step_gate,
    poll_invitation_wait,
)
from packages.personal_modules.runtime import build_personal_runtime

from .orchestrator import AgentLeaseLost, DurableAgentOrchestrator
from .runtime import AgentRuntimeDisabled, GovernedAgentRuntime


def _requires_future_wait(row: AgentRuntimeRun) -> bool:
    """Read the validated objective semantic persisted at task submission.

    Capability shape alone must never create a future wait. Older tasks without the
    ObjectiveIR marker continue normally instead of being reinterpreted after restart.
    """

    try:
        grants = json.loads(row.grants_reference_json or "{}")
    except (TypeError, ValueError):
        return False
    if not isinstance(grants, dict):
        return False
    objective_ir = grants.get("objective_ir")
    return isinstance(objective_ir, dict) and objective_ir.get("requires_future_wait") is True


async def _personal_post_step_gate(db, row, plan, step, step_result, records):
    if not _requires_future_wait(row):
        return None
    return await invitation_post_step_gate(
        db,
        row,
        plan,
        step,
        step_result,
        records,
    )


class PersonalAgentTaskWorker:
    """Lease and execute durable Personal agent runs inside Operly's existing worker.

    This is deliberately not a new queue service. Candidate rows live in the existing
    database; ``DurableAgentOrchestrator`` owns execution lease fencing and authority
    refresh; Kernel remains the only capability executor. Invitation waits use the same
    shared worker with a separate bounded read-only poll lease.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] = SessionFactory,
        concurrency: int | None = None,
        lease_seconds: int = 300,
        worker_id: str | None = None,
    ) -> None:
        self.session_factory = session_factory
        configured = concurrency or int(os.getenv("OPERLY_AGENT_TASK_WORKER_CONCURRENCY", "2"))
        self.concurrency = max(1, min(int(configured), 16))
        self.lease_seconds = max(30, min(int(lease_seconds), 900))
        self.event_poll_seconds = max(
            30,
            min(int(os.getenv("OPERLY_AGENT_EVENT_POLL_SECONDS", "60")), 900),
        )
        self.worker_id = worker_id or f"agent-worker-{uuid4()}"

    @property
    def enabled(self) -> bool:
        return os.getenv("OPERLY_AGENT_RUNTIME_ENABLED", "0").strip() == "1"

    async def _candidate_ids(self) -> tuple[str, ...]:
        now = datetime.utcnow()
        async with self.session_factory() as db:
            rows = (
                await db.scalars(
                    select(AgentRuntimeRun.id)
                    .where(
                        AgentRuntimeRun.scope_kind == "personal",
                        AgentRuntimeRun.status.in_(("queued", "running")),
                        or_(
                            AgentRuntimeRun.lease_until.is_(None),
                            AgentRuntimeRun.lease_until < now,
                        ),
                    )
                    .order_by(AgentRuntimeRun.created_at)
                    .limit(self.concurrency)
                )
            ).all()
        return tuple(str(row) for row in rows)

    async def _waiting_event_ids(self) -> tuple[str, ...]:
        now = datetime.utcnow()
        async with self.session_factory() as db:
            rows = (
                await db.scalars(
                    select(AgentRuntimeRun.id)
                    .where(
                        AgentRuntimeRun.scope_kind == "personal",
                        AgentRuntimeRun.status == "waiting_event",
                        AgentRuntimeRun.cancellation_requested.is_(False),
                        or_(
                            AgentRuntimeRun.lease_until.is_(None),
                            AgentRuntimeRun.lease_until < now,
                        ),
                    )
                    .order_by(AgentRuntimeRun.updated_at)
                    .limit(self.concurrency)
                )
            ).all()
        return tuple(str(row) for row in rows)

    def _orchestrator(self) -> DurableAgentOrchestrator:
        return DurableAgentOrchestrator(
            runtime=GovernedAgentRuntime(kernel=build_personal_runtime()),
            heartbeat_session_factory=self.session_factory,
            lease_seconds=self.lease_seconds,
            post_step_gate=_personal_post_step_gate,
        )

    async def _persist_checkpoint_version(self, db: AsyncSession, *, run_id: str) -> None:
        attempts = await db.scalar(
            select(func.coalesce(func.sum(AgentRuntimeStep.attempt_count), 0)).where(
                AgentRuntimeStep.agent_run_id == run_id
            )
        )
        row = await db.get(AgentRuntimeRun, run_id)
        if row is None:
            return
        row.checkpoint_version = max(int(row.checkpoint_version or 0), int(attempts or 0))
        row.updated_at = datetime.utcnow()
        await db.commit()

    async def _process(self, run_id: str) -> bool:
        lease_token = f"{self.worker_id}:{uuid4()}"[:80]
        try:
            async with self.session_factory() as db:
                result = await self._orchestrator().run_once(
                    db,
                    run_id=run_id,
                    lease_token=lease_token,
                )
                if result is not None:
                    await self._persist_checkpoint_version(db, run_id=run_id)
                return result is not None
        except (AgentRuntimeDisabled, AgentLeaseLost):
            return False
        except Exception as error:
            # A single malformed/recoverable task must never take down the shared
            # Railway Worker process. The durable run/Kernel records remain the source
            # of truth for reconciliation and a later lease recovery.
            print(f"Personal agent task {run_id} worker error: {type(error).__name__}: {error}")
            return False

    async def _process_waiting_event(self, run_id: str) -> bool:
        lease_token = f"{self.worker_id}:wait:{uuid4()}"[:80]
        try:
            async with self.session_factory() as db:
                return await poll_invitation_wait(
                    db,
                    run_id=run_id,
                    lease_token=lease_token,
                    lease_seconds=min(self.lease_seconds, 180),
                    defer_seconds=self.event_poll_seconds,
                )
        except Exception as error:
            print(f"Personal invitation wait {run_id} worker error: {type(error).__name__}: {error}")
            return False

    async def run_once(self) -> int:
        if not self.enabled:
            return 0
        waiting_ids, run_ids = await asyncio.gather(
            self._waiting_event_ids(),
            self._candidate_ids(),
        )
        jobs = [self._process_waiting_event(run_id) for run_id in waiting_ids]
        jobs.extend(self._process(run_id) for run_id in run_ids)
        if not jobs:
            return 0
        results = await asyncio.gather(*jobs)
        return sum(1 for processed in results if processed)
