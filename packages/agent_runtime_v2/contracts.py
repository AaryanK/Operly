"""Small execution contracts for OPERLY Agent Runtime v2.

Runtime v2 intentionally has four core concepts: Plan, Step, RunState and Engine.
There is no separate blueprint/validator/defect/capability-intent hierarchy. Exact
capability IDs are chosen during planning from an application-supplied authorized
catalog, and every completed step becomes explicit durable-in-run state for later
steps.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class Step:
    id: str
    objective: str
    capabilities: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    mutating: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "objective": self.objective,
            "capabilities": list(self.capabilities),
            "depends_on": list(self.depends_on),
            "mutating": self.mutating,
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
    observations: list[Observation] = field(default_factory=list)
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "summary": self.summary,
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
