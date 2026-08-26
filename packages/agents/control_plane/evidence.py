"""Append-only evidence bridge for factory control-plane runs.

AgentRunEventRecord already provides the immutable event stream and AgentRunRecord is
its resumable projection. The factory reuses that storage instead of inventing a
second audit ledger. Events are serialized through one lock so parallel stages cannot
race event sequence allocation in the existing checkpoint writer.
"""
from __future__ import annotations

import asyncio
import copy
from typing import Any, Iterable

from packages.agents.persistence import checkpoint_agent_run

from .compiler import FactoryBlueprint
from .stage_runner import FactoryExecutionResult


def _waiting_projection(factory: dict[str, Any]) -> dict[str, dict[str, Any]]:
    value = factory.get("waiting_stages")
    return dict(value) if isinstance(value, dict) else {}


def _refresh_waiting_summary(
    projection: dict[str, Any],
    factory: dict[str, Any],
) -> None:
    waiting = _waiting_projection(factory)
    factory["waiting_stages"] = waiting
    if waiting:
        stage_id = sorted(waiting)[0]
        item = waiting[stage_id]
        factory["waiting_stage_id"] = stage_id
        factory["waiting_status"] = str(item.get("status") or "waiting_external")
    else:
        factory.pop("waiting_stage_id", None)
        factory.pop("waiting_status", None)

    pending = {
        str(item.get("approval_id") or "").strip()
        for item in waiting.values()
        if str(item.get("approval_id") or "").strip()
    }
    projection["pending_approval_ids"] = sorted(pending)


class FactoryEvidenceLedger:
    def __init__(
        self,
        *,
        runtime_run_id: str,
        objective: str,
        metadata: dict[str, Any],
        initial_projection: dict[str, Any] | None = None,
    ) -> None:
        self.runtime_run_id = str(runtime_run_id)
        self.objective = str(objective)
        self.metadata = dict(metadata)
        self._lock = asyncio.Lock()
        if isinstance(initial_projection, dict) and initial_projection:
            self._projection = copy.deepcopy(initial_projection)
            self._projection["objective"] = self.objective
            self._projection.setdefault("artifact_refs", [])
            self._projection.setdefault("evidence_refs", [])
            self._projection.setdefault("pending_approval_ids", [])
            factory = self._projection.setdefault("factory", {})
            factory.setdefault("statuses", {})
            factory.setdefault("artifact_refs", [])
            factory.setdefault("evidence_refs", [])
            factory.setdefault("stage_artifacts", {})
            factory.setdefault("stage_evidence_refs", {})
            factory.setdefault("audit_artifact_refs", [])
            factory.setdefault("audit_evidence_refs", [])
            factory.setdefault("defect_count", 0)
            factory.setdefault("attempt_count", 0)
            _refresh_waiting_summary(self._projection, factory)
        else:
            self._projection: dict[str, Any] = {
                "objective": self.objective,
                "factory": {
                    "state": "initializing",
                    "statuses": {},
                    "artifact_refs": [],
                    "evidence_refs": [],
                    "stage_artifacts": {},
                    "stage_evidence_refs": {},
                    "audit_artifact_refs": [],
                    "audit_evidence_refs": [],
                    "waiting_stages": {},
                    "defect_count": 0,
                    "attempt_count": 0,
                    "external_actions": 0,
                    "token_usage": 0,
                    "cost_usd": 0.0,
                },
                "artifact_refs": [],
                "evidence_refs": [],
                "pending_approval_ids": [],
            }

    async def start(
        self,
        blueprint: FactoryBlueprint,
        *,
        initial_context_refs: Iterable[str] | None = None,
        initial_artifact_refs: Iterable[str] | None = None,
        stage_input_artifact_refs: dict[str, Iterable[str]] | None = None,
    ) -> None:
        async with self._lock:
            trusted_stage_inputs = {
                str(stage_id): sorted(
                    {
                        str(item).strip()
                        for item in refs
                        if str(item).strip()
                    }
                )
                for stage_id, refs in dict(stage_input_artifact_refs or {}).items()
                if str(stage_id).strip()
            }
            self._projection["factory"] = {
                **self._projection["factory"],
                "state": "running",
                "objective_spec": blueprint.objective.as_dict(),
                "acceptance": blueprint.acceptance.as_dict(),
                "graph": blueprint.graph.as_dict(),
                "initial_context_refs": sorted(
                    {
                        str(item).strip()
                        for item in (initial_context_refs or ())
                        if str(item).strip()
                    }
                ),
                "initial_artifact_refs": sorted(
                    {
                        str(item).strip()
                        for item in (initial_artifact_refs or ())
                        if str(item).strip()
                    }
                ),
                "stage_input_artifact_refs": trusted_stage_inputs,
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

    async def resume(
        self,
        blueprint: FactoryBlueprint,
        *,
        stage_id: str,
    ) -> None:
        """Record a scoped resume without recompiling or replacing frozen contracts."""

        async with self._lock:
            factory = self._projection.setdefault("factory", {})
            factory["state"] = "running"
            factory.setdefault("objective_spec", blueprint.objective.as_dict())
            factory.setdefault("acceptance", blueprint.acceptance.as_dict())
            factory.setdefault("graph", blueprint.graph.as_dict())
            await checkpoint_agent_run(
                runtime_run_id=self.runtime_run_id,
                objective=self.objective,
                metadata=self.metadata,
                state=self._projection,
                event_type="factory.resumed",
                lifecycle_state="running",
                payload={
                    "stage_id": str(stage_id),
                    "statuses": dict(factory.get("statuses") or {}),
                },
            )

    async def append(self, event_type: str, payload: dict[str, Any]) -> None:
        if event_type in {"factory.completed", "factory.stopped", "factory.waiting"}:
            return
        async with self._lock:
            factory = self._projection.setdefault("factory", {})
            stage_id = str(payload.get("stage_id") or "").strip()
            if event_type == "stage.started" and stage_id:
                factory.setdefault("statuses", {})[stage_id] = "running"
                factory["attempt_count"] = int(factory.get("attempt_count") or 0) + 1
            elif event_type == "stage.attempted" and stage_id:
                audit_artifacts = set(factory.get("audit_artifact_refs") or [])
                audit_artifacts.update(
                    str(item)
                    for item in (payload.get("artifact_refs") or [])
                    if str(item).strip()
                )
                factory["audit_artifact_refs"] = sorted(audit_artifacts)
                audit_evidence = set(factory.get("audit_evidence_refs") or [])
                audit_evidence.update(
                    str(item)
                    for item in (payload.get("evidence_refs") or [])
                    if str(item).strip()
                )
                factory["audit_evidence_refs"] = sorted(audit_evidence)
            elif event_type == "stage.passed" and stage_id:
                factory.setdefault("statuses", {})[stage_id] = "passed"
                waiting = _waiting_projection(factory)
                waiting.pop(stage_id, None)
                factory["waiting_stages"] = waiting
                _refresh_waiting_summary(self._projection, factory)
            elif event_type == "stage.blocked" and stage_id:
                factory.setdefault("statuses", {})[stage_id] = "blocked"
            elif event_type == "stage.waiting" and stage_id:
                waiting_status = str(payload.get("status") or "waiting_external")
                factory.setdefault("statuses", {})[stage_id] = waiting_status
                waiting = _waiting_projection(factory)
                waiting[stage_id] = {
                    "status": waiting_status,
                    "action_id": payload.get("action_id"),
                    "approval_id": payload.get("approval_id"),
                    "continuation_kind": payload.get("continuation_kind"),
                    "job_id": payload.get("job_id"),
                    "project_id": payload.get("project_id"),
                    "artifact_refs": list(payload.get("artifact_refs") or ()),
                    "evidence_refs": list(payload.get("evidence_refs") or ()),
                }
                factory["waiting_stages"] = waiting
                _refresh_waiting_summary(self._projection, factory)
            elif event_type == "defect.created":
                factory["defect_count"] = int(factory.get("defect_count") or 0) + 1

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
            if result.completed:
                lifecycle = "completed"
                factory_state = "completed"
                event_type = "factory.completed"
            elif result.waiting:
                lifecycle = (
                    "waiting_approval"
                    if result.stop_reason == "waiting_approval"
                    else "waiting_external"
                )
                factory_state = lifecycle
                event_type = "factory.waiting"
            else:
                lifecycle = "failed"
                factory_state = "blocked" if result.blocked else "failed"
                event_type = "factory.stopped"

            factory = self._projection.setdefault("factory", {})
            prior_external_actions = int(factory.get("external_actions") or 0)
            prior_token_usage = int(factory.get("token_usage") or 0)
            prior_cost_usd = float(factory.get("cost_usd") or 0.0)
            factory.update(
                {
                    "state": factory_state,
                    "statuses": {
                        key: value.value for key, value in result.statuses.items()
                    },
                    "artifact_refs": sorted(result.artifacts),
                    "evidence_refs": sorted(result.evidence_refs),
                    "stage_artifacts": {
                        key: sorted(value)
                        for key, value in sorted(result.stage_artifacts.items())
                    },
                    "stage_evidence_refs": {
                        key: sorted(value)
                        for key, value in sorted(result.stage_evidence_refs.items())
                    },
                    "stop_reason": result.stop_reason,
                    "external_actions": prior_external_actions + result.external_actions,
                    "token_usage": prior_token_usage + result.token_usage,
                    "cost_usd": prior_cost_usd + result.cost_usd,
                }
            )
            factory["attempt_count"] = int(factory.get("attempt_count") or 0)
            factory["defect_count"] = int(factory.get("defect_count") or 0)

            current_waiting = {
                stage_id: details
                for stage_id, details in _waiting_projection(factory).items()
                if getattr(result.statuses.get(stage_id), "waiting", False)
            }
            factory["waiting_stages"] = current_waiting
            _refresh_waiting_summary(self._projection, factory)

            self._projection["artifact_refs"] = sorted(result.artifacts)
            self._projection["evidence_refs"] = sorted(result.evidence_refs)
            await checkpoint_agent_run(
                runtime_run_id=self.runtime_run_id,
                objective=self.objective,
                metadata=self.metadata,
                state=self._projection,
                event_type=event_type,
                lifecycle_state=lifecycle,
                payload=result.as_dict(),
                error=(
                    None
                    if result.completed or result.waiting
                    else result.stop_reason
                ),
            )
