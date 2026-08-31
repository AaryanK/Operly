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
    workspace_id: str,
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

    Exact action inputs/outputs live on ``WorkflowStepAttempt``. The event plane keeps
    correlation and lifecycle metadata so Activity/FLOW-style consumers do not become
    a second uncontrolled copy of provider/business payloads.
    """

    del owner_user_id  # Workspace Kernel events intentionally do not use personal owner scope.
    at = datetime.utcnow()
    trace = WorkflowTraceEvent(
        workspace_id=workspace_id,
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
            scope_kind="workspace",
            workspace_id=workspace_id,
            owner_user_id=None,
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
