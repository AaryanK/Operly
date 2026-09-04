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
from packages.security.execution_context import (
    ExecutionContextError,
    ScopeKind,
    resolve_execution_context,
    resolve_personal_execution_context,
)
from packages.security.surfaces import SurfaceKind
from packages.workflow.models import (
    WorkflowDefinition,
    WorkflowRun,
    WorkflowStepAttempt,
    WorkflowStepRun,
    WorkflowVersion,
)
from packages.workflow.spec import (
    MAX_WAIT_SECONDS,
    WorkflowSpecError,
    evaluate_condition,
    render_value,
    validate_workflow_spec,
)
from packages.workflow.tracing import record_workflow_event


_STOP_STATES = frozenset({"cancelled", "orphaned"})


def _loads(raw: str | None, fallback: Any) -> Any:
    try:
        return json.loads(raw or "")
    except Exception:
        return fallback


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


def _step_context(run: WorkflowRun, rows: dict[str, WorkflowStepRun]) -> dict[str, Any]:
    steps: dict[str, Any] = {}
    for key, row in rows.items():
        steps[key] = {
            "status": row.status,
            "attempt": row.attempt,
            "result": _loads(row.result_json, {}),
            "error": (
                {"code": row.error_code, "message": row.error_message}
                if row.error_code
                else None
            ),
        }
    return {
        "trigger": _loads(run.trigger_payload_json, {}),
        "steps": steps,
        "run": {
            "id": run.id,
            "scope_kind": run.scope_kind,
            "trigger_type": run.trigger_type,
            "scheduled_for": run.scheduled_for.isoformat() if run.scheduled_for else None,
        },
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

    # Manual runs execute as the person who started them. Scheduled/event runs have
    # no live initiator and therefore execute as the durable definition owner. Every
    # action re-resolves this user against current Personal/Workspace authority.
    authority_user_id = initiated_by_user_id or workflow.owner_user_id
    if not authority_user_id:
        raise PermissionError("Workflow run has no executable authority user")

    scope_kind = str(workflow.scope_kind or "workspace")
    if scope_kind == ScopeKind.PERSONAL.value and workflow.workspace_id is not None:
        raise PermissionError("Personal workflow may not carry Workspace authority")
    if scope_kind == ScopeKind.WORKSPACE.value and not workflow.workspace_id:
        raise PermissionError("Workspace workflow requires workspace_id")

    run = WorkflowRun(
        workflow_id=workflow.id,
        workflow_version_id=version.id,
        scope_kind=scope_kind,
        workspace_id=workflow.workspace_id,
        authority_user_id=authority_user_id,
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
        owner_user_id=authority_user_id,
        principal_id=f"user:{authority_user_id}",
        payload={
            "scope_kind": scope_kind,
            "trigger_type": trigger_type,
            "scheduled_for": scheduled_for.isoformat() if scheduled_for else None,
            "workflow_version": workflow.current_version,
            "workflow_version_id": version.id,
            "authority_user_id": authority_user_id,
        },
    )
    return run


class WorkflowEngine:
    def __init__(self) -> None:
        self._workspace_runtime = None
        self._personal_runtime = None

    def _runtime(self, run: WorkflowRun):
        # Lazy construction prevents Workflow provider composition from recursively
        # building another runtime while the registry itself is being assembled.
        if run.scope_kind == ScopeKind.PERSONAL.value:
            if self._personal_runtime is None:
                from packages.personal_modules.runtime import build_personal_runtime

                self._personal_runtime = build_personal_runtime()
            return self._personal_runtime
        if self._workspace_runtime is None:
            from packages.workspace_modules.tools.runtime import build_workspace_runtime

            self._workspace_runtime = build_workspace_runtime()
        return self._workspace_runtime

    async def _resolve_authority(
        self,
        db: AsyncSession,
        *,
        run: WorkflowRun,
        row: WorkflowStepRun,
        attempt: WorkflowStepAttempt,
    ):
        trigger = _loads(run.trigger_payload_json, {})
        event = trigger.get("event") if isinstance(trigger, dict) else None
        metadata = {
            "workflow_id": run.workflow_id,
            "workflow_run_id": run.id,
            "workflow_step_key": row.step_key,
            "workflow_step_run_id": row.id,
            "workflow_step_attempt_id": attempt.id,
            "workflow_attempt": row.attempt,
        }
        if isinstance(event, dict):
            metadata["workflow_trigger_event_id"] = event.get("id")
            metadata["workflow_correlation_id"] = event.get("correlation_id") or event.get("id")
            metadata["workflow_causation_id"] = event.get("id")
            metadata["workflow_depth"] = int(trigger.get("depth") or 1)

        if run.scope_kind == ScopeKind.PERSONAL.value:
            return await resolve_personal_execution_context(
                db,
                user_id=str(run.authority_user_id),
                channel="workflow",
                surface=SurfaceKind.SYSTEM_TASK,
                conversation_id=f"workflow:{run.id}",
                metadata=metadata,
            )
        if not run.workspace_id:
            raise ExecutionContextError("Workspace workflow has no workspace authority")
        return await resolve_execution_context(
            db,
            workspace_id=run.workspace_id,
            user_id=run.authority_user_id,
            channel="workflow",
            surface=SurfaceKind.SYSTEM_TASK,
            conversation_id=f"workflow:{run.id}",
            metadata=metadata,
        )

    async def _still_owned(
        self,
        db: AsyncSession,
        run: WorkflowRun,
        expected_lease_token: str | None,
    ) -> bool:
        await db.refresh(run)
        if run.status in _STOP_STATES:
            return False
        if expected_lease_token is not None and run.lease_token != expected_lease_token:
            return False
        return True

    async def execute_run(
        self,
        db: AsyncSession,
        run_id: str,
        *,
        expected_lease_token: str | None = None,
    ) -> WorkflowRun:
        run = await db.get(WorkflowRun, run_id)
        if run is None:
            raise LookupError("Workflow run is unavailable")
        if run.status in {
            "completed",
            "completed_with_errors",
            "cancelled",
            "failed",
            "orphaned",
        }:
            return run
        if expected_lease_token is not None and run.lease_token != expected_lease_token:
            return run

        workflow = await db.get(WorkflowDefinition, run.workflow_id)
        version = await db.get(WorkflowVersion, run.workflow_version_id)
        if workflow is None or version is None:
            return await self._fail(
                db,
                run,
                workflow,
                "workflow_unavailable",
                "Workflow definition/version is unavailable",
            )
        if str(workflow.scope_kind or "workspace") != str(run.scope_kind or "workspace"):
            return await self._fail(
                db, run, workflow, "scope_mismatch", "Workflow/run authority scope changed"
            )
        if workflow.status == "archived":
            return await self._fail(
                db, run, workflow, "workflow_archived", "Workflow has been archived"
            )
        if not run.authority_user_id:
            return await self._fail(
                db,
                run,
                workflow,
                "authority_unavailable",
                "Workflow run authority user is unavailable",
            )

        try:
            spec = validate_workflow_spec(_loads(version.spec_json, {}))
        except WorkflowSpecError as error:
            return await self._fail(db, run, workflow, "invalid_spec", str(error))

        first_start = run.started_at is None
        if first_start:
            run.started_at = datetime.utcnow()
        run.status = "running"
        run.error_code = None
        run.error_message = None
        await record_workflow_event(
            db,
            workspace_id=run.workspace_id,
            workflow_id=run.workflow_id,
            workflow_run_id=run.id,
            event_type="workflow.run.started" if first_start else "workflow.run.resumed",
            actor_type="system",
            actor_id="operly:workflow",
            owner_user_id=run.authority_user_id,
            principal_id=f"user:{run.authority_user_id}",
            payload={
                "scope_kind": run.scope_kind,
                "workflow_version_id": run.workflow_version_id,
                "current_step_key": run.current_step_key,
                "authority_user_id": run.authority_user_id,
            },
        )
        await db.commit()

        rows = (
            await db.scalars(
                select(WorkflowStepRun)
                .where(WorkflowStepRun.workflow_run_id == run.id)
                .order_by(WorkflowStepRun.step_order)
            )
        ).all()
        step_rows = {row.step_key: row for row in rows}

        for index, step in enumerate(spec["steps"]):
            if not await self._still_owned(db, run, expected_lease_token):
                return run

            step_key = str(step["id"])
            existing = step_rows.get(step_key)
            if existing and existing.status in {"completed", "skipped"}:
                continue

            missing_dependency = next(
                (
                    dependency
                    for dependency in step.get("depends_on", [])
                    if dependency not in step_rows
                    or step_rows[dependency].status not in {"completed", "skipped"}
                ),
                None,
            )
            if missing_dependency:
                return await self._fail(
                    db,
                    run,
                    workflow,
                    "dependency_unavailable",
                    f"Step {step_key} depends on unfinished step {missing_dependency}",
                )

            context_data = _step_context(run, step_rows)
            try:
                should_run = (
                    step.get("when") is None
                    or evaluate_condition(step["when"], context_data)
                )
            except WorkflowSpecError as error:
                return await self._fail(db, run, workflow, "condition_invalid", str(error))

            if not should_run:
                row = existing or WorkflowStepRun(
                    workflow_run_id=run.id,
                    step_key=step_key,
                    step_order=index,
                    step_kind=str(step["kind"]),
                    capability_id=step.get("capability_id"),
                )
                if existing is None:
                    db.add(row)
                row.status = "skipped"
                row.finished_at = datetime.utcnow()
                run.current_step_key = step_key
                await db.flush()
                step_rows[step_key] = row
                await record_workflow_event(
                    db,
                    workspace_id=run.workspace_id,
                    workflow_id=run.workflow_id,
                    workflow_run_id=run.id,
                    step_run_id=row.id,
                    event_type="workflow.step.skipped",
                    actor_id="operly:workflow",
                    owner_user_id=run.authority_user_id,
                    principal_id=f"user:{run.authority_user_id}",
                    payload={"step_key": step_key, "reason": "condition_false"},
                )
                await db.commit()
                continue

            if step["kind"] == "wait":
                try:
                    waiting = await self._wait_step(
                        db, run, existing, step, index, context_data
                    )
                except WorkflowSpecError as error:
                    return await self._fail(db, run, workflow, "wait_invalid", str(error))
                step_rows[step_key] = waiting
                if waiting.status == "waiting":
                    return run
                if not await self._still_owned(db, run, expected_lease_token):
                    return run
                continue

            action = await self._action_step(
                db, run, existing, step, index, context_data
            )
            step_rows[step_key] = action
            if action.status == "waiting_approval":
                return run
            if not await self._still_owned(db, run, expected_lease_token):
                return run
            if action.status == "failed" and step.get("on_error") != "continue":
                return await self._fail(
                    db,
                    run,
                    workflow,
                    action.error_code or "step_failed",
                    action.error_message or f"Step {step_key} failed",
                )

        if not await self._still_owned(db, run, expected_lease_token):
            return run

        failed_continued = [row for row in step_rows.values() if row.status == "failed"]
        run.status = "completed_with_errors" if failed_continued else "completed"
        run.current_step_key = None
        run.finished_at = datetime.utcnow()
        run.lease_token = None
        run.lease_until = None
        run.result_json = _dumps(
            {
                key: {
                    "status": row.status,
                    "attempt": row.attempt,
                    "result": _loads(row.result_json, {}),
                    "error": (
                        {"code": row.error_code, "message": row.error_message}
                        if row.error_code
                        else None
                    ),
                }
                for key, row in step_rows.items()
            }
        )
        await record_workflow_event(
            db,
            workspace_id=run.workspace_id,
            workflow_id=run.workflow_id,
            workflow_run_id=run.id,
            event_type=(
                "workflow.run.completed_with_errors"
                if failed_continued
                else "workflow.run.completed"
            ),
            actor_id="operly:workflow",
            owner_user_id=run.authority_user_id,
            principal_id=f"user:{run.authority_user_id}",
            payload={
                "step_count": len(spec["steps"]),
                "failed_continued_steps": [row.step_key for row in failed_continued],
            },
        )
        await db.commit()
        return run

    async def _wait_step(
        self,
        db: AsyncSession,
        run: WorkflowRun,
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
                step_kind="wait",
                status="pending",
            )
            db.add(row)
            await db.flush()

        if row.wait_until is None:
            if "seconds" in step:
                row.wait_until = now + timedelta(seconds=int(step["seconds"]))
            else:
                row.wait_until = _parse_until(render_value(step["until"], context_data))
                if row.wait_until > now + timedelta(seconds=MAX_WAIT_SECONDS):
                    raise WorkflowSpecError(
                        "Wait-until exceeds the maximum 31-day workflow wait"
                    )

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
                owner_user_id=run.authority_user_id,
                principal_id=f"user:{run.authority_user_id}",
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
            owner_user_id=run.authority_user_id,
            principal_id=f"user:{run.authority_user_id}",
            payload={"step_key": row.step_key, "kind": "wait"},
        )
        await db.commit()
        return row

    async def _current_attempt(
        self,
        db: AsyncSession,
        row: WorkflowStepRun,
    ) -> WorkflowStepAttempt | None:
        if row.attempt < 1:
            return None
        return await db.scalar(
            select(WorkflowStepAttempt).where(
                WorkflowStepAttempt.step_run_id == row.id,
                WorkflowStepAttempt.attempt == row.attempt,
            )
        )

    async def _fail_action_before_runtime(
        self,
        db: AsyncSession,
        run: WorkflowRun,
        row: WorkflowStepRun,
        attempt: WorkflowStepAttempt,
        *,
        code: str,
        message: str,
    ) -> WorkflowStepRun:
        now = datetime.utcnow()
        row.status = "failed"
        row.error_code = code
        row.error_message = message[:1000]
        row.finished_at = now
        attempt.status = "failed"
        attempt.error_code = code
        attempt.error_message = message[:1000]
        attempt.finished_at = now
        await record_workflow_event(
            db,
            workspace_id=run.workspace_id,
            workflow_id=run.workflow_id,
            workflow_run_id=run.id,
            step_run_id=row.id,
            step_attempt_id=attempt.id,
            event_type="workflow.step.failed",
            actor_id="operly:workflow",
            owner_user_id=run.authority_user_id,
            principal_id=f"user:{run.authority_user_id}" if run.authority_user_id else None,
            capability_id=row.capability_id,
            payload={
                "step_key": row.step_key,
                "attempt": row.attempt,
                "request_id": row.request_id,
                "code": code,
            },
        )
        await db.commit()
        return row

    async def _action_step(
        self,
        db: AsyncSession,
        run: WorkflowRun,
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

        approval_id: str | None = None
        attempt: WorkflowStepAttempt | None = None

        if row.status == "waiting_approval" and row.approval_id:
            attempt = await self._current_attempt(db, row)
            if attempt is None:
                row.attempt += 1
                row.request_id = row.request_id or (
                    f"workflow:{run.id}:{row.step_key}:attempt:{row.attempt}"
                )
                attempt = WorkflowStepAttempt(
                    workflow_run_id=run.id,
                    step_run_id=row.id,
                    attempt=row.attempt,
                    capability_id=str(row.capability_id or step["capability_id"]),
                    status="failed",
                    request_id=row.request_id,
                    arguments_json=row.arguments_json,
                    error_code="attempt_lineage_missing",
                    error_message="Workflow approval attempt lineage is unavailable",
                    started_at=now,
                    finished_at=now,
                )
                db.add(attempt)
                await db.flush()
                return await self._fail_action_before_runtime(
                    db,
                    run,
                    row,
                    attempt,
                    code="attempt_lineage_missing",
                    message="Workflow approval attempt lineage is unavailable",
                )

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
                attempt.status = "failed"
                attempt.approval_id = approval.id
                attempt.error_code = "approval_rejected"
                attempt.error_message = row.error_message
                attempt.finished_at = now
                run.current_step_key = row.step_key
                await record_workflow_event(
                    db,
                    workspace_id=run.workspace_id,
                    workflow_id=run.workflow_id,
                    workflow_run_id=run.id,
                    step_run_id=row.id,
                    step_attempt_id=attempt.id,
                    event_type="workflow.step.failed",
                    actor_type="human",
                    actor_id=approval.decided_by_user_id,
                    owner_user_id=run.authority_user_id,
                    principal_id=(
                        f"user:{run.authority_user_id}" if run.authority_user_id else None
                    ),
                    capability_id=row.capability_id,
                    kernel_run_id=row.kernel_run_id,
                    approval_id=approval.id,
                    payload={
                        "step_key": row.step_key,
                        "attempt": row.attempt,
                        "request_id": row.request_id,
                        "code": "approval_rejected",
                    },
                )
                await db.commit()
                return row
            approval_id = approval.id
            attempt.status = "running"
            attempt.approval_id = approval.id
        else:
            row.attempt += 1
            row.request_id = f"workflow:{run.id}:{row.step_key}:attempt:{row.attempt}"
            row.approval_id = None
            row.kernel_run_id = None
            row.error_code = None
            row.error_message = None
            row.result_json = "{}"
            row.finished_at = None
            try:
                rendered_arguments = render_value(step.get("arguments") or {}, context_data)
                if not isinstance(rendered_arguments, dict):
                    raise WorkflowSpecError("Rendered action arguments must be an object")
                row.arguments_json = _dumps(rendered_arguments)
            except WorkflowSpecError as error:
                row.arguments_json = "{}"
                attempt = WorkflowStepAttempt(
                    workflow_run_id=run.id,
                    step_run_id=row.id,
                    attempt=row.attempt,
                    capability_id=str(row.capability_id or step["capability_id"]),
                    status="failed",
                    request_id=row.request_id,
                    arguments_json="{}",
                    error_code="argument_render_failed",
                    error_message=str(error)[:1000],
                    started_at=now,
                    finished_at=now,
                )
                db.add(attempt)
                await db.flush()
                return await self._fail_action_before_runtime(
                    db,
                    run,
                    row,
                    attempt,
                    code="argument_render_failed",
                    message=str(error),
                )

            attempt = WorkflowStepAttempt(
                workflow_run_id=run.id,
                step_run_id=row.id,
                attempt=row.attempt,
                capability_id=str(row.capability_id or step["capability_id"]),
                status="running",
                request_id=row.request_id,
                arguments_json=row.arguments_json,
                started_at=now,
            )
            db.add(attempt)
            await db.flush()

        assert attempt is not None
        arguments = _loads(attempt.arguments_json, {})
        row.arguments_json = attempt.arguments_json
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
            step_attempt_id=attempt.id,
            event_type=(
                "workflow.step.started"
                if approval_id is None
                else "workflow.step.approval_resumed"
            ),
            actor_id="operly:workflow",
            owner_user_id=run.authority_user_id,
            principal_id=(
                f"user:{run.authority_user_id}" if run.authority_user_id else None
            ),
            capability_id=row.capability_id,
            approval_id=approval_id,
            payload={
                "step_key": row.step_key,
                "attempt": row.attempt,
                "request_id": row.request_id,
                "argument_keys": sorted(arguments),
            },
        )
        await db.commit()

        if not run.authority_user_id:
            return await self._fail_action_before_runtime(
                db,
                run,
                row,
                attempt,
                code="authority_unavailable",
                message="Workflow authority user is unavailable",
            )

        try:
            authority = await self._resolve_authority(
                db, run=run, row=row, attempt=attempt
            )
        except (ExecutionContextError, PermissionError) as error:
            row = await db.get(WorkflowStepRun, row.id)
            attempt = await db.get(WorkflowStepAttempt, attempt.id)
            run = await db.get(WorkflowRun, run.id)
            assert row is not None and attempt is not None and run is not None
            return await self._fail_action_before_runtime(
                db,
                run,
                row,
                attempt,
                code="authority_unavailable",
                message=str(error),
            )

        try:
            response = await self._runtime(run).execute(
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
            attempt = await db.get(WorkflowStepAttempt, attempt.id)
            run = await db.get(WorkflowRun, run.id)
            assert row is not None and attempt is not None and run is not None
            row.kernel_run_id = error.run_id
            attempt.kernel_run_id = error.run_id
            if error.code == "approval_required" and error.approval_id:
                row.status = "waiting_approval"
                row.approval_id = error.approval_id
                attempt.status = "waiting_approval"
                attempt.approval_id = error.approval_id
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
                    step_attempt_id=attempt.id,
                    event_type="workflow.step.waiting_approval",
                    actor_id="operly:workflow",
                    owner_user_id=run.authority_user_id,
                    principal_id=(
                        f"user:{run.authority_user_id}" if run.authority_user_id else None
                    ),
                    capability_id=row.capability_id,
                    kernel_run_id=error.run_id,
                    approval_id=error.approval_id,
                    payload={
                        "step_key": row.step_key,
                        "attempt": row.attempt,
                        "request_id": row.request_id,
                    },
                )
            else:
                row.status = "failed"
                row.error_code = error.code
                row.error_message = str(error)[:1000]
                row.finished_at = datetime.utcnow()
                attempt.status = "failed"
                attempt.error_code = error.code
                attempt.error_message = str(error)[:1000]
                attempt.finished_at = row.finished_at
                await record_workflow_event(
                    db,
                    workspace_id=run.workspace_id,
                    workflow_id=run.workflow_id,
                    workflow_run_id=run.id,
                    step_run_id=row.id,
                    step_attempt_id=attempt.id,
                    event_type="workflow.step.failed",
                    actor_id="operly:workflow",
                    owner_user_id=run.authority_user_id,
                    principal_id=(
                        f"user:{run.authority_user_id}" if run.authority_user_id else None
                    ),
                    capability_id=row.capability_id,
                    kernel_run_id=error.run_id,
                    approval_id=approval_id,
                    payload={
                        "step_key": row.step_key,
                        "attempt": row.attempt,
                        "request_id": row.request_id,
                        "code": error.code,
                    },
                )
            await db.commit()
            return row

        row = await db.get(WorkflowStepRun, row.id)
        attempt = await db.get(WorkflowStepAttempt, attempt.id)
        run = await db.get(WorkflowRun, run.id)
        assert row is not None and attempt is not None and run is not None
        finished = datetime.utcnow()
        row.status = "completed"
        row.kernel_run_id = response.run_id
        row.result_json = _dumps(response.result or {})
        row.error_code = None
        row.error_message = None
        row.finished_at = finished
        attempt.status = "completed"
        attempt.kernel_run_id = response.run_id
        attempt.result_json = row.result_json
        attempt.error_code = None
        attempt.error_message = None
        attempt.finished_at = finished
        await record_workflow_event(
            db,
            workspace_id=run.workspace_id,
            workflow_id=run.workflow_id,
            workflow_run_id=run.id,
            step_run_id=row.id,
            step_attempt_id=attempt.id,
            event_type="workflow.step.completed",
            actor_id="operly:workflow",
            owner_user_id=run.authority_user_id,
            principal_id=(
                f"user:{run.authority_user_id}" if run.authority_user_id else None
            ),
            capability_id=row.capability_id,
            kernel_run_id=response.run_id,
            approval_id=approval_id,
            payload={
                "step_key": row.step_key,
                "attempt": row.attempt,
                "request_id": row.request_id,
                "result_keys": sorted((response.result or {}).keys()),
            },
        )
        await db.commit()
        return row

    async def _fail(
        self,
        db: AsyncSession,
        run: WorkflowRun,
        workflow: WorkflowDefinition | None,
        code: str,
        message: str,
    ) -> WorkflowRun:
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
                owner_user_id=run.authority_user_id,
                principal_id=(
                    f"user:{run.authority_user_id}" if run.authority_user_id else None
                ),
                payload={"code": code, "current_step_key": run.current_step_key},
            )
        await db.commit()
        return run


workflow_engine = WorkflowEngine()