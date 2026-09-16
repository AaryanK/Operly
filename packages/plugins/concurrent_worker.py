from __future__ import annotations

import asyncio
import os

from packages.agent_runtime.worker import PersonalAgentTaskWorker
from packages.database.db import init_db
from packages.plugins.worker import PlatformWorker


class ConcurrentPlatformWorker(PlatformWorker):
    """Bounded-concurrency scheduler for the existing Operly Worker service.

    Platform jobs and durable Personal agent tasks share this process, but keep their
    independent database leases/idempotency contracts. This avoids introducing a
    second Railway queue or scheduler merely for agent work.
    """

    def __init__(self) -> None:
        super().__init__()
        self.concurrency = max(
            1,
            min(int(os.getenv("OPERLY_PLATFORM_WORKER_CONCURRENCY", "4")), 32),
        )
        # Never lease more long-running platform jobs than this process can start
        # immediately. This avoids jobs sitting idle while their lease clock runs.
        self.batch_size = min(self.batch_size, self.concurrency)
        self.personal_agent_tasks = PersonalAgentTaskWorker(
            concurrency=min(
                self.concurrency,
                max(1, int(os.getenv("OPERLY_AGENT_TASK_WORKER_CONCURRENCY", "2"))),
            )
        )

    async def run_once(self) -> int:
        # Durable Personal tasks use their own bounded lease acquisition and sessions.
        # Start that slice concurrently so ordinary plugin jobs are not starved by a
        # long-running Personal capability.
        personal_task = asyncio.create_task(self.personal_agent_tasks.run_once())

        job_ids = await self._lease_jobs()
        if job_ids:
            await asyncio.gather(*(self._process_job(job_id) for job_id in job_ids))

        # Event fanout/delivery remains on the existing conservative serial path.
        # Plugin runtime/build workloads are the expensive jobs that need parallelism.
        event_ids = await self._lease_events()
        for event_id in event_ids:
            await self._fanout_event(event_id)
        delivery_ids = await self._lease_deliveries()
        for delivery_id in delivery_ids:
            await self._process_delivery(delivery_id)

        personal_count = await personal_task
        return len(job_ids) + len(event_ids) + len(delivery_ids) + personal_count

    async def run_forever(self) -> None:
        await init_db()
        print(
            f"Operly Platform Worker started as {self.worker_id} "
            f"with concurrency={self.concurrency}"
        )
        while True:
            processed = await self.run_once()
            if processed == 0:
                await asyncio.sleep(self.poll_seconds)


async def main() -> None:
    enabled = os.getenv("OPERLY_PLATFORM_WORKER_ENABLED", "true").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        print("Operly Platform Worker is disabled")
        return
    await ConcurrentPlatformWorker().run_forever()


if __name__ == "__main__":
    asyncio.run(main())