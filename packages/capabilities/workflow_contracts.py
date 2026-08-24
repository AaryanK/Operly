"""Serializable contracts for promoted, resumable agent workflows.

These structures intentionally do not require a new global database entity.  They
can be stored inside the existing durable task/workflow state and promoted only
when a conversational objective actually needs dependencies, approval, future
execution, or resume semantics.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4


OwnerType = Literal["workspace", "personal"]


@dataclass(frozen=True, slots=True)
class AuthoritySource:
    owner_type: OwnerType
    owner_id: str
    delegated_to_workspace: str | None = None
    delegation_id: str | None = None

    def __post_init__(self) -> None:
        if self.owner_type == "personal" and self.delegated_to_workspace and not self.delegation_id:
            raise ValueError("Personal-to-workspace authority requires an explicit delegation_id")
        if self.owner_type == "workspace" and (self.delegated_to_workspace or self.delegation_id):
            raise ValueError("Workspace-owned authority cannot carry personal delegation metadata")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class EvidenceReference:
    plugin: str
    resource_type: str
    resource_id: str
    fields_used: tuple[str, ...] = ()
    retrieved_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    authority: AuthoritySource | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["fields_used"] = list(self.fields_used)
        if self.authority is not None:
            payload["authority"] = self.authority.as_dict()
        return payload


@dataclass(frozen=True, slots=True)
class ProposalOperation:
    capability: str
    arguments: dict[str, Any]
    risk: str
    expected_outcome: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "arguments": dict(self.arguments),
            "risk": self.risk,
            "expected_outcome": self.expected_outcome,
        }


@dataclass(slots=True)
class WorkflowProposal:
    operations: list[ProposalOperation] = field(default_factory=list)
    missing_inputs: list[str] = field(default_factory=list)
    executable: bool = False
    proposal_id: str = field(default_factory=lambda: str(uuid4()))
    compiled_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def validate_for_execution(self) -> None:
        if self.missing_inputs:
            raise ValueError("Proposal has unresolved inputs")
        if not self.operations:
            raise ValueError("Proposal has no operations")
        if not self.executable:
            raise ValueError("Proposal has not been compiled as executable")

    def as_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "operations": [operation.as_dict() for operation in self.operations],
            "missing_inputs": list(self.missing_inputs),
            "executable": bool(self.executable),
            "compiled_at": self.compiled_at,
        }


@dataclass(slots=True)
class WorkflowState:
    objective: str
    stage: str = "research"
    facts: dict[str, Any] = field(default_factory=dict)
    proposed_operations: list[dict[str, Any]] = field(default_factory=list)
    completed_operations: list[dict[str, Any]] = field(default_factory=list)
    evidence_refs: list[dict[str, Any]] = field(default_factory=list)
    proposal: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "stage": self.stage,
            "facts": dict(self.facts),
            "proposed_operations": list(self.proposed_operations),
            "completed_operations": list(self.completed_operations),
            "evidence_refs": list(self.evidence_refs),
            "proposal": dict(self.proposal) if isinstance(self.proposal, dict) else None,
        }


def should_promote_workflow(
    *,
    dependent_steps: int = 1,
    needs_approval: bool = False,
    future_execution: bool = False,
    resumable: bool = False,
) -> bool:
    """Keep ordinary chat ephemeral unless durable coordination is actually useful."""
    return bool(
        dependent_steps > 1
        or needs_approval
        or future_execution
        or resumable
    )
