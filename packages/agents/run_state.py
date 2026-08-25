"""Structured state for durable/adaptive agent runs.

This is intentionally model-neutral. Models consume compact summaries; raw evidence
and capability observations remain outside the live prompt and are referenced by ID.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class RunTaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass(slots=True)
class RunTask:
    id: str
    objective: str
    dependencies: tuple[str, ...] = ()
    success_criteria: tuple[str, ...] = ()
    context_intents: tuple[str, ...] = ()
    capability_intents: tuple[str, ...] = ()
    assigned_role: str = "business_agent"
    can_parallelize: bool = False
    status: RunTaskStatus = RunTaskStatus.PENDING

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "RunTask":
        raw_status = str(value.get("status") or RunTaskStatus.PENDING.value)
        try:
            status = RunTaskStatus(raw_status)
        except ValueError:
            status = RunTaskStatus.PENDING
        return cls(
            id=str(value.get("id") or "")[:120],
            objective=str(value.get("objective") or "")[:20_000],
            dependencies=tuple(str(item) for item in (value.get("dependencies") or []) if str(item).strip()),
            success_criteria=tuple(str(item) for item in (value.get("success_criteria") or []) if str(item).strip()),
            context_intents=tuple(str(item) for item in (value.get("context_intents") or []) if str(item).strip()),
            capability_intents=tuple(str(item) for item in (value.get("capability_intents") or []) if str(item).strip()),
            assigned_role=str(value.get("assigned_role") or "business_agent")[:80],
            can_parallelize=bool(value.get("can_parallelize")),
            status=status,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "objective": self.objective,
            "dependencies": list(self.dependencies),
            "success_criteria": list(self.success_criteria),
            "context_intents": list(self.context_intents),
            "capability_intents": list(self.capability_intents),
            "assigned_role": self.assigned_role,
            "can_parallelize": self.can_parallelize,
            "status": self.status.value,
        }


@dataclass(slots=True)
class RunPlan:
    objective: str
    success_criteria: tuple[str, ...] = ()
    tasks: list[RunTask] = field(default_factory=list)
    planning_required: bool = False
    revision: int = 0

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "RunPlan":
        tasks = [
            RunTask.from_dict(item)
            for item in (value.get("tasks") or [])
            if isinstance(item, dict)
        ]
        return cls(
            objective=str(value.get("objective") or "")[:50_000],
            success_criteria=tuple(str(item) for item in (value.get("success_criteria") or []) if str(item).strip()),
            tasks=tasks,
            planning_required=bool(value.get("planning_required")),
            revision=max(0, int(value.get("revision") or 0)),
        )

    def task(self, task_id: str) -> RunTask | None:
        return next((task for task in self.tasks if task.id == task_id), None)

    def ready_tasks(self) -> list[RunTask]:
        completed = {
            task.id for task in self.tasks if task.status is RunTaskStatus.COMPLETED
        }
        return [
            task
            for task in self.tasks
            if task.status is RunTaskStatus.PENDING
            and set(task.dependencies).issubset(completed)
        ]

    def as_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "success_criteria": list(self.success_criteria),
            "planning_required": self.planning_required,
            "revision": self.revision,
            "tasks": [task.as_dict() for task in self.tasks],
        }


@dataclass(slots=True)
class CompactRunState:
    objective: str
    plan: RunPlan | None = None
    facts: dict[str, Any] = field(default_factory=dict)
    context_refs: set[str] = field(default_factory=set)
    artifact_refs: set[str] = field(default_factory=set)
    evidence_refs: set[str] = field(default_factory=set)
    action_ids: set[str] = field(default_factory=set)
    pending_approval_ids: set[str] = field(default_factory=set)
    unresolved: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    revision: int = 0

    @classmethod
    def from_dict(cls, value: dict[str, Any], *, fallback_objective: str = "") -> "CompactRunState":
        plan_value = value.get("plan")
        plan = RunPlan.from_dict(plan_value) if isinstance(plan_value, dict) else None
        facts = value.get("facts") if isinstance(value.get("facts"), dict) else {}
        return cls(
            objective=str(value.get("objective") or fallback_objective or (plan.objective if plan else ""))[:50_000],
            plan=plan,
            facts=dict(facts),
            context_refs={str(item) for item in (value.get("context_refs") or []) if str(item).strip()},
            artifact_refs={str(item) for item in (value.get("artifact_refs") or []) if str(item).strip()},
            evidence_refs={str(item) for item in (value.get("evidence_refs") or []) if str(item).strip()},
            action_ids={str(item) for item in (value.get("action_ids") or []) if str(item).strip()},
            pending_approval_ids={str(item) for item in (value.get("pending_approval_ids") or []) if str(item).strip()},
            unresolved=[str(item) for item in (value.get("unresolved") or []) if str(item).strip()][-12:],
            failures=[str(item) for item in (value.get("failures") or []) if str(item).strip()][-12:],
            revision=max(0, int(value.get("revision") or 0)),
        )

    @staticmethod
    def _strings(value: Any) -> list[str]:
        if isinstance(value, str):
            return [value] if value.strip() else []
        if isinstance(value, (list, tuple, set)):
            return [str(item) for item in value if str(item).strip()]
        return []

    def record_observation(
        self,
        capability_id: str,
        observation: dict[str, Any],
    ) -> None:
        """Extract durable handles/status without copying bulky observations."""
        self.revision += 1
        payloads = [observation]
        nested = observation.get("observation")
        if isinstance(nested, dict):
            payloads.append(nested)

        for payload in payloads:
            for key, value in payload.items():
                lowered = str(key).lower()
                if lowered in {"ref", "context_ref"}:
                    self.context_refs.update(self._strings(value))
                elif lowered in {"refs", "context_refs", "context_refs_used"}:
                    self.context_refs.update(self._strings(value))
                elif lowered in {"artifact_id", "artifact_ref"}:
                    self.artifact_refs.update(self._strings(value))
                elif lowered in {"artifact_ids", "artifact_refs"}:
                    self.artifact_refs.update(self._strings(value))
                elif lowered in {"evidence_ref", "evidence_refs"}:
                    self.evidence_refs.update(self._strings(value))
                elif lowered == "action_id":
                    self.action_ids.update(self._strings(value))
                elif lowered == "approval_id":
                    self.pending_approval_ids.update(self._strings(value))

        status = str(observation.get("status") or "").upper()
        error = str(observation.get("error") or "").strip()
        if status == "WAITING_APPROVAL":
            self.unresolved.append(f"Approval pending for {capability_id}")
        elif status in {"FAILED", "UNVERIFIED", "DENIED"}:
            self.failures.append(
                f"{capability_id}: {error or status.lower()}"
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "plan": self.plan.as_dict() if self.plan else None,
            "facts": self.facts,
            "context_refs": sorted(self.context_refs),
            "artifact_refs": sorted(self.artifact_refs),
            "evidence_refs": sorted(self.evidence_refs),
            "action_ids": sorted(self.action_ids),
            "pending_approval_ids": sorted(self.pending_approval_ids),
            "unresolved": self.unresolved[-12:],
            "failures": self.failures[-12:],
            "revision": self.revision,
        }

    def prompt_summary(self) -> dict[str, Any]:
        """Bounded model-facing state; raw observations intentionally excluded."""
        plan = self.plan.as_dict() if self.plan else None
        return {
            "objective": self.objective,
            "plan": plan,
            "facts": dict(list(self.facts.items())[-20:]),
            "context_refs": sorted(self.context_refs)[-20:],
            "artifact_refs": sorted(self.artifact_refs)[-20:],
            "evidence_refs": sorted(self.evidence_refs)[-20:],
            "pending_approval_ids": sorted(self.pending_approval_ids)[-10:],
            "unresolved": self.unresolved[-8:],
            "failures": self.failures[-8:],
        }


@dataclass(frozen=True, slots=True)
class SpecialistResult:
    task_id: str
    status: str
    summary: str
    context_refs: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "summary": self.summary,
            "context_refs": list(self.context_refs),
            "artifact_refs": list(self.artifact_refs),
            "evidence_refs": list(self.evidence_refs),
            "unresolved": list(self.unresolved),
        }
