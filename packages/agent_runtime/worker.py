from __future__ import annotations

import asyncio
import os
from datetime import datetime
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.database.agent_runtime_models import AgentRuntimeRun
from packages.database.db import SessionFactory
from packages.personal_modules.runtime import build_personal_runtime

from .orchestrator import AgentLeaseLost, DurableAgentOrchestrator
from .runtime import AgentRuntimeDisabled, GovernedAgentRuntime


class PersonalAgentTaskWorker:
    """Lease and execute durable Personal agent runs inside Operly's existing worker.

    This is deliberately not a new queue service. Candidate rows live in the existing
    database; ``DurableAgentOrchestrator`` owns lease fencing and authority refresh;
    Kernel remains the only capability executor.
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

    def _orchestrator(self) -> DurableAgentOrchestrator:
        return DurableAgentOrchestrator(
            runtime=GovernedAgentRuntime(kernel=build_personal_runtime()),
            heartbeat_session_factory=self.session_factory,
            lease_seconds=self.lease_seconds,
        )

    async def _process(self, run_id: str) -> bool:
        lease_token = f"{self.worker_id}:{uuid4()}"[:80]
        try:
            async with self.session_factory() as db:
                result = await self._orchestrator().run_once(
                    db,
                    run_id=run_id,
                    lease_token=lease_token,
                )
                return result is not None
        except (AgentRuntimeDisabled, AgentLeaseLost):
            return False
        except Exception as error:
            # A single malformed/recoverable task must never take down the shared
            # Railway Worker process. The durable run/Kernel records remain the source
            # of truth for reconciliation and a later lease recovery.
            print(f"Personal agent task {run_id} worker error: {type(error).__name__}: {error}")
            return False

    async def run_once(self) -> int:
        if not self.enabled:
            return 0
        run_ids = await self._candidate_ids()
        if not run_ids:
            return 0
        results = await asyncio.gather(*(self._process(run_id) for run_id in run_ids))
        return sum(1 for processed in results if processed)
