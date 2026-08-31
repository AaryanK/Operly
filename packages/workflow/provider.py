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
    WorkflowStepAttempt,
    WorkflowStepRun,
    WorkflowTraceEvent,
    WorkflowVersion,
)
from packages.workflow.spec import next_schedule_time, validate_schedule, validate_workflow_spec
from packages.workflow.tracing import record_workflow_event


PROVIDER_ID = "operly.workflow"


def _object(
    properties: dict[str, Any],
    *,
    required: list[str] | None = None,
    additional: bool = False,
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": additional,
    }


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
        tags=frozenset(
            {"workspace", "workflow", "automation", "traceable", "deterministic"}
        ),
        resource_scope="workspace",
    )


def workflow_capabilities() -> tuple[CapabilitySpec, ...]:
    schedule_schema = _object(
        {
            "type": {
                "type": "string",
                "enum": ["manual", "once", "interval", "daily", "weekly", "cron"],
            },
            "timezone": {"type": "string", "maxLength": 80},
            "at": {"type": "string", "maxLength": 80},
            "every_seconds": {"type": "integer", "minimum": 60},
            "start_at": {"type": "string", "maxLength": 80},
            "time": {"type": "string", "maxLength": 5},
            "days": {
                "type": "array",
                "items": {"type": "integer"},
                "maxItems": 7,
            },
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
    workflow_id = {"type": "string", "minLength": 1, "maxLength": 80}
    run_id = {"type": "string", "minLength": 1, "maxLength": 80}
    return (
        _capability(
            "workflow.list",
            "List workflows",
            "List durable workflows in this workspace, including schedule and current version.",
            permission="workflows:read",
            input_schema=_object(
                {
                    "include_archived": {"type": "boolean"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                }
            ),
        ),
        _capability(
            "workflow.get",
            "Read workflow",
            "Read one workflow, current immutable definition snapshot, schedule, and recent runs.",
            permission="workflows:read",
            input_schema=_object({"workflow_id": workflow_id}, required=["workflow_id"]),
        ),
        _capability(
            "workflow.version.list",
            "List workflow versions",
            "List immutable editable-definition versions for one workflow.",
            permission="workflows:read",
            input_schema=_object(
                {
                    "workflow_id": workflow_id,
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                },
                required=["workflow_id"],
            ),
        ),
        _capability(
            "workflow.version.get",
            "Read workflow version",
            "Read the exact step spec and full definition snapshot for one immutable workflow version.",
            permission="workflows:read",
            input_schema=_object(
                {
                    "workflow_id": workflow_id,
                    "version": {"type": "integer", "minimum": 1},
                },
                required=["workflow_id", "version"],
            ),
        ),
        _capability(
            "workflow.create",
            "Create workflow",
            "Create a versioned workflow whose action steps can invoke any normal Workspace capability. Scheduled execution never receives extra authority.",
            permission="workflows:write",
            input_schema=_object(
                {
                    "name": {"type": "string", "minLength": 1, "maxLength": 200},
                    "description": {"type": "string", "maxLength": 10000},
                    "spec": spec_schema,
                    "schedule": {
                        "type": ["object", "null"],
                        "properties": schedule_schema["properties"],
                        "additionalProperties": False,
                    },
                    "enabled": {"type": "boolean"},
                },
                required=["name", "spec"],
            ),
            risk=CapabilityRisk.HIGH,
            approval=True,
            reversible=True,
            emits=("workflow.created",),
        ),
        _capability(
            "workflow.update",
            "Update workflow",
            "Update workflow metadata, schedule, or steps. Every editable change creates an immutable definition version.",
            permission="workflows:write",
            input_schema=_object(
                {
                    "workflow_id": workflow_id,
                    "name": {"type": ["string", "null"], "maxLength": 200},
                    "description": {"type": ["string", "null"], "maxLength": 10000},
                    "spec": {
                        "type": ["object", "null"],
                        "properties": spec_schema["properties"],
                        "additionalProperties": False,
                    },
                    "schedule": {
                        "type": ["object", "null"],
                        "properties": schedule_schema["properties"],
                        "additionalProperties": False,
                    },
                },
                required=["workflow_id"],
            ),
            risk=CapabilityRisk.HIGH,
            approval=True,
            reversible=True,
            emits=("workflow.updated",),
        ),
        _capability(
            "workflow.enable",
            "Enable workflow",
            "Allow this workflow's schedule to enqueue future runs.",
            permission="workflows:write",
            input_schema=_object({"workflow_id": workflow_id}, required=["workflow_id"]),
            risk=CapabilityRisk.HIGH,
            approval=True,
            reversible=True,
            emits=("workflow.enabled",),
        ),
        _capability(
            "workflow.disable",
            "Disable workflow",
            "Stop future scheduled runs without deleting definitions, versions, runs, attempts, or trace history.",
            permission="workflows:write",
            input_schema=_object({"workflow_id": workflow_id}, required=["workflow_id"]),
            risk=CapabilityRisk.LOW,
            reversible=True,
            emits=("workflow.disabled",),
        ),
        _capability(
            "workflow.archive",
            "Archive workflow",
            "Archive a workflow while preserving every version, run, step, attempt, and trace event.",
            permission="workflows:write",
            input_schema=_object({"workflow_id": workflow_id}, required=["workflow_id"]),
            risk=CapabilityRisk.HIGH,
            approval=True,
            reversible=False,
            emits=("workflow.archived",),
        ),
        _capability(
            "workflow.run.start",
            "Run workflow now",
            "Queue a manual workflow run. Each action step is independently authorized by the normal Workspace capability policy.",
            permission="workflows:run",
            input_schema=_object(
                {"workflow_id": workflow_id, "trigger": {"type": "object"}},
                required=["workflow_id"],
            ),
            risk=CapabilityRisk.LOW,
            emits=("workflow.run.requested",),
        ),
        _capability(
            "workflow.run.list",
            "List workflow runs",
            "List workflow executions with trigger, authority user, status, current step, and timestamps.",
            permission="workflows:read",
            input_schema=_object(
                {
                    "workflow_id": {"type": ["string", "null"]},
                    "status": {"type": ["string", "null"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                }
            ),
        ),
        _capability(
            "workflow.run.get",
            "Inspect workflow run",
            "Inspect the pinned definition version plus every step and immutable action attempt, including exact arguments/results and Kernel/approval correlation.",
            permission="workflows:read",
            input_schema=_object({"run_id": run_id}, required=["run_id"]),
        ),
        _capability(
            "workflow.run.cancel",
            "Cancel workflow run",
            "Cancel a queued, waiting, or running workflow before another workflow step is dispatched.",
            permission="workflows:run",
            input_schema=_object({"run_id": run_id}, required=["run_id"]),
            risk=CapabilityRisk.LOW,
            emits=("workflow.run.cancelled",),
        ),
        _capability(
            "workflow.run.retry",
            "Retry failed workflow run",
            "Resume a failed workflow from its failed step while preserving completed steps and every previous immutable attempt.",
            permission="workflows:run",
            input_schema=_object({"run_id": run_id}, required=["run_id"]),
            risk=CapabilityRisk.MEDIUM,
            approval=True,
            emits=("workflow.run.retry_requested",),
        ),
        _capability(
            "workflow.trace",
            "Read workflow trace",
            "Read durable orchestration events correlating workflow definitions, runs, steps, attempts, Kernel runs, and approvals.",
            permission="workflows:read",
            input_schema=_object(
                {
                    "workflow_id": {"type": ["string", "null"]},
                    "run_id": {"type": ["string", "null"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                }
            ),
        ),
        _capability(
            "workflow.schedule.preview",
            "Preview workflow schedule",
            "Validate a schedule and show its next occurrences without enabling anything.",
            permission="workflows:read",
            input_schema=_object(
                {
                    "schedule": schedule_schema,
                    "count": {"type": "integer", "minimum": 1, "maximum": 20},
                },
                required=["schedule"],
            ),
        ),
        _capability(
            "workflow.runtime.status",
            "Workflow runtime status",
            "Read scheduler health, polling cadence, worker capacity, and last dispatcher error.",
            permission="workflows:read",
        ),
    )


def _loads(raw: str | None, fallback: Any) -> Any:
    try:
        return json.loads(raw or "")
    except Exception:
        return fallback


def _dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True, default=str)


def _snapshot(
    *,
    name: str,
    description: str,
    spec: dict[str, Any],
    schedule: dict[str, Any] | None,
    status: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "spec": spec,
        "schedule": schedule,
        "status": status,
    }


def _workflow_json(
    row: WorkflowDefinition,
    schedule: WorkflowSchedule | None = None,
) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "status": row.status,
        "owner_user_id": row.owner_user_id,
        "current_version": row.current_version,
        "schedule": _loads(schedule.schedule_json, {}) if schedule else None,
        "schedule_enabled": bool(schedule.enabled) if schedule else False,
        "next_run_at": (
            schedule.next_run_at.isoformat()
            if schedule and schedule.next_run_at
            else None
        ),
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def _version_json(row: WorkflowVersion, *, include_definition: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": row.id,
        "workflow_id": row.workflow_id,
        "version": row.version,
        "created_by_user_id": row.created_by_user_id,
        "created_at": row.created_at.isoformat(),
    }
    snapshot = _loads(row.snapshot_json, {})
    if include_definition:
        payload["spec"] = _loads(row.spec_json, {})
        payload["snapshot"] = snapshot
    else:
        payload["name"] = snapshot.get("name")
        payload["status"] = snapshot.get("status")
        payload["schedule"] = snapshot.get("schedule")
    return payload


def _run_json(row: WorkflowRun) -> dict[str, Any]:
    return {
        "id": row.id,
        "workflow_id": row.workflow_id,
        "workflow_version_id": row.workflow_version_id,
        "authority_user_id": row.authority_user_id,
        "initiated_by_user_id": row.initiated_by_user_id,
        "status": row.status,
        "trigger_type": row.trigger_type,
        "trigger": _loads(row.trigger_payload_json, {}),
        "current_step_key": row.current_step_key,
        "error_code": row.error_code,
        "error_message": row.error_message,
        "scheduled_for": row.scheduled_for.isoformat() if row.scheduled_for else None,
        "created_at": row.created_at.isoformat(),
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }


def _attempt_json(row: WorkflowStepAttempt) -> dict[str, Any]:
    return {
        "id": row.id,
        "attempt": row.attempt,
        "capability_id": row.capability_id,
        "status": row.status,
        "request_id": row.request_id,
        "kernel_run_id": row.kernel_run_id,
        "approval_id": row.approval_id,
        "arguments": _loads(row.arguments_json, {}),
        "result": _loads(row.result_json, {}),
        "error_code": row.error_code,
        "error_message": row.error_message,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "created_at": row.created_at.isoformat(),
    }


def _step_json(
    row: WorkflowStepRun,
    attempts: list[WorkflowStepAttempt] | None = None,
) -> dict[str, Any]:
    return {
        "id": row.id,
        "step_key": row.step_key,
        "step_order": row.step_order,
        "kind": row.step_kind,
        "capability_id": row.capability_id,
        "status": row.status,
        "attempt": row.attempt,
        "request_id": row.request_id,
        "kernel_run_id": row.kernel_run_id,
        "approval_id": row.approval_id,
        "arguments": _loads(row.arguments_json, {}),
        "result": _loads(row.result_json, {}),
        "error_code": row.error_code,
        "error_message": row.error_message,
        "wait_until": row.wait_until.isoformat() if row.wait_until else None,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "attempts": [_attempt_json(item) for item in attempts or []],
    }


class WorkflowProvider:
    def __init__(self) -> None:
        self._handlers = {
            "workflow.list": self._list,
            "workflow.get": self._get,
            "workflow.version.list": self._list_versions,
            "workflow.version.get": self._get_version,
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
            "workflow.runtime.status": self._runtime_status,
        }

    async def execute(
        self,
        db: AsyncSession,
        *,
        context: ExecutionContext,
        capability: CapabilitySpec,
        arguments: dict[str, Any],
        minimum_context: dict[str, Any],
    ) -> CapabilityExecutionResult:
        del minimum_context
        if not context.workspace_id:
            raise PermissionError("Workflow requires Workspace authority")
        handler = self._handlers.get(capability.id)
        if handler is None:
            raise LookupError("Workflow capability is not implemented")
        return await handler(db, context, arguments)

    async def _workflow(
        self,
        db: AsyncSession,
        context: ExecutionContext,
        workflow_id: str,
    ) -> WorkflowDefinition:
        row = await db.scalar(
            select(WorkflowDefinition).where(
                WorkflowDefinition.id == workflow_id,
                WorkflowDefinition.workspace_id == context.workspace_id,
            )
        )
        if row is None:
            raise LookupError("Workflow is unavailable")
        return row

    async def _run(
        self,
        db: AsyncSession,
        context: ExecutionContext,
        run_id: str,
    ) -> WorkflowRun:
        row = await db.scalar(
            select(WorkflowRun).where(
                WorkflowRun.id == run_id,
                WorkflowRun.workspace_id == context.workspace_id,
            )
        )
        if row is None:
            raise LookupError("Workflow run is unavailable")
        return row

    async def _list(
        self,
        db: AsyncSession,
        context: ExecutionContext,
        arguments: dict[str, Any],
    ) -> CapabilityExecutionResult:
        statement = select(WorkflowDefinition).where(
            WorkflowDefinition.workspace_id == context.workspace_id
        )
        if not bool(arguments.get("include_archived")):
            statement = statement.where(WorkflowDefinition.status != "archived")
        rows = (
            await db.scalars(
                statement.order_by(WorkflowDefinition.updated_at.desc()).limit(
                    max(1, min(int(arguments.get("limit") or 100), 200))
                )
            )
        ).all()
        schedules = (
            {
                row.workflow_id: row
                for row in (
                    await db.scalars(
                        select(WorkflowSchedule).where(
                            WorkflowSchedule.workflow_id.in_([item.id for item in rows])
                        )
                    )
                ).all()
            }
            if rows
            else {}
        )
        return CapabilityExecutionResult(
            value={"workflows": [_workflow_json(row, schedules.get(row.id)) for row in rows]}
        )

    async def _get(
        self,
        db: AsyncSession,
        context: ExecutionContext,
        arguments: dict[str, Any],
    ) -> CapabilityExecutionResult:
        row = await self._workflow(db, context, str(arguments["workflow_id"]))
        version = await db.scalar(
            select(WorkflowVersion).where(
                WorkflowVersion.workflow_id == row.id,
                WorkflowVersion.version == row.current_version,
            )
        )
        schedule = await db.scalar(
            select(WorkflowSchedule).where(WorkflowSchedule.workflow_id == row.id)
        )
        runs = (
            await db.scalars(
                select(WorkflowRun)
                .where(WorkflowRun.workflow_id == row.id)
                .order_by(WorkflowRun.created_at.desc())
                .limit(20)
            )
        ).all()
        return CapabilityExecutionResult(
            value={
                "workflow": _workflow_json(row, schedule),
                "version": _version_json(version, include_definition=True) if version else None,
                "recent_runs": [_run_json(item) for item in runs],
            },
            resource_type="workflow",
            resource_id=row.id,
        )

    async def _list_versions(
        self,
        db: AsyncSession,
        context: ExecutionContext,
        arguments: dict[str, Any],
    ) -> CapabilityExecutionResult:
        workflow = await self._workflow(db, context, str(arguments["workflow_id"]))
        rows = (
            await db.scalars(
                select(WorkflowVersion)
                .where(WorkflowVersion.workflow_id == workflow.id)
                .order_by(WorkflowVersion.version.desc())
                .limit(max(1, min(int(arguments.get("limit") or 100), 200)))
            )
        ).all()
        return CapabilityExecutionResult(
            value={"versions": [_version_json(row) for row in rows]},
            resource_type="workflow",
            resource_id=workflow.id,
        )

    async def _get_version(
        self,
        db: AsyncSession,
        context: ExecutionContext,
        arguments: dict[str, Any],
    ) -> CapabilityExecutionResult:
        workflow = await self._workflow(db, context, str(arguments["workflow_id"]))
        row = await db.scalar(
            select(WorkflowVersion).where(
                WorkflowVersion.workflow_id == workflow.id,
                WorkflowVersion.version == int(arguments["version"]),
            )
        )
        if row is None:
            raise LookupError("Workflow version is unavailable")
        return CapabilityExecutionResult(
            value={"version": _version_json(row, include_definition=True)},
            resource_type="workflow_version",
            resource_id=row.id,
        )

    def _future_schedule_time(
        self,
        schedule_spec: dict[str, Any] | None,
        *,
        enabled: bool,
    ) -> datetime | None:
        if not schedule_spec or not enabled:
            return None
        next_run = next_schedule_time(
            schedule_spec, after=datetime.utcnow() - timedelta(seconds=1)
        )
        if next_run is None:
            raise ValueError("Enabled schedule has no future occurrence")
        return next_run

    async def _create(
        self,
        db: AsyncSession,
        context: ExecutionContext,
        arguments: dict[str, Any],
    ) -> CapabilityExecutionResult:
        if not context.user_id:
            raise PermissionError("Workflow owner must be an Operly user")
        spec = validate_workflow_spec(arguments["spec"])
        schedule_spec = validate_schedule(arguments.get("schedule"))
        enabled = bool(arguments.get("enabled"))
        next_run = self._future_schedule_time(schedule_spec, enabled=enabled)
        row = WorkflowDefinition(
            workspace_id=context.workspace_id,
            owner_user_id=context.user_id,
            name=str(arguments["name"]).strip(),
            description=str(arguments.get("description") or ""),
            status="enabled" if enabled else "disabled",
            current_version=1,
        )
        db.add(row)
        await db.flush()
        version = WorkflowVersion(
            workflow_id=row.id,
            version=1,
            spec_json=_dumps(spec),
            snapshot_json=_dumps(
                _snapshot(
                    name=row.name,
                    description=row.description,
                    spec=spec,
                    schedule=schedule_spec,
                    status=row.status,
                )
            ),
            created_by_user_id=context.user_id,
        )
        db.add(version)
        schedule = None
        if schedule_spec:
            schedule = WorkflowSchedule(
                workflow_id=row.id,
                schedule_type=schedule_spec["type"],
                schedule_json=_dumps(schedule_spec),
                timezone=schedule_spec.get("timezone", "UTC"),
                enabled=enabled,
                next_run_at=next_run,
            )
            db.add(schedule)
        await db.flush()
        await record_workflow_event(
            db,
            workspace_id=context.workspace_id,
            workflow_id=row.id,
            event_type="workflow.created",
            actor_type="human",
            actor_id=context.user_id,
            owner_user_id=context.user_id,
            principal_id=context.principal_id,
            payload={
                "version": 1,
                "version_id": version.id,
                "enabled": enabled,
                "schedule_type": schedule_spec.get("type") if schedule_spec else "manual",
            },
        )
        return CapabilityExecutionResult(
            value={
                "workflow": _workflow_json(row, schedule),
                "version": _version_json(version, include_definition=True),
            },
            resource_type="workflow",
            resource_id=row.id,
            event_payload={"workflow_id": row.id, "workflow_version_id": version.id},
        )

    async def _update(
        self,
        db: AsyncSession,
        context: ExecutionContext,
        arguments: dict[str, Any],
    ) -> CapabilityExecutionResult:
        row = await self._workflow(db, context, str(arguments["workflow_id"]))
        if row.status == "archived":
            raise ValueError("Archived workflow cannot be edited")
        current_version = await db.scalar(
            select(WorkflowVersion).where(
                WorkflowVersion.workflow_id == row.id,
                WorkflowVersion.version == row.current_version,
            )
        )
        if current_version is None:
            raise RuntimeError("Current workflow version is unavailable")
        current_spec = _loads(current_version.spec_json, {})
        schedule = await db.scalar(
            select(WorkflowSchedule).where(WorkflowSchedule.workflow_id == row.id)
        )
        current_schedule = _loads(schedule.schedule_json, {}) if schedule else None

        new_name = row.name
        new_description = row.description
        new_spec = current_spec
        new_schedule = current_schedule
        changed: list[str] = []

        if arguments.get("name") is not None:
            candidate = str(arguments["name"]).strip()
            if candidate != row.name:
                new_name = candidate
                changed.append("name")
        if arguments.get("description") is not None:
            candidate = str(arguments["description"])
            if candidate != row.description:
                new_description = candidate
                changed.append("description")
        if arguments.get("spec") is not None:
            candidate = validate_workflow_spec(arguments["spec"])
            if candidate != current_spec:
                new_spec = candidate
                changed.append("spec")
        if "schedule" in arguments:
            candidate = validate_schedule(arguments.get("schedule"))
            if candidate != current_schedule:
                new_schedule = candidate
                changed.append("schedule")

        if not changed:
            return CapabilityExecutionResult(
                value={
                    "workflow": _workflow_json(row, schedule),
                    "version": _version_json(current_version, include_definition=True),
                    "changed": [],
                },
                resource_type="workflow",
                resource_id=row.id,
            )

        next_run = self._future_schedule_time(
            new_schedule,
            enabled=row.status == "enabled",
        )
        row.name = new_name
        row.description = new_description

        if "schedule" in changed:
            if new_schedule is None:
                if schedule is not None:
                    await db.delete(schedule)
                    schedule = None
            else:
                if schedule is None:
                    schedule = WorkflowSchedule(
                        workflow_id=row.id,
                        schedule_type=new_schedule["type"],
                        schedule_json=_dumps(new_schedule),
                        timezone=new_schedule.get("timezone", "UTC"),
                    )
                    db.add(schedule)
                schedule.schedule_type = new_schedule["type"]
                schedule.schedule_json = _dumps(new_schedule)
                schedule.timezone = new_schedule.get("timezone", "UTC")
                schedule.enabled = row.status == "enabled"
                schedule.next_run_at = next_run

        row.current_version += 1
        version = WorkflowVersion(
            workflow_id=row.id,
            version=row.current_version,
            spec_json=_dumps(new_spec),
            snapshot_json=_dumps(
                _snapshot(
                    name=row.name,
                    description=row.description,
                    spec=new_spec,
                    schedule=new_schedule,
                    status=row.status,
                )
            ),
            created_by_user_id=context.user_id,
        )
        db.add(version)
        await db.flush()
        await record_workflow_event(
            db,
            workspace_id=context.workspace_id,
            workflow_id=row.id,
            event_type="workflow.updated",
            actor_type="human",
            actor_id=context.user_id,
            owner_user_id=row.owner_user_id,
            principal_id=context.principal_id,
            payload={
                "version": row.current_version,
                "version_id": version.id,
                "previous_version_id": current_version.id,
                "changed": changed,
            },
        )
        return CapabilityExecutionResult(
            value={
                "workflow": _workflow_json(row, schedule),
                "version": _version_json(version, include_definition=True),
                "changed": changed,
            },
            resource_type="workflow",
            resource_id=row.id,
            event_payload={"workflow_id": row.id, "workflow_version_id": version.id},
        )

    async def _enable(
        self,
        db: AsyncSession,
        context: ExecutionContext,
        arguments: dict[str, Any],
    ) -> CapabilityExecutionResult:
        row = await self._workflow(db, context, str(arguments["workflow_id"]))
        if row.status == "archived":
            raise ValueError("Archived workflow cannot be enabled")
        schedule = await db.scalar(
            select(WorkflowSchedule).where(WorkflowSchedule.workflow_id == row.id)
        )
        next_run = None
        if schedule:
            next_run = self._future_schedule_time(
                _loads(schedule.schedule_json, {}), enabled=True
            )
        row.status = "enabled"
        if schedule:
            schedule.enabled = True
            schedule.next_run_at = next_run
        await record_workflow_event(
            db,
            workspace_id=context.workspace_id,
            workflow_id=row.id,
            event_type="workflow.enabled",
            actor_type="human",
            actor_id=context.user_id,
            owner_user_id=row.owner_user_id,
            principal_id=context.principal_id,
            payload={"next_run_at": next_run.isoformat() if next_run else None},
        )
        return CapabilityExecutionResult(
            value={"workflow": _workflow_json(row, schedule)},
            resource_type="workflow",
            resource_id=row.id,
        )

    async def _disable(
        self,
        db: AsyncSession,
        context: ExecutionContext,
        arguments: dict[str, Any],
    ) -> CapabilityExecutionResult:
        row = await self._workflow(db, context, str(arguments["workflow_id"]))
        if row.status != "archived":
            row.status = "disabled"
        schedule = await db.scalar(
            select(WorkflowSchedule).where(WorkflowSchedule.workflow_id == row.id)
        )
        if schedule:
            schedule.enabled = False
            schedule.next_run_at = None
        await record_workflow_event(
            db,
            workspace_id=context.workspace_id,
            workflow_id=row.id,
            event_type="workflow.disabled",
            actor_type="human",
            actor_id=context.user_id,
            owner_user_id=row.owner_user_id,
            principal_id=context.principal_id,
        )
        return CapabilityExecutionResult(
            value={"workflow": _workflow_json(row, schedule)},
            resource_type="workflow",
            resource_id=row.id,
        )

    async def _archive(
        self,
        db: AsyncSession,
        context: ExecutionContext,
        arguments: dict[str, Any],
    ) -> CapabilityExecutionResult:
        row = await self._workflow(db, context, str(arguments["workflow_id"]))
        row.status = "archived"
        schedule = await db.scalar(
            select(WorkflowSchedule).where(WorkflowSchedule.workflow_id == row.id)
        )
        if schedule:
            schedule.enabled = False
            schedule.next_run_at = None
        await record_workflow_event(
            db,
            workspace_id=context.workspace_id,
            workflow_id=row.id,
            event_type="workflow.archived",
            actor_type="human",
            actor_id=context.user_id,
            owner_user_id=row.owner_user_id,
            principal_id=context.principal_id,
        )
        return CapabilityExecutionResult(
            value={"workflow": _workflow_json(row, schedule)},
            resource_type="workflow",
            resource_id=row.id,
        )

    async def _start_run(
        self,
        db: AsyncSession,
        context: ExecutionContext,
        arguments: dict[str, Any],
    ) -> CapabilityExecutionResult:
        row = await self._workflow(db, context, str(arguments["workflow_id"]))
        if row.status == "archived":
            raise ValueError("Archived workflow cannot run")
        run = await queue_workflow_run(
            db,
            workflow=row,
            trigger_type="manual",
            trigger_payload=(
                arguments.get("trigger")
                if isinstance(arguments.get("trigger"), dict)
                else {}
            ),
            initiated_by_user_id=context.user_id,
        )
        return CapabilityExecutionResult(
            value={"run": _run_json(run)},
            resource_type="workflow_run",
            resource_id=run.id,
            event_payload={"workflow_id": row.id, "workflow_run_id": run.id},
        )

    async def _list_runs(
        self,
        db: AsyncSession,
        context: ExecutionContext,
        arguments: dict[str, Any],
    ) -> CapabilityExecutionResult:
        statement = select(WorkflowRun).where(
            WorkflowRun.workspace_id == context.workspace_id
        )
        if arguments.get("workflow_id"):
            statement = statement.where(
                WorkflowRun.workflow_id == str(arguments["workflow_id"])
            )
        if arguments.get("status"):
            statement = statement.where(WorkflowRun.status == str(arguments["status"]))
        rows = (
            await db.scalars(
                statement.order_by(WorkflowRun.created_at.desc()).limit(
                    max(1, min(int(arguments.get("limit") or 100), 200))
                )
            )
        ).all()
        return CapabilityExecutionResult(value={"runs": [_run_json(row) for row in rows]})

    async def _get_run(
        self,
        db: AsyncSession,
        context: ExecutionContext,
        arguments: dict[str, Any],
    ) -> CapabilityExecutionResult:
        run = await self._run(db, context, str(arguments["run_id"]))
        version = await db.get(WorkflowVersion, run.workflow_version_id)
        steps = (
            await db.scalars(
                select(WorkflowStepRun)
                .where(WorkflowStepRun.workflow_run_id == run.id)
                .order_by(WorkflowStepRun.step_order)
            )
        ).all()
        attempts = (
            await db.scalars(
                select(WorkflowStepAttempt)
                .where(WorkflowStepAttempt.workflow_run_id == run.id)
                .order_by(WorkflowStepAttempt.step_run_id, WorkflowStepAttempt.attempt)
            )
        ).all()
        attempts_by_step: dict[str, list[WorkflowStepAttempt]] = {}
        for attempt in attempts:
            attempts_by_step.setdefault(attempt.step_run_id, []).append(attempt)
        return CapabilityExecutionResult(
            value={
                "run": _run_json(run),
                "version": _version_json(version, include_definition=True) if version else None,
                "steps": [
                    _step_json(row, attempts_by_step.get(row.id, [])) for row in steps
                ],
                "result": _loads(run.result_json, {}),
            },
            resource_type="workflow_run",
            resource_id=run.id,
        )

    async def _cancel_run(
        self,
        db: AsyncSession,
        context: ExecutionContext,
        arguments: dict[str, Any],
    ) -> CapabilityExecutionResult:
        run = await self._run(db, context, str(arguments["run_id"]))
        terminal = {"completed", "completed_with_errors", "failed", "cancelled", "orphaned"}
        if run.status not in terminal:
            previous_status = run.status
            run.status = "cancelled"
            run.finished_at = datetime.utcnow()
            run.lease_token = None
            run.lease_until = None
            step = None
            attempt = None
            if run.current_step_key:
                step = await db.scalar(
                    select(WorkflowStepRun).where(
                        WorkflowStepRun.workflow_run_id == run.id,
                        WorkflowStepRun.step_key == run.current_step_key,
                    )
                )
                if step is not None and previous_status in {"waiting", "waiting_approval"}:
                    step.status = "cancelled"
                    step.finished_at = run.finished_at
                    if step.step_kind == "action" and step.attempt > 0:
                        attempt = await db.scalar(
                            select(WorkflowStepAttempt).where(
                                WorkflowStepAttempt.step_run_id == step.id,
                                WorkflowStepAttempt.attempt == step.attempt,
                            )
                        )
                        if attempt is not None:
                            attempt.status = "cancelled"
                            attempt.finished_at = run.finished_at
            await record_workflow_event(
                db,
                workspace_id=context.workspace_id,
                workflow_id=run.workflow_id,
                workflow_run_id=run.id,
                step_run_id=step.id if step else None,
                step_attempt_id=attempt.id if attempt else None,
                event_type="workflow.run.cancelled",
                actor_type="human",
                actor_id=context.user_id,
                owner_user_id=run.authority_user_id,
                principal_id=context.principal_id,
                capability_id=step.capability_id if step else None,
                kernel_run_id=step.kernel_run_id if step else None,
                approval_id=step.approval_id if step else None,
                payload={
                    "previous_status": previous_status,
                    "current_step_key": run.current_step_key,
                },
            )
        return CapabilityExecutionResult(
            value={"run": _run_json(run)},
            resource_type="workflow_run",
            resource_id=run.id,
        )

    async def _retry_run(
        self,
        db: AsyncSession,
        context: ExecutionContext,
        arguments: dict[str, Any],
    ) -> CapabilityExecutionResult:
        run = await self._run(db, context, str(arguments["run_id"]))
        if run.status != "failed":
            raise ValueError(
                "Only failed workflow runs can be retried; orphaned runs require manual reconciliation"
            )
        failed = await db.scalar(
            select(WorkflowStepRun)
            .where(
                WorkflowStepRun.workflow_run_id == run.id,
                WorkflowStepRun.status == "failed",
            )
            .order_by(WorkflowStepRun.step_order.desc())
        )
        if failed:
            failed.status = "pending"
            failed.request_id = None
            failed.kernel_run_id = None
            failed.approval_id = None
            failed.arguments_json = "{}"
            failed.result_json = "{}"
            failed.error_code = None
            failed.error_message = None
            failed.finished_at = None
        run.status = "queued"
        run.error_code = None
        run.error_message = None
        run.finished_at = None
        run.lease_token = None
        run.lease_until = None
        await record_workflow_event(
            db,
            workspace_id=context.workspace_id,
            workflow_id=run.workflow_id,
            workflow_run_id=run.id,
            step_run_id=failed.id if failed else None,
            event_type="workflow.run.retry_requested",
            actor_type="human",
            actor_id=context.user_id,
            owner_user_id=run.authority_user_id,
            principal_id=context.principal_id,
            payload={
                "step_key": failed.step_key if failed else None,
                "next_attempt": (failed.attempt + 1) if failed else None,
            },
        )
        return CapabilityExecutionResult(
            value={"run": _run_json(run)},
            resource_type="workflow_run",
            resource_id=run.id,
        )

    async def _trace(
        self,
        db: AsyncSession,
        context: ExecutionContext,
        arguments: dict[str, Any],
    ) -> CapabilityExecutionResult:
        statement = select(WorkflowTraceEvent).where(
            WorkflowTraceEvent.workspace_id == context.workspace_id
        )
        if arguments.get("workflow_id"):
            statement = statement.where(
                WorkflowTraceEvent.workflow_id == str(arguments["workflow_id"])
            )
        if arguments.get("run_id"):
            statement = statement.where(
                WorkflowTraceEvent.workflow_run_id == str(arguments["run_id"])
            )
        rows = (
            await db.scalars(
                statement.order_by(WorkflowTraceEvent.created_at.desc()).limit(
                    max(1, min(int(arguments.get("limit") or 200), 500))
                )
            )
        ).all()
        return CapabilityExecutionResult(
            value={
                "events": [
                    {
                        "id": row.id,
                        "event_type": row.event_type,
                        "workflow_id": row.workflow_id,
                        "workflow_run_id": row.workflow_run_id,
                        "step_run_id": row.step_run_id,
                        "step_attempt_id": row.step_attempt_id,
                        "capability_id": row.capability_id,
                        "kernel_run_id": row.kernel_run_id,
                        "approval_id": row.approval_id,
                        "actor_type": row.actor_type,
                        "actor_id": row.actor_id,
                        "payload": _loads(row.payload_json, {}),
                        "created_at": row.created_at.isoformat(),
                    }
                    for row in rows
                ]
            }
        )

    async def _schedule_preview(
        self,
        db: AsyncSession,
        context: ExecutionContext,
        arguments: dict[str, Any],
    ) -> CapabilityExecutionResult:
        del db, context
        schedule = validate_schedule(arguments["schedule"])
        if schedule is None:
            return CapabilityExecutionResult(value={"schedule": None, "occurrences": []})
        count = max(1, min(int(arguments.get("count") or 5), 20))
        cursor = datetime.utcnow() - timedelta(seconds=1)
        occurrences: list[str] = []
        for _ in range(count):
            next_at = next_schedule_time(schedule, after=cursor)
            if next_at is None:
                break
            occurrences.append(next_at.isoformat())
            cursor = next_at
        return CapabilityExecutionResult(
            value={"schedule": schedule, "occurrences": occurrences}
        )

    async def _runtime_status(
        self,
        db: AsyncSession,
        context: ExecutionContext,
        arguments: dict[str, Any],
    ) -> CapabilityExecutionResult:
        del db, context, arguments
        from packages.workflow.scheduler import workflow_scheduler

        return CapabilityExecutionResult(value=workflow_scheduler.status())
