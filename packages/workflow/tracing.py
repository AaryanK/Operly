from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.kernel_models import KernelEventRecord
from packages.workflow.models import WorkflowTraceEvent


def _json(value: dict[str, Any] | None) -> str:
    return json.dumps(value or {}, separators=(",", ":"), sort_keys=True, default=str)


async def record_workflow_event(
    db: AsyncSession,
    *,
    workspace_id: str | None,
    workflow_id: str,
    event_type: str,
    workflow_run_id: str | None = None,
    step_run_id: str | None = None,
    step_attempt_id: str | None = None,
    actor_type: str = "system",
    actor_id: str | None = None,
    owner_user_id: str | None = None,
    principal_id: str | None = None,
    capability_id: str | None = None,
    kernel_run_id: str | None = None,
    approval_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> WorkflowTraceEvent:
    """Persist Workflow-domain and global Kernel trace records atomically.

    ``workspace_id=None`` is a real Personal authority namespace, never a synthetic
    tenant. Exact action inputs/outputs live on ``WorkflowStepAttempt``; the event
    plane keeps correlation and lifecycle metadata without becoming another copy of
    private/provider payloads.
    """

    scope_kind = "workspace" if workspace_id else "personal"
    if scope_kind == "personal" and not owner_user_id:
        raise ValueError("Personal workflow trace requires owner_user_id")
    at = datetime.utcnow()
    trace = WorkflowTraceEvent(
        scope_kind=scope_kind,
        workspace_id=workspace_id,
        owner_user_id=owner_user_id if scope_kind == "personal" else None,
        workflow_id=workflow_id,
        workflow_run_id=workflow_run_id,
        step_run_id=step_run_id,
        step_attempt_id=step_attempt_id,
        event_type=event_type,
        actor_type=actor_type,
        actor_id=actor_id,
        capability_id=capability_id,
        kernel_run_id=kernel_run_id,
        approval_id=approval_id,
        payload_json=_json(payload),
        created_at=at,
    )
    db.add(trace)
    db.add(
        KernelEventRecord(
            event_type=event_type,
            scope_kind=scope_kind,
            workspace_id=workspace_id,
            owner_user_id=owner_user_id if scope_kind == "personal" else None,
            principal_id=principal_id,
            actor_type=actor_type,
            actor_id=actor_id,
            initiator_principal_id=principal_id,
            executor_principal_id="operly:workflow",
            capability_id=capability_id,
            resource_type="workflow_run" if workflow_run_id else "workflow",
            resource_id=workflow_run_id or workflow_id,
            payload_json=_json(
                {
                    "workflow_id": workflow_id,
                    "workflow_run_id": workflow_run_id,
                    "workflow_step_run_id": step_run_id,
                    "workflow_step_attempt_id": step_attempt_id,
                    "kernel_run_id": kernel_run_id,
                    "approval_id": approval_id,
                    **(payload or {}),
                }
            ),
            created_at=at,
        )
    )
    await db.flush()
    return trace