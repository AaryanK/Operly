from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.kernel_models import KernelApproval
from packages.kernel.contracts import RuntimeRequest
from packages.kernel.runtime import RuntimeExecutionError
from packages.security.execution_context import ExecutionContextError, resolve_execution_context
from packages.security.surfaces import SurfaceKind
from packages.workflow.models import (
    WorkflowDefinition,
    WorkflowRun,
    WorkflowStepRun,
    WorkflowVersion,
)
from packages.workflow.spec import WorkflowSpecError, evaluate_condition, render_value, validate_workflow_spec
from packages.workflow.tracing import record_workflow_event


def _loads(raw: str | None, fallback: Any) -> Any:
    try:
        value = json.loads(raw or "")
    except Exception:
        return fallback
    return value


def _dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True, default=str)


def _parse_until(value: Any) -> datetime:
    raw = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise WorkflowSpecError("Wait-until value must resolve to ISO 8601") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _step_context(run: WorkflowRun, completed: dict[str, WorkflowStepRun]) -> dict[str, Any]:
    steps: dict[str, Any] = {}
    for key, row in completed.items():
        steps[key] = {
            "status": row.status,
            "attempt": row.attempt,
            "result": _loads(row.result_json, {}),
            "error": {"code": row.error_code, "message": row.error_message} if row.error_code else None,
        }
    return {
        "trigger": _loads(run.trigger_payload_json, {}),
        "steps": steps,
        "run": {"id": run.id, "trigger_type": run.trigger_type, "scheduled_for": run.scheduled_for.isoformat() if run.scheduled_for else None},
    }


async def queue_workflow_run(
    db: AsyncSession,
    *,
    workflow: WorkflowDefinition,
    trigger_type: str,
    trigger_payload: dict[str, Any] | None,
    initiated_by_user_id: str | None,
    dedupe_key: str | None = None,
    scheduled_for: datetime | None = None,
) -> WorkflowRun:
    version = await db.scalar(
        select(WorkflowVersion).where(
            WorkflowVersion.workflow_id == workflow.id,
            WorkflowVersion.version == workflow.current_version,
        )
    )
    if version is None:
        raise RuntimeError("Workflow version is unavailable")
    run = WorkflowRun(
        workflow_id=workflow.id,
        workflow_version_id=version.id,
        workspace_id=workflow.workspace_id,
        owner_user_id=workflow.owner_user_id,
        initiated_by_user_id=initiated_by_user_id,
        trigger_type=trigger_type,
        trigger_payload_json=_dumps(trigger_payload or {}),
        dedupe_key=dedupe_key or f"manual:{workflow.id}:{uuid4()}",
        status="queued",
        scheduled_for=scheduled_for,
    )
    db.add(run)
    await db.flush()
    await record_workflow_event(
        db,
        workspace_id=workflow.workspace_id,
        workflow_id=workflow.id,
        workflow_run_id=run.id,
        event_type="workflow.run.queued",
        actor_type="human" if initiated_by_user_id else "system",
        actor_id=initiated_by_user_id or "operly:scheduler",
        owner_user_id=workflow.owner_user_id,
        principal_id=f"user:{initiated_by_user_id}" if initiated_by_user_id else f"user:{workflow.owner_user_id}" if workflow.owner_user_id else None,
        payload={"trigger_type": trigger_type, "scheduled_for": scheduled_for.isoformat() if scheduled_for else None, "workflow_version": workflow.current_version},
    )
    return run


class WorkflowEngine:
    def _runtime(self):
        # Lazy import prevents Workflow provider composition from recursively building
        # another Workspace runtime while the registry is being assembled.
        from packages.workspace_modules.tools.runtime import build_workspace_runtime
        return build_workspace_runtime()

    async def execute_run(self, db: AsyncSession, run_id: str) -> WorkflowRun:
        run = await db.get(WorkflowRun, run_id)
        if run is None:
            raise LookupError("Workflow run is unavailable")
        if run.status in {"completed", "cancelled"}:
            return run
        workflow = await db.get(WorkflowDefinition, run.workflow_id)
        version = await db.get(WorkflowVersion, run.workflow_version_id)
        if workflow is None or version is None:
            return await self._fail(db, run, workflow, "workflow_unavailable", "Workflow definition/version is unavailable")
        if workflow.status == "archived":
            return await self._fail(db, run, workflow, "workflow_archived", "Workflow has been archived")
        if not run.owner_user_id:
            return await self._fail(db, run, workflow, "owner_unavailable", "Workflow owner is unavailable")

        try:
            spec = validate_workflow_spec(_loads(version.spec_json, {}))
        except WorkflowSpecError as error:
            return await self._fail(db, run, workflow, "invalid_spec", str(error))

        if run.started_at is None:
            run.started_at = datetime.utcnow()
        run.status = "running"
        run.error_code = None
        run.error_message = None
        await record_workflow_event(
            db,
            workspace_id=run.workspace_id,
            workflow_id=run.workflow_id,
            workflow_run_id=run.id,
            event_type="workflow.run.started",
            actor_type="system",
            actor_id="operly:workflow",
            owner_user_id=run.owner_user_id,
            principal_id=f"user:{run.owner_user_id}",
            payload={"workflow_version_id": run.workflow_version_id},
        )
        await db.commit()

        rows = (
            await db.scalars(
                select(WorkflowStepRun)
                .where(WorkflowStepRun.workflow_run_id == run.id)
                .order_by(WorkflowStepRun.step_order)
            )
        ).all()
        completed = {row.step_key: row for row in rows}

        for index, step in enumerate(spec["steps"]):
            await db.refresh(run)
            if run.status == "cancelled":
                run.lease_token = None
                run.lease_until = None
                await db.commit()
                return run
            step_key = str(step["id"])
            existing = completed.get(step_key)
            if existing and existing.status in {"completed", "skipped"}:
                continue

            missing_dependency = next(
                (dependency for dependency in step.get("depends_on", []) if dependency not in completed or completed[dependency].status not in {"completed", "skipped"}),
                None,
            )
            if missing_dependency:
                return await self._fail(db, run, workflow, "dependency_unavailable", f"Step {step_key} depends on unfinished step {missing_dependency}")

            context_data = _step_context(run, completed)
            if step.get("when") is not None and not evaluate_condition(step["when"], context_data):
                row = existing or WorkflowStepRun(workflow_run_id=run.id, step_key=step_key, step_order=index, step_kind=str(step["kind"]), capability_id=step.get("capability_id"))
                if existing is None:
                    db.add(row)
                row.status = "skipped"
                row.finished_at = datetime.utcnow()
                run.current_step_key = step_key
                await db.flush()
                completed[step_key] = row
                await record_workflow_event(
                    db,
                    workspace_id=run.workspace_id,
                    workflow_id=run.workflow_id,
                    workflow_run_id=run.id,
                    step_run_id=row.id,
                    event_type="workflow.step.skipped",
                    actor_id="operly:workflow",
                    owner_user_id=run.owner_user_id,
                    principal_id=f"user:{run.owner_user_id}",
                    payload={"step_key": step_key, "reason": "condition_false"},
                )
                await db.commit()
                continue

            if step["kind"] == "wait":
                waiting = await self._wait_step(db, run, workflow, existing, step, index, context_data)
                completed[step_key] = waiting
                if waiting.status == "waiting":
                    return run
                continue

            action = await self._action_step(db, run, workflow, existing, step, index, context_data)
            completed[step_key] = action
            if action.status == "waiting_approval":
                return run
            if action.status == "failed" and step.get("on_error") != "continue":
                return await self._fail(db, run, workflow, action.error_code or "step_failed", action.error_message or f"Step {step_key} failed")

        run.status = "completed"
        run.current_step_key = None
        run.finished_at = datetime.utcnow()
        run.lease_token = None
        run.lease_until = None
        run.result_json = _dumps({key: _loads(row.result_json, {}) for key, row in completed.items()})
        await record_workflow_event(
            db,
            workspace_id=run.workspace_id,
            workflow_id=run.workflow_id,
            workflow_run_id=run.id,
            event_type="workflow.run.completed",
            actor_id="operly:workflow",
            owner_user_id=run.owner_user_id,
            principal_id=f"user:{run.owner_user_id}",
            payload={"step_count": len(spec["steps"])},
        )
        await db.commit()
        return run

    async def _wait_step(
        self,
        db: AsyncSession,
        run: WorkflowRun,
        workflow: WorkflowDefinition,
        row: WorkflowStepRun | None,
        step: dict[str, Any],
        index: int,
        context_data: dict[str, Any],
    ) -> WorkflowStepRun:
        now = datetime.utcnow()
        if row is None:
            row = WorkflowStepRun(workflow_run_id=run.id, step_key=step["id"], step_order=index, step_kind="wait", status="pending")
            db.add(row)
            await db.flush()
        if row.wait_until is None:
            if "seconds" in step:
                row.wait_until = now + timedelta(seconds=int(step["seconds"]))
            else:
                row.wait_until = _parse_until(render_value(step["until"], context_data))
        if row.wait_until > now:
            row.status = "waiting"
            row.started_at = row.started_at or now
            run.status = "waiting"
            run.current_step_key = row.step_key
            run.lease_token = None
            run.lease_until = None
            await record_workflow_event(
                db,
                workspace_id=run.workspace_id,
                workflow_id=run.workflow_id,
                workflow_run_id=run.id,
                step_run_id=row.id,
                event_type="workflow.step.waiting",
                actor_id="operly:workflow",
                owner_user_id=run.owner_user_id,
                principal_id=f"user:{run.owner_user_id}",
                payload={"step_key": row.step_key, "wait_until": row.wait_until.isoformat()},
            )
            await db.commit()
            return row
        row.status = "completed"
        row.started_at = row.started_at or now
        row.finished_at = now
        row.result_json = _dumps({"waited_until": row.wait_until.isoformat()})
        run.current_step_key = row.step_key
        await record_workflow_event(
            db,
            workspace_id=run.workspace_id,
            workflow_id=run.workflow_id,
            workflow_run_id=run.id,
            step_run_id=row.id,
            event_type="workflow.step.completed",
            actor_id="operly:workflow",
            owner_user_id=run.owner_user_id,
            principal_id=f"user:{run.owner_user_id}",
            payload={"step_key": row.step_key, "kind": "wait"},
        )
        await db.commit()
        return row

    async def _action_step(
        self,
        db: AsyncSession,
        run: WorkflowRun,
        workflow: WorkflowDefinition,
        row: WorkflowStepRun | None,
        step: dict[str, Any],
        index: int,
        context_data: dict[str, Any],
    ) -> WorkflowStepRun:
        now = datetime.utcnow()
        if row is None:
            row = WorkflowStepRun(
                workflow_run_id=run.id,
                step_key=step["id"],
                step_order=index,
                step_kind="action",
                capability_id=step["capability_id"],
                status="pending",
            )
            db.add(row)
            await db.flush()
        approval_id = None
        if row.status == "waiting_approval" and row.approval_id:
            approval = await db.get(KernelApproval, row.approval_id)
            if approval is None or approval.status == "pending":
                run.status = "waiting_approval"
                run.current_step_key = row.step_key
                run.lease_token = None
                run.lease_until = None
                await db.commit()
                return row
            if approval.status != "approved":
                row.status = "failed"
                row.error_code = "approval_rejected"
                row.error_message = "A human rejected this workflow step"
                row.finished_at = now
                return row
            approval_id = approval.id
        else:
            row.attempt += 1
            row.request_id = f"workflow:{run.id}:{row.step_key}:attempt:{row.attempt}"
            row.arguments_json = _dumps(render_value(step.get("arguments") or {}, context_data))
            row.approval_id = None
            row.kernel_run_id = None
            row.error_code = None
            row.error_message = None

        arguments = _loads(row.arguments_json, {})
        row.status = "running"
        row.started_at = row.started_at or now
        run.status = "running"
        run.current_step_key = row.step_key
        await record_workflow_event(
            db,
            workspace_id=run.workspace_id,
            workflow_id=run.workflow_id,
            workflow_run_id=run.id,
            step_run_id=row.id,
            event_type="workflow.step.started",
            actor_id="operly:workflow",
            owner_user_id=run.owner_user_id,
            principal_id=f"user:{run.owner_user_id}",
            capability_id=row.capability_id,
            approval_id=approval_id,
            payload={"step_key": row.step_key, "attempt": row.attempt, "request_id": row.request_id, "argument_keys": sorted(arguments)},
        )
        await db.commit()

        try:
            authority = await resolve_execution_context(
                db,
                workspace_id=run.workspace_id,
                user_id=run.owner_user_id,
                channel="workflow",
                surface=SurfaceKind.SYSTEM_TASK,
                conversation_id=f"workflow:{run.id}",
                metadata={"workflow_id": run.workflow_id, "workflow_run_id": run.id, "workflow_step_key": row.step_key},
            )
        except (ExecutionContextError, PermissionError) as error:
            row = await db.get(WorkflowStepRun, row.id)
            assert row is not None
            row.status = "failed"
            row.error_code = "authority_unavailable"
            row.error_message = str(error)[:1000]
            row.finished_at = datetime.utcnow()
            await record_workflow_event(
                db,
                workspace_id=run.workspace_id,
                workflow_id=run.workflow_id,
                workflow_run_id=run.id,
                step_run_id=row.id,
                event_type="workflow.step.failed",
                actor_id="operly:workflow",
                owner_user_id=run.owner_user_id,
                principal_id=f"user:{run.owner_user_id}",
                capability_id=row.capability_id,
                payload={"step_key": row.step_key, "code": row.error_code},
            )
            await db.commit()
            return row

        try:
            response = await self._runtime().execute(
                db,
                context=authority,
                request=RuntimeRequest(
                    capability_id=row.capability_id,
                    arguments=arguments,
                    conversation_id=f"workflow:{run.id}",
                    request_id=row.request_id,
                    approval_id=approval_id,
                ),
            )
        except RuntimeExecutionError as error:
            row = await db.get(WorkflowStepRun, row.id)
            run = await db.get(WorkflowRun, run.id)
            assert row is not None and run is not None
            row.kernel_run_id = error.run_id
            if error.code == "approval_required" and error.approval_id:
                row.status = "waiting_approval"
                row.approval_id = error.approval_id
                run.status = "waiting_approval"
                run.current_step_key = row.step_key
                run.lease_token = None
                run.lease_until = None
                await record_workflow_event(
                    db,
                    workspace_id=run.workspace_id,
                    workflow_id=run.workflow_id,
                    workflow_run_id=run.id,
                    step_run_id=row.id,
                    event_type="workflow.step.waiting_approval",
                    actor_id="operly:workflow",
                    owner_user_id=run.owner_user_id,
                    principal_id=f"user:{run.owner_user_id}",
                    capability_id=row.capability_id,
                    kernel_run_id=error.run_id,
                    approval_id=error.approval_id,
                    payload={"step_key": row.step_key, "request_id": row.request_id},
                )
            else:
                row.status = "failed"
                row.error_code = error.code
                row.error_message = str(error)[:1000]
                row.finished_at = datetime.utcnow()
                await record_workflow_event(
                    db,
                    workspace_id=run.workspace_id,
                    workflow_id=run.workflow_id,
                    workflow_run_id=run.id,
                    step_run_id=row.id,
                    event_type="workflow.step.failed",
                    actor_id="operly:workflow",
                    owner_user_id=run.owner_user_id,
                    principal_id=f"user:{run.owner_user_id}",
                    capability_id=row.capability_id,
                    kernel_run_id=error.run_id,
                    payload={"step_key": row.step_key, "code": error.code},
                )
            await db.commit()
            return row

        row = await db.get(WorkflowStepRun, row.id)
        run = await db.get(WorkflowRun, run.id)
        assert row is not None and run is not None
        row.status = "completed"
        row.kernel_run_id = response.run_id
        row.result_json = _dumps(response.result or {})
        row.error_code = None
        row.error_message = None
        row.finished_at = datetime.utcnow()
        await record_workflow_event(
            db,
            workspace_id=run.workspace_id,
            workflow_id=run.workflow_id,
            workflow_run_id=run.id,
            step_run_id=row.id,
            event_type="workflow.step.completed",
            actor_id="operly:workflow",
            owner_user_id=run.owner_user_id,
            principal_id=f"user:{run.owner_user_id}",
            capability_id=row.capability_id,
            kernel_run_id=response.run_id,
            approval_id=approval_id,
            payload={"step_key": row.step_key, "attempt": row.attempt, "request_id": row.request_id, "result_keys": sorted((response.result or {}).keys())},
        )
        await db.commit()
        return row

    async def _fail(self, db: AsyncSession, run: WorkflowRun, workflow: WorkflowDefinition | None, code: str, message: str) -> WorkflowRun:
        run.status = "failed"
        run.error_code = code
        run.error_message = message[:2000]
        run.finished_at = datetime.utcnow()
        run.lease_token = None
        run.lease_until = None
        if workflow is not None:
            await record_workflow_event(
                db,
                workspace_id=run.workspace_id,
                workflow_id=run.workflow_id,
                workflow_run_id=run.id,
                event_type="workflow.run.failed",
                actor_id="operly:workflow",
                owner_user_id=run.owner_user_id,
                principal_id=f"user:{run.owner_user_id}" if run.owner_user_id else None,
                payload={"code": code},
            )
        await db.commit()
        return run


workflow_engine = WorkflowEngine()
