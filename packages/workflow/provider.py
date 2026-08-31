from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.kernel.contracts import CapabilityExecutionResult, CapabilityRisk, CapabilitySpec
from packages.security.execution_context import ExecutionContext
from packages.workflow.engine import queue_workflow_run
from packages.workflow.models import (
    WorkflowDefinition,
    WorkflowRun,
    WorkflowSchedule,
    WorkflowStepRun,
    WorkflowTraceEvent,
    WorkflowVersion,
)
from packages.workflow.spec import next_schedule_time, validate_schedule, validate_workflow_spec
from packages.workflow.tracing import record_workflow_event


PROVIDER_ID = "operly.workflow"


def _object(properties: dict[str, Any], *, required: list[str] | None = None, additional: bool = False) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": additional}


def _capability(
    capability_id: str,
    name: str,
    description: str,
    *,
    permission: str,
    input_schema: dict[str, Any] | None = None,
    risk: CapabilityRisk = CapabilityRisk.READ_ONLY,
    approval: bool = False,
    reversible: bool = False,
    emits: tuple[str, ...] = (),
) -> CapabilitySpec:
    return CapabilitySpec(
        id=capability_id,
        version="1.0.0",
        display_name=name,
        description=description,
        provider_id=PROVIDER_ID,
        scopes=frozenset({"workspace"}),
        input_schema=input_schema or _object({}),
        output_schema=_object({}, additional=True),
        permissions=(permission,),
        risk=risk,
        approval_required=approval,
        reversible=reversible,
        emits=emits,
        tags=frozenset({"workspace", "workflow", "automation", "traceable", "deterministic"}),
        resource_scope="workspace",
    )


def workflow_capabilities() -> tuple[CapabilitySpec, ...]:
    schedule_schema = _object(
        {
            "type": {"type": "string", "enum": ["manual", "once", "interval", "daily", "weekly", "cron"]},
            "timezone": {"type": "string", "maxLength": 80},
            "at": {"type": "string", "maxLength": 80},
            "every_seconds": {"type": "integer", "minimum": 60},
            "start_at": {"type": "string", "maxLength": 80},
            "time": {"type": "string", "maxLength": 5},
            "days": {"type": "array", "items": {"type": "integer"}, "maxItems": 7},
            "expression": {"type": "string", "maxLength": 120},
        },
        additional=False,
    )
    spec_schema = _object(
        {
            "steps": {
                "type": "array",
                "minItems": 1,
                "maxItems": 100,
                "items": _object({}, additional=True),
            }
        },
        required=["steps"],
    )
    return (
        _capability("workflow.list", "List workflows", "List durable workflows in this workspace, including schedule and current version.", permission="workflows:read", input_schema=_object({"include_archived": {"type": "boolean"}, "limit": {"type": "integer", "minimum": 1, "maximum": 200}})),
        _capability("workflow.get", "Read workflow", "Read one workflow definition, immutable current version, schedule, and recent runs.", permission="workflows:read", input_schema=_object({"workflow_id": {"type": "string"}}, required=["workflow_id"])),
        _capability(
            "workflow.create",
            "Create workflow",
            "Create a versioned workflow whose action steps can invoke any normal Workspace capability. Scheduled execution never receives extra authority.",
            permission="workflows:write",
            input_schema=_object({"name": {"type": "string", "minLength": 1, "maxLength": 200}, "description": {"type": "string", "maxLength": 10000}, "spec": spec_schema, "schedule": {"type": ["object", "null"], "properties": schedule_schema["properties"], "additionalProperties": False}, "enabled": {"type": "boolean"}}, required=["name", "spec"]),
            risk=CapabilityRisk.HIGH,
            approval=True,
            reversible=True,
            emits=("workflow.created",),
        ),
        _capability(
            "workflow.update",
            "Update workflow",
            "Update workflow metadata, schedule, or steps. Step changes create an immutable new workflow version.",
            permission="workflows:write",
            input_schema=_object({"workflow_id": {"type": "string"}, "name": {"type": ["string", "null"], "maxLength": 200}, "description": {"type": ["string", "null"], "maxLength": 10000}, "spec": {"type": ["object", "null"], "properties": spec_schema["properties"], "additionalProperties": False}, "schedule": {"type": ["object", "null"], "properties": schedule_schema["properties"], "additionalProperties": False}}, required=["workflow_id"]),
            risk=CapabilityRisk.HIGH,
            approval=True,
            reversible=True,
            emits=("workflow.updated",),
        ),
        _capability("workflow.enable", "Enable workflow", "Allow this workflow's schedule to enqueue future runs.", permission="workflows:write", input_schema=_object({"workflow_id": {"type": "string"}}, required=["workflow_id"]), risk=CapabilityRisk.HIGH, approval=True, reversible=True, emits=("workflow.enabled",)),
        _capability("workflow.disable", "Disable workflow", "Stop future scheduled runs without deleting definitions, versions, runs, or trace history.", permission="workflows:write", input_schema=_object({"workflow_id": {"type": "string"}}, required=["workflow_id"]), risk=CapabilityRisk.LOW, reversible=True, emits=("workflow.disabled",)),
        _capability("workflow.archive", "Archive workflow", "Archive a workflow while preserving every version, run, step, and trace event.", permission="workflows:write", input_schema=_object({"workflow_id": {"type": "string"}}, required=["workflow_id"]), risk=CapabilityRisk.HIGH, approval=True, reversible=False, emits=("workflow.archived",)),
        _capability("workflow.run.start", "Run workflow now", "Queue a manual workflow run. Each action step is still independently authorized by the normal Workspace capability policy.", permission="workflows:run", input_schema=_object({"workflow_id": {"type": "string"}, "trigger": {"type": "object"}}, required=["workflow_id"]), risk=CapabilityRisk.LOW, emits=("workflow.run.requested",)),
        _capability("workflow.run.list", "List workflow runs", "List workflow executions with trigger, status, current step, and timestamps.", permission="workflows:read", input_schema=_object({"workflow_id": {"type": ["string", "null"]}, "status": {"type": ["string", "null"]}, "limit": {"type": "integer", "minimum": 1, "maximum": 200}})),
        _capability("workflow.run.get", "Inspect workflow run", "Inspect a workflow run with every step, attempt, Kernel run ID, approval ID, rendered arguments, result, and failure.", permission="workflows:read", input_schema=_object({"run_id": {"type": "string"}}, required=["run_id"])),
        _capability("workflow.run.cancel", "Cancel workflow run", "Cancel a queued, waiting, or running workflow before another workflow step is dispatched.", permission="workflows:run", input_schema=_object({"run_id": {"type": "string"}}, required=["run_id"]), risk=CapabilityRisk.LOW, emits=("workflow.run.cancelled",)),
        _capability("workflow.run.retry", "Retry failed workflow run", "Resume a failed workflow from its failed step while preserving already completed steps and creating a new traced attempt.", permission="workflows:run", input_schema=_object({"run_id": {"type": "string"}}, required=["run_id"]), risk=CapabilityRisk.MEDIUM, approval=True, emits=("workflow.run.retry_requested",)),
        _capability("workflow.trace", "Read workflow trace", "Read the durable orchestration trace that correlates workflow runs and steps with Kernel runs and approvals.", permission="workflows:read", input_schema=_object({"workflow_id": {"type": ["string", "null"]}, "run_id": {"type": ["string", "null"]}, "limit": {"type": "integer", "minimum": 1, "maximum": 500}})),
        _capability("workflow.schedule.preview", "Preview workflow schedule", "Validate a schedule and show its next occurrences without enabling anything.", permission="workflows:read", input_schema=_object({"schedule": schedule_schema, "count": {"type": "integer", "minimum": 1, "maximum": 20}}, required=["schedule"])),
    )


def _loads(raw: str | None, fallback: Any) -> Any:
    try:
        return json.loads(raw or "")
    except Exception:
        return fallback


def _dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True, default=str)


def _workflow_json(row: WorkflowDefinition, schedule: WorkflowSchedule | None = None) -> dict[str, Any]:
    return {"id": row.id, "name": row.name, "description": row.description, "status": row.status, "owner_user_id": row.owner_user_id, "current_version": row.current_version, "schedule": _loads(schedule.schedule_json, {}) if schedule else None, "next_run_at": schedule.next_run_at.isoformat() if schedule and schedule.next_run_at else None, "created_at": row.created_at.isoformat(), "updated_at": row.updated_at.isoformat()}


def _run_json(row: WorkflowRun) -> dict[str, Any]:
    return {"id": row.id, "workflow_id": row.workflow_id, "workflow_version_id": row.workflow_version_id, "status": row.status, "trigger_type": row.trigger_type, "trigger": _loads(row.trigger_payload_json, {}), "current_step_key": row.current_step_key, "error_code": row.error_code, "error_message": row.error_message, "scheduled_for": row.scheduled_for.isoformat() if row.scheduled_for else None, "created_at": row.created_at.isoformat(), "started_at": row.started_at.isoformat() if row.started_at else None, "finished_at": row.finished_at.isoformat() if row.finished_at else None}


def _step_json(row: WorkflowStepRun) -> dict[str, Any]:
    return {"id": row.id, "step_key": row.step_key, "step_order": row.step_order, "kind": row.step_kind, "capability_id": row.capability_id, "status": row.status, "attempt": row.attempt, "request_id": row.request_id, "kernel_run_id": row.kernel_run_id, "approval_id": row.approval_id, "arguments": _loads(row.arguments_json, {}), "result": _loads(row.result_json, {}), "error_code": row.error_code, "error_message": row.error_message, "wait_until": row.wait_until.isoformat() if row.wait_until else None, "started_at": row.started_at.isoformat() if row.started_at else None, "finished_at": row.finished_at.isoformat() if row.finished_at else None}


class WorkflowProvider:
    def __init__(self) -> None:
        self._handlers = {
            "workflow.list": self._list,
            "workflow.get": self._get,
            "workflow.create": self._create,
            "workflow.update": self._update,
            "workflow.enable": self._enable,
            "workflow.disable": self._disable,
            "workflow.archive": self._archive,
            "workflow.run.start": self._start_run,
            "workflow.run.list": self._list_runs,
            "workflow.run.get": self._get_run,
            "workflow.run.cancel": self._cancel_run,
            "workflow.run.retry": self._retry_run,
            "workflow.trace": self._trace,
            "workflow.schedule.preview": self._schedule_preview,
        }

    async def execute(self, db: AsyncSession, *, context: ExecutionContext, capability: CapabilitySpec, arguments: dict[str, Any], minimum_context: dict[str, Any]) -> CapabilityExecutionResult:
        del minimum_context
        if not context.workspace_id:
            raise PermissionError("Workflow requires Workspace authority")
        handler = self._handlers.get(capability.id)
        if handler is None:
            raise LookupError("Workflow capability is not implemented")
        return await handler(db, context, arguments)

    async def _workflow(self, db: AsyncSession, context: ExecutionContext, workflow_id: str) -> WorkflowDefinition:
        row = await db.scalar(select(WorkflowDefinition).where(WorkflowDefinition.id == workflow_id, WorkflowDefinition.workspace_id == context.workspace_id))
        if row is None:
            raise LookupError("Workflow is unavailable")
        return row

    async def _run(self, db: AsyncSession, context: ExecutionContext, run_id: str) -> WorkflowRun:
        row = await db.scalar(select(WorkflowRun).where(WorkflowRun.id == run_id, WorkflowRun.workspace_id == context.workspace_id))
        if row is None:
            raise LookupError("Workflow run is unavailable")
        return row

    async def _list(self, db: AsyncSession, context: ExecutionContext, arguments: dict[str, Any]) -> CapabilityExecutionResult:
        statement = select(WorkflowDefinition).where(WorkflowDefinition.workspace_id == context.workspace_id)
        if not bool(arguments.get("include_archived")):
            statement = statement.where(WorkflowDefinition.status != "archived")
        rows = (await db.scalars(statement.order_by(WorkflowDefinition.updated_at.desc()).limit(max(1, min(int(arguments.get("limit") or 100), 200))))).all()
        schedules = {row.workflow_id: row for row in (await db.scalars(select(WorkflowSchedule).where(WorkflowSchedule.workflow_id.in_([item.id for item in rows])))).all()} if rows else {}
        return CapabilityExecutionResult(value={"workflows": [_workflow_json(row, schedules.get(row.id)) for row in rows]})

    async def _get(self, db: AsyncSession, context: ExecutionContext, arguments: dict[str, Any]) -> CapabilityExecutionResult:
        row = await self._workflow(db, context, str(arguments["workflow_id"]))
        version = await db.scalar(select(WorkflowVersion).where(WorkflowVersion.workflow_id == row.id, WorkflowVersion.version == row.current_version))
        schedule = await db.scalar(select(WorkflowSchedule).where(WorkflowSchedule.workflow_id == row.id))
        runs = (await db.scalars(select(WorkflowRun).where(WorkflowRun.workflow_id == row.id).order_by(WorkflowRun.created_at.desc()).limit(20))).all()
        return CapabilityExecutionResult(value={"workflow": _workflow_json(row, schedule), "spec": _loads(version.spec_json, {}) if version else None, "version_id": version.id if version else None, "recent_runs": [_run_json(item) for item in runs]}, resource_type="workflow", resource_id=row.id)

    async def _create(self, db: AsyncSession, context: ExecutionContext, arguments: dict[str, Any]) -> CapabilityExecutionResult:
        if not context.user_id:
            raise PermissionError("Workflow owner must be an Operly user")
        spec = validate_workflow_spec(arguments["spec"])
        schedule_spec = validate_schedule(arguments.get("schedule"))
        enabled = bool(arguments.get("enabled"))
        row = WorkflowDefinition(workspace_id=context.workspace_id, owner_user_id=context.user_id, name=str(arguments["name"]).strip(), description=str(arguments.get("description") or ""), status="enabled" if enabled else "disabled", current_version=1)
        db.add(row)
        await db.flush()
        version = WorkflowVersion(workflow_id=row.id, version=1, spec_json=_dumps(spec), created_by_user_id=context.user_id)
        db.add(version)
        schedule = None
        if schedule_spec:
            schedule = WorkflowSchedule(workflow_id=row.id, schedule_type=schedule_spec["type"], schedule_json=_dumps(schedule_spec), timezone=schedule_spec.get("timezone", "UTC"), enabled=enabled, next_run_at=next_schedule_time(schedule_spec, after=datetime.utcnow() - timedelta(seconds=1)) if enabled else None)
            db.add(schedule)
        await record_workflow_event(db, workspace_id=context.workspace_id, workflow_id=row.id, event_type="workflow.created", actor_type="human", actor_id=context.user_id, owner_user_id=context.user_id, principal_id=context.principal_id, payload={"version": 1, "enabled": enabled, "schedule_type": schedule_spec.get("type") if schedule_spec else "manual"})
        await db.flush()
        return CapabilityExecutionResult(value={"workflow": _workflow_json(row, schedule), "spec": spec}, resource_type="workflow", resource_id=row.id, event_payload={"workflow_id": row.id})

    async def _update(self, db: AsyncSession, context: ExecutionContext, arguments: dict[str, Any]) -> CapabilityExecutionResult:
        row = await self._workflow(db, context, str(arguments["workflow_id"]))
        if row.status == "archived": raise ValueError("Archived workflow cannot be edited")
        changed: list[str] = []
        if arguments.get("name") is not None:
            row.name = str(arguments["name"]).strip(); changed.append("name")
        if arguments.get("description") is not None:
            row.description = str(arguments["description"]); changed.append("description")
        if arguments.get("spec") is not None:
            spec = validate_workflow_spec(arguments["spec"])
            row.current_version += 1
            db.add(WorkflowVersion(workflow_id=row.id, version=row.current_version, spec_json=_dumps(spec), created_by_user_id=context.user_id))
            changed.append("spec")
        schedule = await db.scalar(select(WorkflowSchedule).where(WorkflowSchedule.workflow_id == row.id))
        if "schedule" in arguments:
            schedule_spec = validate_schedule(arguments.get("schedule"))
            if schedule_spec is None:
                if schedule is not None: await db.delete(schedule); schedule = None
            else:
                if schedule is None:
                    schedule = WorkflowSchedule(workflow_id=row.id, schedule_type=schedule_spec["type"], schedule_json=_dumps(schedule_spec), timezone=schedule_spec.get("timezone", "UTC"))
                    db.add(schedule)
                schedule.schedule_type = schedule_spec["type"]
                schedule.schedule_json = _dumps(schedule_spec)
                schedule.timezone = schedule_spec.get("timezone", "UTC")
                schedule.enabled = row.status == "enabled"
                schedule.next_run_at = next_schedule_time(schedule_spec, after=datetime.utcnow() - timedelta(seconds=1)) if schedule.enabled else None
            changed.append("schedule")
        await record_workflow_event(db, workspace_id=context.workspace_id, workflow_id=row.id, event_type="workflow.updated", actor_type="human", actor_id=context.user_id, owner_user_id=row.owner_user_id, principal_id=context.principal_id, payload={"version": row.current_version, "changed": changed})
        await db.flush()
        return CapabilityExecutionResult(value={"workflow": _workflow_json(row, schedule), "changed": changed}, resource_type="workflow", resource_id=row.id, event_payload={"workflow_id": row.id})

    async def _enable(self, db: AsyncSession, context: ExecutionContext, arguments: dict[str, Any]) -> CapabilityExecutionResult:
        row = await self._workflow(db, context, str(arguments["workflow_id"]))
        if row.status == "archived": raise ValueError("Archived workflow cannot be enabled")
        row.status = "enabled"
        schedule = await db.scalar(select(WorkflowSchedule).where(WorkflowSchedule.workflow_id == row.id))
        if schedule:
            schedule.enabled = True
            schedule.next_run_at = next_schedule_time(_loads(schedule.schedule_json, {}), after=datetime.utcnow() - timedelta(seconds=1))
        await record_workflow_event(db, workspace_id=context.workspace_id, workflow_id=row.id, event_type="workflow.enabled", actor_type="human", actor_id=context.user_id, owner_user_id=row.owner_user_id, principal_id=context.principal_id)
        return CapabilityExecutionResult(value={"workflow": _workflow_json(row, schedule)}, resource_type="workflow", resource_id=row.id)

    async def _disable(self, db: AsyncSession, context: ExecutionContext, arguments: dict[str, Any]) -> CapabilityExecutionResult:
        row = await self._workflow(db, context, str(arguments["workflow_id"]))
        if row.status != "archived": row.status = "disabled"
        schedule = await db.scalar(select(WorkflowSchedule).where(WorkflowSchedule.workflow_id == row.id))
        if schedule: schedule.enabled = False; schedule.next_run_at = None
        await record_workflow_event(db, workspace_id=context.workspace_id, workflow_id=row.id, event_type="workflow.disabled", actor_type="human", actor_id=context.user_id, owner_user_id=row.owner_user_id, principal_id=context.principal_id)
        return CapabilityExecutionResult(value={"workflow": _workflow_json(row, schedule)}, resource_type="workflow", resource_id=row.id)

    async def _archive(self, db: AsyncSession, context: ExecutionContext, arguments: dict[str, Any]) -> CapabilityExecutionResult:
        row = await self._workflow(db, context, str(arguments["workflow_id"]))
        row.status = "archived"
        schedule = await db.scalar(select(WorkflowSchedule).where(WorkflowSchedule.workflow_id == row.id))
        if schedule: schedule.enabled = False; schedule.next_run_at = None
        await record_workflow_event(db, workspace_id=context.workspace_id, workflow_id=row.id, event_type="workflow.archived", actor_type="human", actor_id=context.user_id, owner_user_id=row.owner_user_id, principal_id=context.principal_id)
        return CapabilityExecutionResult(value={"workflow": _workflow_json(row, schedule)}, resource_type="workflow", resource_id=row.id)

    async def _start_run(self, db: AsyncSession, context: ExecutionContext, arguments: dict[str, Any]) -> CapabilityExecutionResult:
        row = await self._workflow(db, context, str(arguments["workflow_id"]))
        if row.status == "archived": raise ValueError("Archived workflow cannot run")
        run = await queue_workflow_run(db, workflow=row, trigger_type="manual", trigger_payload=arguments.get("trigger") if isinstance(arguments.get("trigger"), dict) else {}, initiated_by_user_id=context.user_id)
        return CapabilityExecutionResult(value={"run": _run_json(run)}, resource_type="workflow_run", resource_id=run.id, event_payload={"workflow_id": row.id, "workflow_run_id": run.id})

    async def _list_runs(self, db: AsyncSession, context: ExecutionContext, arguments: dict[str, Any]) -> CapabilityExecutionResult:
        statement = select(WorkflowRun).where(WorkflowRun.workspace_id == context.workspace_id)
        if arguments.get("workflow_id"): statement = statement.where(WorkflowRun.workflow_id == str(arguments["workflow_id"]))
        if arguments.get("status"): statement = statement.where(WorkflowRun.status == str(arguments["status"]))
        rows = (await db.scalars(statement.order_by(WorkflowRun.created_at.desc()).limit(max(1, min(int(arguments.get("limit") or 100), 200))))).all()
        return CapabilityExecutionResult(value={"runs": [_run_json(row) for row in rows]})

    async def _get_run(self, db: AsyncSession, context: ExecutionContext, arguments: dict[str, Any]) -> CapabilityExecutionResult:
        run = await self._run(db, context, str(arguments["run_id"]))
        steps = (await db.scalars(select(WorkflowStepRun).where(WorkflowStepRun.workflow_run_id == run.id).order_by(WorkflowStepRun.step_order))).all()
        return CapabilityExecutionResult(value={"run": _run_json(run), "steps": [_step_json(row) for row in steps], "result": _loads(run.result_json, {})}, resource_type="workflow_run", resource_id=run.id)

    async def _cancel_run(self, db: AsyncSession, context: ExecutionContext, arguments: dict[str, Any]) -> CapabilityExecutionResult:
        run = await self._run(db, context, str(arguments["run_id"]))
        if run.status not in {"completed", "failed", "cancelled"}:
            run.status = "cancelled"; run.finished_at = datetime.utcnow(); run.lease_token = None; run.lease_until = None
            await record_workflow_event(db, workspace_id=context.workspace_id, workflow_id=run.workflow_id, workflow_run_id=run.id, event_type="workflow.run.cancelled", actor_type="human", actor_id=context.user_id, owner_user_id=run.owner_user_id, principal_id=context.principal_id, payload={"current_step_key": run.current_step_key})
        return CapabilityExecutionResult(value={"run": _run_json(run)}, resource_type="workflow_run", resource_id=run.id)

    async def _retry_run(self, db: AsyncSession, context: ExecutionContext, arguments: dict[str, Any]) -> CapabilityExecutionResult:
        run = await self._run(db, context, str(arguments["run_id"]))
        if run.status != "failed": raise ValueError("Only failed workflow runs can be retried")
        failed = await db.scalar(select(WorkflowStepRun).where(WorkflowStepRun.workflow_run_id == run.id, WorkflowStepRun.status == "failed").order_by(WorkflowStepRun.step_order.desc()))
        if failed:
            failed.status = "pending"; failed.request_id = None; failed.kernel_run_id = None; failed.approval_id = None; failed.error_code = None; failed.error_message = None; failed.finished_at = None
        run.status = "queued"; run.error_code = None; run.error_message = None; run.finished_at = None; run.lease_token = None; run.lease_until = None
        await record_workflow_event(db, workspace_id=context.workspace_id, workflow_id=run.workflow_id, workflow_run_id=run.id, step_run_id=failed.id if failed else None, event_type="workflow.run.retry_requested", actor_type="human", actor_id=context.user_id, owner_user_id=run.owner_user_id, principal_id=context.principal_id, payload={"step_key": failed.step_key if failed else None})
        return CapabilityExecutionResult(value={"run": _run_json(run)}, resource_type="workflow_run", resource_id=run.id)

    async def _trace(self, db: AsyncSession, context: ExecutionContext, arguments: dict[str, Any]) -> CapabilityExecutionResult:
        statement = select(WorkflowTraceEvent).where(WorkflowTraceEvent.workspace_id == context.workspace_id)
        if arguments.get("workflow_id"): statement = statement.where(WorkflowTraceEvent.workflow_id == str(arguments["workflow_id"]))
        if arguments.get("run_id"): statement = statement.where(WorkflowTraceEvent.workflow_run_id == str(arguments["run_id"]))
        rows = (await db.scalars(statement.order_by(WorkflowTraceEvent.created_at.desc()).limit(max(1, min(int(arguments.get("limit") or 200), 500))))).all()
        return CapabilityExecutionResult(value={"events": [{"id": row.id, "event_type": row.event_type, "workflow_id": row.workflow_id, "workflow_run_id": row.workflow_run_id, "step_run_id": row.step_run_id, "capability_id": row.capability_id, "kernel_run_id": row.kernel_run_id, "approval_id": row.approval_id, "actor_type": row.actor_type, "actor_id": row.actor_id, "payload": _loads(row.payload_json, {}), "created_at": row.created_at.isoformat()} for row in rows]})

    async def _schedule_preview(self, db: AsyncSession, context: ExecutionContext, arguments: dict[str, Any]) -> CapabilityExecutionResult:
        del db, context
        schedule = validate_schedule(arguments["schedule"])
        if schedule is None: return CapabilityExecutionResult(value={"schedule": None, "occurrences": []})
        count = max(1, min(int(arguments.get("count") or 5), 20))
        cursor = datetime.utcnow() - timedelta(seconds=1)
        occurrences: list[str] = []
        for _ in range(count):
            next_at = next_schedule_time(schedule, after=cursor)
            if next_at is None: break
            occurrences.append(next_at.isoformat()); cursor = next_at
        return CapabilityExecutionResult(value={"schedule": schedule, "occurrences": occurrences})
