"""Deterministic DAG execution for disposable Operly workers.

This runner owns sequencing, bounded parallelism, retries and completion state.  It
never grants authority; worker/context callbacks are expected to be backed by the
existing governed capability/context seams.  A worker may propose work, but only the
control plane can mark a stage passed after validators return evidence.
"""
from __future__ import annotations

import asyncio
import inspect
from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Any, Awaitable, Callable

from .contracts import (
    AcceptanceContract,
    ContextCapsule,
    Defect,
    RepairBudget,
    StageGraph,
    StageSpec,
    StageStatus,
    StageWorkerResult,
    ValidatorSpec,
)
from .context_injector import StageContextInjector


Worker = Callable[
    [StageSpec, ContextCapsule, int, Defect | None],
    Awaitable[StageWorkerResult] | StageWorkerResult,
]
Validator = Callable[
    [ValidatorSpec, StageSpec, StageWorkerResult],
    Awaitable[dict[str, Any]] | dict[str, Any],
]
RepairPlanner = Callable[
    [StageSpec, Defect, int],
    Awaitable[StageSpec | None] | StageSpec | None,
]
EventSink = Callable[[str, dict[str, Any]], Awaitable[None] | None]


async def _resolve(value):
    return await value if inspect.isawaitable(value) else value


@dataclass(slots=True)
class StageAttempt:
    stage_id: str
    attempt: int
    result: StageWorkerResult
    defects: list[Defect] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage_id": self.stage_id,
            "attempt": self.attempt,
            "result": self.result.as_dict(),
            "defects": [item.as_dict() for item in self.defects],
        }


@dataclass(slots=True)
class FactoryExecutionResult:
    statuses: dict[str, StageStatus]
    attempts: list[StageAttempt]
    defects: list[Defect]
    artifacts: set[str]
    evidence_refs: set[str]
    external_actions: int
    token_usage: int
    cost_usd: float
    completed: bool
    blocked: bool
    stop_reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "statuses": {key: value.value for key, value in self.statuses.items()},
            "attempts": [item.as_dict() for item in self.attempts],
            "defects": [item.as_dict() for item in self.defects],
            "artifacts": sorted(self.artifacts),
            "evidence_refs": sorted(self.evidence_refs),
            "external_actions": self.external_actions,
            "token_usage": self.token_usage,
            "cost_usd": round(self.cost_usd, 6),
            "completed": self.completed,
            "blocked": self.blocked,
            "stop_reason": self.stop_reason,
        }


class FactoryStageRunner:
    """Execute a stage graph without sharing worker conversations between stations."""

    def __init__(
        self,
        *,
        context_injector: StageContextInjector,
        worker: Worker,
        validator: Validator,
        repair: RepairPlanner | None = None,
        event_sink: EventSink | None = None,
        repair_budget: RepairBudget | None = None,
        max_parallelism: int = 4,
    ) -> None:
        self.context_injector = context_injector
        self.worker = worker
        self.validator = validator
        self.repair = repair
        self.event_sink = event_sink
        self.budget = (repair_budget or RepairBudget()).normalized()
        self.max_parallelism = max(1, min(int(max_parallelism), 16))

    async def _event(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.event_sink is not None:
            await _resolve(self.event_sink(event_type, payload))

    @staticmethod
    def _validators_for(
        stage: StageSpec,
        contract: AcceptanceContract,
    ) -> tuple[ValidatorSpec, ...]:
        if not stage.validation_ids:
            return ()
        wanted = set(stage.validation_ids)
        return tuple(item for item in contract.ordered() if item.id in wanted)

    async def _validate(
        self,
        *,
        stage: StageSpec,
        result: StageWorkerResult,
        contract: AcceptanceContract,
        repair_depth: int,
    ) -> list[Defect]:
        defects: list[Defect] = []
        for spec in self._validators_for(stage, contract):
            outcome = dict(await _resolve(self.validator(spec, stage, result)) or {})
            passed = bool(outcome.get("passed"))
            await self._event(
                "validator.completed",
                {
                    "stage_id": stage.id,
                    "validator_id": spec.id,
                    "kind": spec.kind.value,
                    "passed": passed,
                    "expected": outcome.get("expected", spec.expected),
                    "observed": outcome.get("observed"),
                    "evidence_refs": list(outcome.get("evidence_refs") or ()),
                },
            )
            if passed or not spec.required:
                continue
            defects.append(
                Defect(
                    stage_id=stage.id,
                    validator_id=spec.id,
                    expected=outcome.get("expected", spec.expected),
                    observed=outcome.get("observed", outcome.get("error")),
                    evidence_refs=tuple(str(item) for item in (outcome.get("evidence_refs") or ())),
                    failure_class=str(outcome.get("failure_class") or "validation_failed")[:120],
                    strategy=result.strategy,
                    retryable=bool(outcome.get("retryable", True)),
                    repair_depth=repair_depth,
                )
            )
        return defects

    async def _run_one(
        self,
        *,
        original_stage: StageSpec,
        contract: AcceptanceContract,
        inherited_context_refs: set[str],
        artifacts: set[str],
        facts: dict[str, Any],
        total_attempt_counter: list[int],
        defect_counts: Counter[str],
    ) -> tuple[StageStatus, list[StageAttempt], list[Defect], set[str], set[str], int, int, float]:
        stage = original_stage
        attempts: list[StageAttempt] = []
        defects_all: list[Defect] = []
        stage_artifacts: set[str] = set()
        stage_evidence: set[str] = set()
        external_actions = 0
        token_usage = 0
        cost_usd = 0.0
        previous_defect: Defect | None = None
        repair_depth = 0

        for attempt_index in range(1, self.budget.max_attempts_per_stage + 1):
            if total_attempt_counter[0] >= self.budget.max_total_attempts:
                return StageStatus.BLOCKED, attempts, defects_all, stage_artifacts, stage_evidence, external_actions, token_usage, cost_usd
            total_attempt_counter[0] += 1

            capsule = await self.context_injector.build(
                stage,
                inherited_context_refs=inherited_context_refs,
                artifact_refs=artifacts | stage_artifacts,
                facts=facts,
            )
            await self._event(
                "stage.started",
                {
                    "stage_id": stage.id,
                    "attempt": attempt_index,
                    "repair_depth": repair_depth,
                    "context_refs": list(capsule.context_refs),
                    "artifact_refs": list(capsule.artifact_refs),
                    "capability_ids": list(capsule.capability_ids),
                },
            )
            result = await _resolve(self.worker(stage, capsule, attempt_index, previous_defect))
            if not isinstance(result, StageWorkerResult):
                raise TypeError("Factory worker must return StageWorkerResult")

            stage_artifacts.update(result.artifacts)
            stage_evidence.update(result.evidence_refs)
            external_actions += max(0, int(result.external_actions))
            token_usage += max(0, int(result.token_usage))
            cost_usd += max(0.0, float(result.cost_usd))

            budget_exceeded = (
                external_actions > self.budget.max_external_actions
                or token_usage > self.budget.max_tokens
                or cost_usd > self.budget.max_cost_usd
            )
            if budget_exceeded:
                defect = Defect(
                    stage_id=stage.id,
                    validator_id="factory.repair_budget",
                    expected={
                        "max_external_actions": self.budget.max_external_actions,
                        "max_tokens": self.budget.max_tokens,
                        "max_cost_usd": self.budget.max_cost_usd,
                    },
                    observed={
                        "external_actions": external_actions,
                        "tokens": token_usage,
                        "cost_usd": cost_usd,
                    },
                    evidence_refs=tuple(result.evidence_refs),
                    failure_class="repair_budget_exhausted",
                    strategy=result.strategy,
                    retryable=False,
                    repair_depth=repair_depth,
                )
                attempt = StageAttempt(stage.id, attempt_index, result, [defect])
                attempts.append(attempt)
                defects_all.append(defect)
                await self._event("stage.blocked", defect.as_dict())
                return StageStatus.BLOCKED, attempts, defects_all, stage_artifacts, stage_evidence, external_actions, token_usage, cost_usd

            defects = await self._validate(
                stage=stage,
                result=result,
                contract=contract,
                repair_depth=repair_depth,
            )
            attempt = StageAttempt(stage.id, attempt_index, result, defects)
            attempts.append(attempt)

            if not defects and str(result.status).lower() not in {"failed", "blocked"}:
                await self._event(
                    "stage.passed",
                    {
                        "stage_id": stage.id,
                        "attempt": attempt_index,
                        "strategy": result.strategy,
                        "artifacts": list(result.artifacts),
                        "evidence_refs": list(result.evidence_refs),
                    },
                )
                return StageStatus.PASSED, attempts, defects_all, stage_artifacts, stage_evidence, external_actions, token_usage, cost_usd

            if not defects:
                defects = [
                    Defect(
                        stage_id=stage.id,
                        validator_id="worker.exit_status",
                        expected="successful worker result",
                        observed=result.status,
                        evidence_refs=tuple(result.evidence_refs),
                        failure_class="worker_failed",
                        strategy=result.strategy,
                        retryable=True,
                        repair_depth=repair_depth,
                    )
                ]
                attempt.defects.extend(defects)

            defects_all.extend(defects)
            terminal = next((item for item in defects if not item.retryable), None)
            for defect in defects:
                defect_counts[defect.fingerprint] += 1
                await self._event("defect.created", defect.as_dict())
                if defect_counts[defect.fingerprint] >= self.budget.repeated_failure_threshold:
                    terminal = replace(defect, retryable=False)
                    await self._event(
                        "repair.repeated_failure_blocked",
                        {
                            **terminal.as_dict(),
                            "count": defect_counts[defect.fingerprint],
                        },
                    )
                    break
            if terminal is not None:
                return StageStatus.BLOCKED, attempts, defects_all, stage_artifacts, stage_evidence, external_actions, token_usage, cost_usd

            previous_defect = defects[0]
            if self.repair is None or repair_depth >= self.budget.max_repair_depth:
                return StageStatus.FAILED, attempts, defects_all, stage_artifacts, stage_evidence, external_actions, token_usage, cost_usd

            repair_depth += 1
            revised = await _resolve(self.repair(stage, previous_defect, repair_depth))
            if revised is None:
                return StageStatus.FAILED, attempts, defects_all, stage_artifacts, stage_evidence, external_actions, token_usage, cost_usd
            if revised.id != original_stage.id:
                raise ValueError("Repair planner cannot change a stage identity")
            # Dependencies are factory topology, not something a repair worker may rewrite.
            stage = replace(revised, dependencies=original_stage.dependencies)
            await self._event(
                "repair.planned",
                {
                    "stage_id": stage.id,
                    "repair_depth": repair_depth,
                    "defect": previous_defect.as_dict(),
                    "revised_stage": stage.as_dict(),
                },
            )

        return StageStatus.FAILED, attempts, defects_all, stage_artifacts, stage_evidence, external_actions, token_usage, cost_usd

    async def run(
        self,
        *,
        graph: StageGraph,
        acceptance: AcceptanceContract,
        initial_context_refs: set[str] | None = None,
        initial_artifact_refs: set[str] | None = None,
        facts: dict[str, Any] | None = None,
    ) -> FactoryExecutionResult:
        statuses = {stage.id: StageStatus.PENDING for stage in graph.stages}
        attempts: list[StageAttempt] = []
        defects: list[Defect] = []
        context_refs = set(initial_context_refs or ())
        artifacts = set(initial_artifact_refs or ())
        evidence_refs: set[str] = set()
        total_attempt_counter = [0]
        defect_counts: Counter[str] = Counter()
        total_external_actions = 0
        total_tokens = 0
        total_cost = 0.0
        run_facts = dict(facts or {})

        while True:
            ready = list(graph.ready(statuses))
            if not ready:
                break

            # Parallelism is explicit. Non-parallel stages form a deterministic barrier.
            first_serial = next((stage for stage in ready if not stage.can_parallelize), None)
            batch = [first_serial] if first_serial is not None else ready[: self.max_parallelism]
            for stage in batch:
                statuses[stage.id] = StageStatus.RUNNING

            results = await asyncio.gather(
                *(
                    self._run_one(
                        original_stage=stage,
                        contract=acceptance,
                        inherited_context_refs=context_refs,
                        artifacts=artifacts,
                        facts=run_facts,
                        total_attempt_counter=total_attempt_counter,
                        defect_counts=defect_counts,
                    )
                    for stage in batch
                )
            )

            for stage, outcome in zip(batch, results):
                (
                    status,
                    stage_attempts,
                    stage_defects,
                    stage_artifacts,
                    stage_evidence,
                    external_actions,
                    tokens,
                    cost,
                ) = outcome
                statuses[stage.id] = status
                attempts.extend(stage_attempts)
                defects.extend(stage_defects)
                artifacts.update(stage_artifacts)
                evidence_refs.update(stage_evidence)
                total_external_actions += external_actions
                total_tokens += tokens
                total_cost += cost

            if any(status in {StageStatus.BLOCKED, StageStatus.FAILED} for status in statuses.values()):
                # Dependents of a failed station must never run with missing inputs.
                failed_ids = {
                    stage_id
                    for stage_id, status in statuses.items()
                    if status in {StageStatus.BLOCKED, StageStatus.FAILED}
                }
                for stage in graph.stages:
                    if statuses[stage.id] is StageStatus.PENDING and set(stage.dependencies) & failed_ids:
                        statuses[stage.id] = StageStatus.BLOCKED
                break

        completed = all(status is StageStatus.PASSED for status in statuses.values())
        blocked = any(status is StageStatus.BLOCKED for status in statuses.values())
        if completed:
            stop_reason = "completed"
        elif blocked:
            stop_reason = "blocked"
        elif any(status is StageStatus.FAILED for status in statuses.values()):
            stop_reason = "failed"
        else:
            stop_reason = "incomplete_graph"

        await self._event(
            "factory.completed" if completed else "factory.stopped",
            {
                "completed": completed,
                "blocked": blocked,
                "stop_reason": stop_reason,
                "statuses": {key: value.value for key, value in statuses.items()},
                "attempt_count": len(attempts),
                "defect_count": len(defects),
            },
        )
        return FactoryExecutionResult(
            statuses=statuses,
            attempts=attempts,
            defects=defects,
            artifacts=artifacts,
            evidence_refs=evidence_refs,
            external_actions=total_external_actions,
            token_usage=total_tokens,
            cost_usd=total_cost,
            completed=completed,
            blocked=blocked,
            stop_reason=stop_reason,
        )
