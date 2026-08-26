"""Model-neutral contracts for the Operly agent factory control plane.

The control plane owns the root objective, acceptance criteria, stage graph, context
capsules, repair budgets and defects.  Workers are intentionally ephemeral consumers
of these contracts; no worker owns authorization, job state or completion truth.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Iterable


class ValidatorKind(StrEnum):
    DETERMINISTIC = "deterministic"
    PROVIDER = "provider"
    SEMANTIC = "semantic"


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ObjectiveSpec:
    """Immutable description of what the factory is trying to deliver."""

    objective: str
    deliverables: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    required_side_effects: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "deliverables": list(self.deliverables),
            "constraints": list(self.constraints),
            "required_side_effects": list(self.required_side_effects),
        }


@dataclass(frozen=True, slots=True)
class ValidatorSpec:
    """One acceptance check. Deterministic checks always run before semantic checks."""

    id: str
    criterion: str
    kind: ValidatorKind = ValidatorKind.DETERMINISTIC
    validator: str = "evidence_present"
    expected: dict[str, Any] = field(default_factory=dict)
    parameters: dict[str, Any] = field(default_factory=dict)
    required: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "criterion": self.criterion,
            "kind": self.kind.value,
            "validator": self.validator,
            "expected": dict(self.expected),
            "parameters": dict(self.parameters),
            "required": self.required,
        }


@dataclass(frozen=True, slots=True)
class AcceptanceContract:
    """Frozen definition of the evidence required before completion may be claimed."""

    validators: tuple[ValidatorSpec, ...]

    def ordered(self) -> tuple[ValidatorSpec, ...]:
        priority = {
            ValidatorKind.DETERMINISTIC: 0,
            ValidatorKind.PROVIDER: 1,
            ValidatorKind.SEMANTIC: 2,
        }
        return tuple(sorted(self.validators, key=lambda item: (priority[item.kind], item.id)))

    def as_dict(self) -> dict[str, Any]:
        return {"validators": [item.as_dict() for item in self.ordered()]}


@dataclass(frozen=True, slots=True)
class StageSpec:
    """One bounded worker station in the factory graph."""

    id: str
    objective: str
    dependencies: tuple[str, ...] = ()
    context_intents: tuple[str, ...] = ()
    capability_intents: tuple[str, ...] = ()
    input_refs: tuple[str, ...] = ()
    validation_ids: tuple[str, ...] = ()
    assigned_role: str = "business_agent"
    can_parallelize: bool = False
    max_output_chars: int = 12_000

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "objective": self.objective,
            "dependencies": list(self.dependencies),
            "context_intents": list(self.context_intents),
            "capability_intents": list(self.capability_intents),
            "input_refs": list(self.input_refs),
            "validation_ids": list(self.validation_ids),
            "assigned_role": self.assigned_role,
            "can_parallelize": self.can_parallelize,
            "max_output_chars": self.max_output_chars,
        }


@dataclass(frozen=True, slots=True)
class StageGraph:
    stages: tuple[StageSpec, ...]

    def __post_init__(self) -> None:
        ids = [stage.id for stage in self.stages]
        if not ids or any(not item for item in ids):
            raise ValueError("StageGraph requires at least one named stage")
        if len(ids) != len(set(ids)):
            raise ValueError("StageGraph stage IDs must be unique")
        known = set(ids)
        for stage in self.stages:
            unknown = set(stage.dependencies) - known
            if unknown:
                raise ValueError(f"Unknown dependencies for {stage.id}: {sorted(unknown)}")
            if stage.id in stage.dependencies:
                raise ValueError(f"Stage {stage.id} cannot depend on itself")
        # Kahn-style cycle check. Keep this deterministic and independent of a model.
        remaining = {stage.id: set(stage.dependencies) for stage in self.stages}
        while remaining:
            ready = {stage_id for stage_id, deps in remaining.items() if not deps}
            if not ready:
                raise ValueError("StageGraph contains a dependency cycle")
            for stage_id in ready:
                remaining.pop(stage_id, None)
            for deps in remaining.values():
                deps.difference_update(ready)

    def stage(self, stage_id: str) -> StageSpec:
        for stage in self.stages:
            if stage.id == stage_id:
                return stage
        raise LookupError(stage_id)

    def ready(self, statuses: dict[str, StageStatus]) -> tuple[StageSpec, ...]:
        passed = {stage_id for stage_id, status in statuses.items() if status is StageStatus.PASSED}
        return tuple(
            stage
            for stage in self.stages
            if statuses.get(stage.id, StageStatus.PENDING) is StageStatus.PENDING
            and set(stage.dependencies).issubset(passed)
        )

    def as_dict(self) -> dict[str, Any]:
        return {"stages": [stage.as_dict() for stage in self.stages]}


@dataclass(frozen=True, slots=True)
class ContextCapsule:
    """The only context payload a worker station should receive from the factory.

    References are preferred over copied payloads. ``materialized`` is intentionally
    bounded and contains only stage-selected resources that passed authorization.
    """

    stage_id: str
    objective: str
    context_refs: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()
    facts: tuple[tuple[str, Any], ...] = ()
    materialized: tuple[dict[str, Any], ...] = ()
    capability_ids: tuple[str, ...] = ()
    max_context_chars: int = 18_000

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage_id": self.stage_id,
            "objective": self.objective,
            "context_refs": list(self.context_refs),
            "artifact_refs": list(self.artifact_refs),
            "facts": {key: value for key, value in self.facts},
            "materialized": [dict(item) for item in self.materialized],
            "capability_ids": list(self.capability_ids),
            "max_context_chars": self.max_context_chars,
        }


@dataclass(frozen=True, slots=True)
class RepairBudget:
    max_attempts_per_stage: int = 3
    max_total_attempts: int = 12
    max_repair_depth: int = 2
    max_external_actions: int = 20
    max_runtime_seconds: int = 900
    max_tokens: int = 120_000
    max_cost_usd: float = 10.0
    repeated_failure_threshold: int = 2

    def normalized(self) -> "RepairBudget":
        return RepairBudget(
            max_attempts_per_stage=max(1, min(int(self.max_attempts_per_stage), 10)),
            max_total_attempts=max(1, min(int(self.max_total_attempts), 100)),
            max_repair_depth=max(0, min(int(self.max_repair_depth), 10)),
            max_external_actions=max(0, min(int(self.max_external_actions), 10_000)),
            max_runtime_seconds=max(1, min(int(self.max_runtime_seconds), 86_400)),
            max_tokens=max(1_000, min(int(self.max_tokens), 10_000_000)),
            max_cost_usd=max(0.0, min(float(self.max_cost_usd), 100_000.0)),
            repeated_failure_threshold=max(1, min(int(self.repeated_failure_threshold), 10)),
        )


@dataclass(frozen=True, slots=True)
class Defect:
    stage_id: str
    validator_id: str
    expected: Any
    observed: Any
    evidence_refs: tuple[str, ...] = ()
    failure_class: str = "validation_failed"
    strategy: str = ""
    retryable: bool = True
    repair_depth: int = 0

    @property
    def fingerprint(self) -> str:
        payload = {
            "stage_id": self.stage_id,
            "validator_id": self.validator_id,
            "expected": self.expected,
            "observed": self.observed,
            "failure_class": self.failure_class,
            "strategy": self.strategy,
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage_id": self.stage_id,
            "validator_id": self.validator_id,
            "expected": self.expected,
            "observed": self.observed,
            "evidence_refs": list(self.evidence_refs),
            "failure_class": self.failure_class,
            "strategy": self.strategy,
            "retryable": self.retryable,
            "repair_depth": self.repair_depth,
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True, slots=True)
class StageWorkerResult:
    """Typed output returned by one disposable worker to the control plane."""

    status: str
    strategy: str = ""
    summary: str = ""
    artifacts: tuple[str, ...] = ()
    evidence: dict[str, Any] = field(default_factory=dict)
    evidence_refs: tuple[str, ...] = ()
    external_actions: int = 0
    token_usage: int = 0
    cost_usd: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "strategy": self.strategy,
            "summary": self.summary,
            "artifacts": list(self.artifacts),
            "evidence": dict(self.evidence),
            "evidence_refs": list(self.evidence_refs),
            "external_actions": self.external_actions,
            "token_usage": self.token_usage,
            "cost_usd": self.cost_usd,
        }


def bounded_strings(value: Iterable[Any] | None, *, limit: int, item_chars: int = 500) -> tuple[str, ...]:
    return tuple(
        str(item).strip()[:item_chars]
        for item in list(value or ())[:limit]
        if str(item).strip()
    )
