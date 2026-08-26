"""Deterministic DAG execution for disposable Operly workers.

This runner owns sequencing, bounded parallelism, retries, promotion and completion
state. It never grants authority. Worker outputs are audit data until the exact stage
attempt passes its acceptance validators; only then are artifact/evidence refs promoted
for downstream dependency consumption.
"""
from __future__ import annotations

import asyncio
import inspect
from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Any, Awaitable, Callable, Iterable

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


def _worker_waiting_status(status: str) -> StageStatus | None:
    value = str(status or "").strip().lower()
    if value in {"waiting_approval", "awaiting_approval"}:
        return StageStatus.WAITING_APPROVAL
    if value in {
        "waiting_external",
        "pending_evidence",
        "waiting_external_completion",
        "pending_external",
    }:
        return StageStatus.WAITING_EXTERNAL
    return None


def _stage_status(value: StageStatus | str | None) -> StageStatus:
    if isinstance(value, StageStatus):
        return value
    try:
        return StageStatus(str(value or StageStatus.PENDING.value))
    except ValueError:
        return StageStatus.PENDING


def _ref_set(values: Iterable[Any] | None) -> set[str]:
    return {
        str(item).strip()
        for item in (values or ())
        if str(item).strip()
    }


@dataclass(slots=True)
class StageAttempt:
    stage_id: str
    attempt: int
    result: StageWorkerResult
    defects: list[Defect] = field(default_factory=list)
    source: str = "worker"

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage_id": self.stage_id,
            "attempt": self.attempt,
            "source": self.source,
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
    stage_artifacts: dict[str, set[str]]
    stage_evidence_refs: dict[str, set[str]]
    external_actions: int
    token_usage: int
    cost_usd: float
    completed: bool
    waiting: bool
    blocked: bool
    stop_reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "statuses": {key: value.value for key, value in self.statuses.items()},
            "attempts": [item.as_dict() for item in self.attempts],
            "defects": [item.as_dict() for item in self.defects],
            "artifacts": sorted(self.artifacts),
            "evidence_refs": sorted(self.evidence_refs),
            "stage_artifacts": {
                key: sorted(value) for key, value in sorted(self.stage_artifacts.items())
            },
            "stage_evidence_refs": {
                key: sorted(value)
                for key, value in sorted(self.stage_evidence_refs.items())
            },
            "external_actions": self.external_actions,
            "token_usage": self.token_usage,
            "cost_usd": round(self.cost_usd, 6),
            "completed": self.completed,
            "waiting": self.waiting,
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
    ) -> tuple[list[Defect], set[str]]:
        defects: list[Defect] = []
        validation_evidence: set[str] = set()
        for spec in self._validators_for(stage, contract):
            outcome = dict(await _resolve(self.validator(spec, stage, result)) or {})
            passed = bool(outcome.get("passed"))
            evidence_refs = _ref_set(outcome.get("evidence_refs"))
            validation_evidence.update(evidence_refs)
            await self._event(
                "validator.completed",
                {
                    "stage_id": stage.id,
                    "validator_id": spec.id,
                    "kind": spec.kind.value,
                    "passed": passed,
                    "expected": outcome.get("expected", spec.expected),
                    "observed": outcome.get("observed"),
                    "evidence_refs": sorted(evidence_refs),
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
                    evidence_refs=tuple(sorted(evidence_refs)),
                    failure_class=str(
                        outcome.get("failure_class") or "validation_failed"
                    )[:120],
                    strategy=result.strategy,
                    retryable=bool(outcome.get("retryable", True)),
                    repair_depth=repair_depth,
                )
            )
        return defects, validation_evidence

    async def _run_one(
        self,
        *,
        original_stage: StageSpec,
        contract: AcceptanceContract,
        inherited_context_refs: set[str],
        input_artifacts: set[str],
        facts: dict[str, Any],
        total_attempt_counter: list[int],
        defect_counts: Counter[str],
        resume_result: StageWorkerResult | None = None,
    ) -> tuple[
        StageStatus,
        list[StageAttempt],
        list[Defect],
        set[str],
        set[str],
        int,
        int,
        float,
    ]:
        """Run one station without promoting unverified attempt outputs."""

        stage = original_stage
        attempts: list[StageAttempt] = []
        defects_all: list[Defect] = []
        external_actions = 0
        token_usage = 0
        cost_usd = 0.0
        previous_defect: Defect | None = None
        repair_depth = 0
        pending_resume_result = resume_result

        for attempt_index in range(1, self.budget.max_attempts_per_stage + 1):
            if total_attempt_counter[0] >= self.budget.max_total_attempts:
                return (
                    StageStatus.BLOCKED,
                    attempts,
                    defects_all,
                    set(),
                    set(),
                    external_actions,
                    token_usage,
                    cost_usd,
                )
            total_attempt_counter[0] += 1

            capsule = await self.context_injector.build(
                stage,
                inherited_context_refs=inherited_context_refs,
                artifact_refs=input_artifacts,
                facts=facts,
            )
            source = "resume" if pending_resume_result is not None else "worker"
            await self._event(
                "stage.started",
                {
                    "stage_id": stage.id,
                    "attempt": attempt_index,
                    "source": source,
                    "repair_depth": repair_depth,
                    "context_refs": list(capsule.context_refs),
                    "artifact_refs": list(capsule.artifact_refs),
                    "capability_ids": list(capsule.capability_ids),
                },
            )

            if pending_resume_result is not None:
                result = pending_resume_result
                pending_resume_result = None
                await self._event(
                    "stage.resumed",
                    {
                        "stage_id": stage.id,
                        "attempt": attempt_index,
                        "status": result.status,
                        "strategy": result.strategy,
                        "artifact_refs": list(result.artifacts),
                        "evidence_refs": list(result.evidence_refs),
                    },
                )
            else:
                result = await _resolve(
                    self.worker(stage, capsule, attempt_index, previous_defect)
                )

            if not isinstance(result, StageWorkerResult):
                raise TypeError("Factory worker must return StageWorkerResult")

            external_actions += max(0, int(result.external_actions))
            token_usage += max(0, int(result.token_usage))
            cost_usd += max(0.0, float(result.cost_usd))

            await self._event(
                "stage.attempted",
                {
                    "stage_id": stage.id,
                    "attempt": attempt_index,
                    "source": source,
                    "status": result.status,
                    "strategy": result.strategy,
                    "artifact_refs": list(result.artifacts),
                    "evidence_refs": list(result.evidence_refs),
                    "external_actions": max(0, int(result.external_actions)),
                    "token_usage": max(0, int(result.token_usage)),
                    "cost_usd": max(0.0, float(result.cost_usd)),
                },
            )

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
                attempts.append(
                    StageAttempt(stage.id, attempt_index, result, [defect], source=source)
                )
                defects_all.append(defect)
                await self._event("stage.blocked", defect.as_dict())
                return (
                    StageStatus.BLOCKED,
                    attempts,
                    defects_all,
                    set(),
                    set(),
                    external_actions,
                    token_usage,
                    cost_usd,
                )

            waiting_status = _worker_waiting_status(result.status)
            if waiting_status is not None:
                attempts.append(
                    StageAttempt(stage.id, attempt_index, result, [], source=source)
                )
                await self._event(
                    "stage.waiting",
                    {
                        "stage_id": stage.id,
                        "attempt": attempt_index,
                        "status": waiting_status.value,
                        "strategy": result.strategy,
                        "action_id": result.evidence.get("action_id"),
                        "approval_id": result.evidence.get("approval_id"),
                        "continuation_kind": result.evidence.get("continuation_kind"),
                        "job_id": result.evidence.get("job_id"),
                        "project_id": result.evidence.get("project_id"),
                        "artifact_refs": list(result.artifacts),
                        "evidence_refs": list(result.evidence_refs),
                    },
                )
                return (
                    waiting_status,
                    attempts,
                    defects_all,
                    set(),
                    set(),
                    external_actions,
                    token_usage,
                    cost_usd,
                )

            defects, validator_evidence = await self._validate(
                stage=stage,
                result=result,
                contract=contract,
                repair_depth=repair_depth,
            )
            attempt = StageAttempt(
                stage.id,
                attempt_index,
                result,
                defects,
                source=source,
            )
            attempts.append(attempt)

            if not defects and str(result.status).lower() not in {"failed", "blocked"}:
                promoted_artifacts = _ref_set(result.artifacts)
                promoted_evidence = _ref_set(result.evidence_refs) | validator_evidence
                await self._event(
                    "stage.passed",
                    {
                        "stage_id": stage.id,
                        "attempt": attempt_index,
                        "source": source,
                        "strategy": result.strategy,
                        "artifacts": sorted(promoted_artifacts),
                        "evidence_refs": sorted(promoted_evidence),
                    },
                )
                return (
                    StageStatus.PASSED,
                    attempts,
                    defects_all,
                    promoted_artifacts,
                    promoted_evidence,
                    external_actions,
                    token_usage,
                    cost_usd,
                )

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
                if (
                    defect_counts[defect.fingerprint]
                    >= self.budget.repeated_failure_threshold
                ):
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
                return (
                    StageStatus.BLOCKED,
                    attempts,
                    defects_all,
                    set(),
                    set(),
                    external_actions,
                    token_usage,
                    cost_usd,
                )

            previous_defect = defects[0]
            if self.repair is None or repair_depth >= self.budget.max_repair_depth:
                return (
                    StageStatus.FAILED,
                    attempts,
                    defects_all,
                    set(),
                    set(),
                    external_actions,
                    token_usage,
                    cost_usd,
                )

            repair_depth += 1
            revised = await _resolve(self.repair(stage, previous_defect, repair_depth))
            if revised is None:
                return (
                    StageStatus.FAILED,
                    attempts,
                    defects_all,
                    set(),
                    set(),
                    external_actions,
                    token_usage,
                    cost_usd,
                )
            if revised.id != original_stage.id:
                raise ValueError("Repair planner cannot change a stage identity")
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

        return (
            StageStatus.FAILED,
            attempts,
            defects_all,
            set(),
            set(),
            external_actions,
            token_usage,
            cost_usd,
        )

    @staticmethod
    def _dependency_artifacts(
        stage: StageSpec,
        *,
        initial_artifacts: set[str],
        stage_artifacts: dict[str, set[str]],
        trusted_stage_inputs: dict[str, set[str]],
    ) -> set[str]:
        """Return only root inputs, trusted explicit inputs and dependency outputs."""

        inputs = set(initial_artifacts)
        inputs.update(trusted_stage_inputs.get(stage.id, set()))
        for dependency in stage.dependencies:
            inputs.update(stage_artifacts.get(dependency, set()))
        return inputs

    async def run(
        self,
        *,
        graph: StageGraph,
        acceptance: AcceptanceContract,
        initial_context_refs: set[str] | None = None,
        initial_artifact_refs: set[str] | None = None,
        stage_input_artifact_refs: dict[str, Iterable[str]] | None = None,
        facts: dict[str, Any] | None = None,
        resume_statuses: dict[str, StageStatus | str] | None = None,
        prior_stage_artifacts: dict[str, Iterable[str]] | None = None,
        prior_stage_evidence_refs: dict[str, Iterable[str]] | None = None,
        resume_results: dict[str, StageWorkerResult] | None = None,
    ) -> FactoryExecutionResult:
        statuses = {stage.id: StageStatus.PENDING for stage in graph.stages}
        prior_statuses = {
            str(stage_id): _stage_status(status)
            for stage_id, status in dict(resume_statuses or {}).items()
            if str(stage_id) in statuses
        }
        resume_results = dict(resume_results or {})

        for stage_id in resume_results:
            if stage_id not in statuses:
                raise ValueError(f"Unknown resume stage: {stage_id}")
            if prior_statuses.get(stage_id) not in {
                StageStatus.WAITING_APPROVAL,
                StageStatus.WAITING_EXTERNAL,
            }:
                raise ValueError(
                    f"Resume evidence is only valid for a waiting stage: {stage_id}"
                )

        for stage_id, status in prior_statuses.items():
            if status is StageStatus.PASSED:
                statuses[stage_id] = StageStatus.PASSED
            elif status.waiting:
                statuses[stage_id] = (
                    StageStatus.PENDING if stage_id in resume_results else status
                )
            elif status is StageStatus.RUNNING:
                statuses[stage_id] = StageStatus.PENDING
            elif status in {StageStatus.BLOCKED, StageStatus.FAILED}:
                statuses[stage_id] = status

        attempts: list[StageAttempt] = []
        defects: list[Defect] = []
        context_refs = _ref_set(initial_context_refs)
        initial_artifacts = _ref_set(initial_artifact_refs)
        trusted_stage_inputs = {
            str(stage_id): _ref_set(refs)
            for stage_id, refs in dict(stage_input_artifact_refs or {}).items()
            if str(stage_id) in statuses
        }
        stage_artifacts: dict[str, set[str]] = {}
        stage_evidence_refs: dict[str, set[str]] = {}

        for stage_id, refs in dict(prior_stage_artifacts or {}).items():
            if stage_id in statuses and statuses[stage_id] is StageStatus.PASSED:
                stage_artifacts[stage_id] = _ref_set(refs)
        for stage_id, refs in dict(prior_stage_evidence_refs or {}).items():
            if stage_id in statuses and statuses[stage_id] is StageStatus.PASSED:
                stage_evidence_refs[stage_id] = _ref_set(refs)

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

            if resume_results:
                resumable = [stage for stage in ready if stage.id in resume_results]
                if resumable:
                    ready = resumable
                elif any(status.waiting for status in statuses.values()):
                    break

            first_serial = next(
                (stage for stage in ready if not stage.can_parallelize),
                None,
            )
            batch = (
                [first_serial]
                if first_serial is not None
                else ready[: self.max_parallelism]
            )
            for stage in batch:
                statuses[stage.id] = StageStatus.RUNNING

            results = await asyncio.gather(
                *(
                    self._run_one(
                        original_stage=stage,
                        contract=acceptance,
                        inherited_context_refs=context_refs,
                        input_artifacts=self._dependency_artifacts(
                            stage,
                            initial_artifacts=initial_artifacts,
                            stage_artifacts=stage_artifacts,
                            trusted_stage_inputs=trusted_stage_inputs,
                        ),
                        facts=run_facts,
                        total_attempt_counter=total_attempt_counter,
                        defect_counts=defect_counts,
                        resume_result=resume_results.pop(stage.id, None),
                    )
                    for stage in batch
                )
            )

            for stage, outcome in zip(batch, results):
                (
                    status,
                    stage_attempts,
                    stage_defects,
                    promoted_artifacts,
                    promoted_evidence,
                    external_actions,
                    tokens,
                    cost,
                ) = outcome
                statuses[stage.id] = status
                attempts.extend(stage_attempts)
                defects.extend(stage_defects)
                if status is StageStatus.PASSED:
                    stage_artifacts[stage.id] = set(promoted_artifacts)
                    stage_evidence_refs[stage.id] = set(promoted_evidence)
                else:
                    stage_artifacts.pop(stage.id, None)
                    stage_evidence_refs.pop(stage.id, None)
                total_external_actions += external_actions
                total_tokens += tokens
                total_cost += cost

            if any(status.waiting for status in statuses.values()):
                break

            if any(
                status in {StageStatus.BLOCKED, StageStatus.FAILED}
                for status in statuses.values()
            ):
                failed_ids = {
                    stage_id
                    for stage_id, status in statuses.items()
                    if status in {StageStatus.BLOCKED, StageStatus.FAILED}
                }
                for stage in graph.stages:
                    if (
                        statuses[stage.id] is StageStatus.PENDING
                        and set(stage.dependencies) & failed_ids
                    ):
                        statuses[stage.id] = StageStatus.BLOCKED
                break

        completed = all(
            status is StageStatus.PASSED for status in statuses.values()
        )
        waiting_approval = any(
            status is StageStatus.WAITING_APPROVAL for status in statuses.values()
        )
        waiting_external = any(
            status is StageStatus.WAITING_EXTERNAL for status in statuses.values()
        )
        waiting = waiting_approval or waiting_external
        blocked = any(status is StageStatus.BLOCKED for status in statuses.values())
        if completed:
            stop_reason = "completed"
        elif waiting_approval:
            stop_reason = "waiting_approval"
        elif waiting_external:
            stop_reason = "waiting_external"
        elif blocked:
            stop_reason = "blocked"
        elif any(status is StageStatus.FAILED for status in statuses.values()):
            stop_reason = "failed"
        else:
            stop_reason = "incomplete_graph"

        promoted_artifacts = set(initial_artifacts)
        for refs in stage_artifacts.values():
            promoted_artifacts.update(refs)
        promoted_evidence: set[str] = set()
        for refs in stage_evidence_refs.values():
            promoted_evidence.update(refs)

        event_type = (
            "factory.completed"
            if completed
            else "factory.waiting"
            if waiting
            else "factory.stopped"
        )
        await self._event(
            event_type,
            {
                "completed": completed,
                "waiting": waiting,
                "blocked": blocked,
                "stop_reason": stop_reason,
                "statuses": {key: value.value for key, value in statuses.items()},
                "attempt_count": len(attempts),
                "defect_count": len(defects),
                "stage_artifacts": {
                    key: sorted(value)
                    for key, value in sorted(stage_artifacts.items())
                },
            },
        )
        return FactoryExecutionResult(
            statuses=statuses,
            attempts=attempts,
            defects=defects,
            artifacts=promoted_artifacts,
            evidence_refs=promoted_evidence,
            stage_artifacts=stage_artifacts,
            stage_evidence_refs=stage_evidence_refs,
            external_actions=total_external_actions,
            token_usage=total_tokens,
            cost_usd=total_cost,
            completed=completed,
            waiting=waiting,
            blocked=blocked,
            stop_reason=stop_reason,
        )
