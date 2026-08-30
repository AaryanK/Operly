from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.kernel_models import KernelEventRecord, KernelRun, KernelRunStep
from packages.security.execution_context import ExecutionContext


def _safe_json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True, default=str)


def _result_summary(result: dict[str, Any] | None) -> dict[str, Any]:
    """Audit shape without duplicating provider-returned business/private data."""
    if not result:
        return {}
    return {"result_keys": sorted(str(key) for key in result)}


@dataclass(slots=True)
class RuntimeAuditBuffer:
    run_id: str = field(default_factory=lambda: str(uuid4()))
    started_at: datetime = field(default_factory=datetime.utcnow)
    steps: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)

    def step(self, number: int, name: str, status: str, payload: dict[str, Any] | None = None) -> None:
        self.steps.append(
            {
                "step": number,
                "name": name,
                "status": status,
                "payload": dict(payload or {}),
                "at": datetime.utcnow(),
            }
        )

    def event(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        self.events.append(
            {"event_type": event_type, "payload": dict(payload or {}), "at": datetime.utcnow()}
        )

    def public_trace(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            {
                "step": row["step"],
                "name": row["name"],
                "status": row["status"],
            }
            for row in self.steps
        )


async def persist_audit(
    db: AsyncSession,
    *,
    buffer: RuntimeAuditBuffer,
    context: ExecutionContext,
    goal: str,
    capability_id: str | None,
    status: str,
    result: dict[str, Any] | None,
    error: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
) -> None:
    run = KernelRun(
        id=buffer.run_id,
        scope_kind=context.scope_kind.value,
        workspace_id=context.workspace_id,
        owner_user_id=context.user_id if context.is_personal else None,
        principal_id=context.principal_id,
        channel=context.channel,
        surface=context.surface.value,
        conversation_id=context.conversation_id,
        # Runtime audit records intent presence, not raw user prompts. Provider-returned
        # data is likewise summarized below so the audit plane does not become a second
        # copy of private/workspace business data.
        goal="provided" if str(goal or "").strip() else "",
        capability_id=capability_id,
        status=status,
        result_json=_safe_json(_result_summary(result)),
        # Provider exception text may contain upstream details. The stage/event trace
        # carries the stable failure code; the audit row never stores raw exceptions.
        error="runtime_error" if error else None,
        started_at=buffer.started_at,
        finished_at=datetime.utcnow(),
    )
    db.add(run)
    for row in buffer.steps:
        db.add(
            KernelRunStep(
                run_id=buffer.run_id,
                step_number=int(row["step"]),
                step_name=str(row["name"]),
                status=str(row["status"]),
                payload_json=_safe_json(row["payload"]),
                created_at=row["at"],
            )
        )
    for event in buffer.events:
        db.add(
            KernelEventRecord(
                event_type=str(event["event_type"]),
                scope_kind=context.scope_kind.value,
                workspace_id=context.workspace_id,
                owner_user_id=context.user_id if context.is_personal else None,
                principal_id=context.principal_id,
                actor_type="human" if context.user_id else "system",
                actor_id=context.user_id or context.principal_id,
                initiator_principal_id=context.principal_id,
                executor_principal_id="operly:kernel",
                capability_id=capability_id,
                resource_type=resource_type,
                resource_id=resource_id,
                payload_json=_safe_json(event["payload"]),
                created_at=event["at"],
            )
        )
    await db.flush()
