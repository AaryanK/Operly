"""Small execution contracts for OPERLY Agent Runtime v2.

Runtime v2 keeps the core execution model intentionally small: Plan, Step, RunState
and Engine. StepOutput is the bounded data contract passed between those core
objects; it replaces replaying raw provider observations into every downstream
worker.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


_ALLOWED_RUN_IF_FIELDS = frozenset({"has_findings", "coverage_complete"})


@dataclass(frozen=True, slots=True)
class StepOutput:
    """Structured, bounded result produced by one completed disposable worker."""

    summary: str
    findings: tuple[dict[str, Any], ...] = ()
    refs: tuple[str, ...] = ()
    coverage_complete: bool | None = None
    coverage_reason: str = ""

    @property
    def has_findings(self) -> bool:
        return bool(self.findings)

    def field_value(self, field_name: str) -> bool | None:
        clean = str(field_name or "").strip().lower()
        if clean == "has_findings":
            return self.has_findings
        if clean == "coverage_complete":
            return self.coverage_complete
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "findings": [dict(item) for item in self.findings],
            "refs": list(self.refs),
            "has_findings": self.has_findings,
            "coverage": {
                "complete": self.coverage_complete,
                "reason": self.coverage_reason,
            },
        }


@dataclass(frozen=True, slots=True)
class Step:
    id: str
    objective: str
    capabilities: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    mutating: bool = False
    run_if_step_id: str | None = None
    run_if_field: str | None = None
    run_if_equals: bool = True
    requires_complete_coverage: bool = False

    @property
    def conditional(self) -> bool:
        return bool(self.run_if_step_id and self.run_if_field in _ALLOWED_RUN_IF_FIELDS)

    def as_dict(self) -> dict[str, Any]:
        run_if = None
        if self.conditional:
            run_if = {
                "step_id": self.run_if_step_id,
                "field": self.run_if_field,
                "equals": self.run_if_equals,
            }
        return {
            "id": self.id,
            "objective": self.objective,
            "capabilities": list(self.capabilities),
            "depends_on": list(self.depends_on),
            "mutating": self.mutating,
            "run_if": run_if,
            "requires_complete_coverage": self.requires_complete_coverage,
        }


@dataclass(frozen=True, slots=True)
class Plan:
    goal: str
    constraints: tuple[str, ...]
    steps: tuple[Step, ...]
    final_step_id: str
    blocked: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "constraints": list(self.constraints),
            "steps": [step.as_dict() for step in self.steps],
            "final_step_id": self.final_step_id,
            "blocked": [dict(item) for item in self.blocked],
        }


@dataclass(slots=True)
class Observation:
    capability_id: str
    arguments: dict[str, Any]
    result: dict[str, Any]
    signature: str
    memoized: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "arguments": self.arguments,
            "result": self.result,
            "signature": self.signature,
            "memoized": self.memoized,
        }


@dataclass(slots=True)
class StepState:
    id: str
    status: str = "pending"
    summary: str = ""
    output: StepOutput | None = None
    observations: list[Observation] = field(default_factory=list)
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "summary": self.summary,
            "output": self.output.as_dict() if self.output is not None else None,
            "observations": [item.as_dict() for item in self.observations],
            "model_calls": self.model_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


@dataclass(slots=True)
class RunState:
    run_id: str
    objective: str
    plan: Plan
    steps: dict[str, StepState]
    runtime_context: dict[str, Any] = field(default_factory=dict)
    status: str = "running"
    stop_reason: str | None = None
    mutation_epoch: int = 0
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "objective": self.objective,
            "runtime_context": dict(self.runtime_context),
            "status": self.status,
            "stop_reason": self.stop_reason,
            "mutation_epoch": self.mutation_epoch,
            "model_calls": self.model_calls,
            "token_usage": {
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "total_tokens": self.total_tokens,
            },
            "plan": self.plan.as_dict(),
            "steps": {key: value.as_dict() for key, value in self.steps.items()},
        }
