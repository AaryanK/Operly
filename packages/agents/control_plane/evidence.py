"""Append-only evidence bridge for factory control-plane runs.

AgentRunEventRecord already provides the immutable event stream and AgentRunRecord is
its resumable projection.  The factory reuses that storage instead of inventing a
second audit ledger.  Events are serialized through one lock so parallel stages cannot
race event sequence allocation in the existing checkpoint writer.
"""
from __future__ import annotations

import asyncio
from typing import Any

from packages.agents.persistence import checkpoint_agent_run

from .compiler import FactoryBlueprint
from .stage_runner import FactoryExecutionResult


class FactoryEvidenceLedger:
    def __init__(
        self,
        *,
        runtime_run_id: str,
        objective: str,
        metadata: dict[str, Any],
    ) -> None:
        self.runtime_run_id = str(runtime_run_id)
        self.objective = str(objective)
        self.metadata = dict(metadata)
        self._lock = asyncio.Lock()
        self._projection: dict[str, Any] = {
            "objective": self.objective,
            "factory": {
                "state": "initializing",
                "statuses": {},
                "artifact_refs": [],
                "evidence_refs": [],
                "defect_count": 0,
                "attempt_count": 0,
            },
            "artifact_refs": [],
            "evidence_refs": [],
            "pending_approval_ids": [],
        }

    async def start(self, blueprint: FactoryBlueprint) -> None:
        async with self._lock:
            self._projection["factory"] = {
                **self._projection["factory"],
                "state": "running",
                "objective_spec": blueprint.objective.as_dict(),
                "acceptance": blueprint.acceptance.as_dict(),
                "graph": blueprint.graph.as_dict(),
            }
            await checkpoint_agent_run(
                runtime_run_id=self.runtime_run_id,
                objective=self.objective,
                metadata=self.metadata,
                state=self._projection,
                event_type="factory.started",
                lifecycle_state="running",
                payload={
                    "objective_spec": blueprint.objective.as_dict(),
                    "acceptance": blueprint.acceptance.as_dict(),
                    "graph": blueprint.graph.as_dict(),
                },
            )

    async def append(self, event_type: str, payload: dict[str, Any]) -> None:
        async with self._lock:
            factory = self._projection.setdefault("factory", {})
            stage_id = str(payload.get("stage_id") or "").strip()
            if event_type == "stage.started" and stage_id:
                factory.setdefault("statuses", {})[stage_id] = "running"
            elif event_type == "stage.passed" and stage_id:
                factory.setdefault("statuses", {})[stage_id] = "passed"
            elif event_type == "stage.blocked" and stage_id:
                factory.setdefault("statuses", {})[stage_id] = "blocked"
            elif event_type == "defect.created":
                factory["defect_count"] = int(factory.get("defect_count") or 0) + 1
            if event_type == "stage.started":
                factory["attempt_count"] = int(factory.get("attempt_count") or 0) + 1

            await checkpoint_agent_run(
                runtime_run_id=self.runtime_run_id,
                objective=self.objective,
                metadata=self.metadata,
                state=self._projection,
                event_type=event_type,
                lifecycle_state="running",
                payload=dict(payload),
            )

    async def finish(self, result: FactoryExecutionResult) -> None:
        async with self._lock:
            factory = self._projection.setdefault("factory", {})
            factory.update(
                {
                    "state": "completed" if result.completed else "blocked" if result.blocked else "failed",
                    "statuses": {key: value.value for key, value in result.statuses.items()},
                    "artifact_refs": sorted(result.artifacts),
                    "evidence_refs": sorted(result.evidence_refs),
                    "defect_count": len(result.defects),
                    "attempt_count": len(result.attempts),
                    "stop_reason": result.stop_reason,
                    "external_actions": result.external_actions,
                    "token_usage": result.token_usage,
                    "cost_usd": result.cost_usd,
                }
            )
            self._projection["artifact_refs"] = sorted(result.artifacts)
            self._projection["evidence_refs"] = sorted(result.evidence_refs)
            lifecycle = "completed" if result.completed else "failed"
            await checkpoint_agent_run(
                runtime_run_id=self.runtime_run_id,
                objective=self.objective,
                metadata=self.metadata,
                state=self._projection,
                event_type="factory.completed" if result.completed else "factory.stopped",
                lifecycle_state=lifecycle,
                payload=result.as_dict(),
                error=None if result.completed else result.stop_reason,
            )
