from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.kernel.contracts import CapabilityExecutionResult, CapabilitySpec
from packages.security.execution_context import ExecutionContext
from packages.workflow.models import (
    WorkflowDefinition,
    WorkflowRun,
    WorkflowSchedule,
    WorkflowTraceEvent,
    WorkflowVersion,
)
from packages.workflow.provider import (
    WorkflowProvider as BaseWorkflowProvider,
    _loads,
    _run_json,
    _version_json,
    _workflow_json,
)


def _validate_mutation_input(capability_id: str, arguments: dict[str, Any]) -> None:
    """Enforce normalized invariants JSON Schema cannot express by length alone."""

    if capability_id == "workflow.create":
        if not str(arguments.get("name") or "").strip():
            raise ValueError("Workflow name is required")
    elif capability_id == "workflow.update" and arguments.get("name") is not None:
        if not str(arguments["name"]).strip():
            raise ValueError("Workflow name cannot be empty")


class WorkflowProvider(BaseWorkflowProvider):
    """Scope delegated Workflow authority to the principal's own definitions/runs.

    Workspace owners are administrative root. A future agent/custom role may be
    explicitly granted workflow permissions, but that grant cannot inspect or execute
    another principal's workflow and thereby borrow stronger Workspace/connector
    authority or private attempt data.
    """

    async def _owned_workflow(
        self,
        db: AsyncSession,
        context: ExecutionContext,
        workflow_id: str,
    ) -> WorkflowDefinition:
        row = await db.scalar(
            select(WorkflowDefinition).where(
                WorkflowDefinition.id == workflow_id,
                WorkflowDefinition.workspace_id == context.workspace_id,
                WorkflowDefinition.owner_user_id == context.user_id,
            )
        )
        if row is None:
            raise PermissionError("A non-owner may only access their own workflows")
        return row

    async def _owned_run(
        self,
        db: AsyncSession,
        context: ExecutionContext,
        run_id: str,
    ) -> WorkflowRun:
        row = await db.scalar(
            select(WorkflowRun).where(
                WorkflowRun.id == run_id,
                WorkflowRun.workspace_id == context.workspace_id,
                WorkflowRun.authority_user_id == context.user_id,
            )
        )
        if row is None:
            raise PermissionError("A non-owner may only access their own workflow runs")
        return row

    async def _delegated_list(
        self,
        db: AsyncSession,
        context: ExecutionContext,
        arguments: dict[str, Any],
    ) -> CapabilityExecutionResult:
        limit = max(1, min(int(arguments.get("limit") or 100), 200))
        statement = select(WorkflowDefinition).where(
            WorkflowDefinition.workspace_id == context.workspace_id,
            WorkflowDefinition.owner_user_id == context.user_id,
        )
        if not bool(arguments.get("include_archived")):
            statement = statement.where(WorkflowDefinition.status != "archived")
        rows = (
            await db.scalars(
                statement.order_by(WorkflowDefinition.updated_at.desc()).limit(limit)
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

    async def _delegated_get(
        self,
        db: AsyncSession,
        context: ExecutionContext,
        arguments: dict[str, Any],
    ) -> CapabilityExecutionResult:
        workflow = await self._owned_workflow(
            db, context, str(arguments.get("workflow_id") or "")
        )
        version = await db.scalar(
            select(WorkflowVersion).where(
                WorkflowVersion.workflow_id == workflow.id,
                WorkflowVersion.version == workflow.current_version,
            )
        )
        schedule = await db.scalar(
            select(WorkflowSchedule).where(WorkflowSchedule.workflow_id == workflow.id)
        )
        runs = (
            await db.scalars(
                select(WorkflowRun)
                .where(
                    WorkflowRun.workflow_id == workflow.id,
                    WorkflowRun.authority_user_id == context.user_id,
                )
                .order_by(WorkflowRun.created_at.desc())
                .limit(20)
            )
        ).all()
        return CapabilityExecutionResult(
            value={
                "workflow": _workflow_json(workflow, schedule),
                "version": _version_json(version, include_definition=True) if version else None,
                "recent_runs": [_run_json(run) for run in runs],
            },
            resource_type="workflow",
            resource_id=workflow.id,
        )

    async def _delegated_run_list(
        self,
        db: AsyncSession,
        context: ExecutionContext,
        arguments: dict[str, Any],
    ) -> CapabilityExecutionResult:
        limit = max(1, min(int(arguments.get("limit") or 100), 200))
        statement = select(WorkflowRun).where(
            WorkflowRun.workspace_id == context.workspace_id,
            WorkflowRun.authority_user_id == context.user_id,
        )
        if arguments.get("workflow_id"):
            workflow = await self._owned_workflow(
                db, context, str(arguments["workflow_id"])
            )
            statement = statement.where(WorkflowRun.workflow_id == workflow.id)
        if arguments.get("status"):
            statement = statement.where(WorkflowRun.status == str(arguments["status"]))
        rows = (
            await db.scalars(statement.order_by(WorkflowRun.created_at.desc()).limit(limit))
        ).all()
        return CapabilityExecutionResult(value={"runs": [_run_json(row) for row in rows]})

    async def _delegated_trace(
        self,
        db: AsyncSession,
        context: ExecutionContext,
        arguments: dict[str, Any],
    ) -> CapabilityExecutionResult:
        limit = max(1, min(int(arguments.get("limit") or 200), 500))
        owned_ids = select(WorkflowDefinition.id).where(
            WorkflowDefinition.workspace_id == context.workspace_id,
            WorkflowDefinition.owner_user_id == context.user_id,
        )
        owned_run_ids = select(WorkflowRun.id).where(
            WorkflowRun.workspace_id == context.workspace_id,
            WorkflowRun.authority_user_id == context.user_id,
        )
        statement = select(WorkflowTraceEvent).where(
            WorkflowTraceEvent.workspace_id == context.workspace_id,
            WorkflowTraceEvent.workflow_id.in_(owned_ids),
        )
        statement = statement.where(
            (WorkflowTraceEvent.workflow_run_id.is_(None))
            | (WorkflowTraceEvent.workflow_run_id.in_(owned_run_ids))
        )
        if arguments.get("workflow_id"):
            workflow = await self._owned_workflow(
                db, context, str(arguments["workflow_id"])
            )
            statement = statement.where(WorkflowTraceEvent.workflow_id == workflow.id)
        if arguments.get("run_id"):
            run = await self._owned_run(db, context, str(arguments["run_id"]))
            statement = statement.where(WorkflowTraceEvent.workflow_run_id == run.id)
        rows = (
            await db.scalars(
                statement.order_by(WorkflowTraceEvent.created_at.desc()).limit(limit)
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

    async def execute(
        self,
        db: AsyncSession,
        *,
        context: ExecutionContext,
        capability: CapabilitySpec,
        arguments: dict[str, Any],
        minimum_context: dict[str, Any],
    ) -> CapabilityExecutionResult:
        _validate_mutation_input(capability.id, arguments)

        if context.role == "owner":
            return await super().execute(
                db,
                context=context,
                capability=capability,
                arguments=arguments,
                minimum_context=minimum_context,
            )
        if not context.user_id:
            raise PermissionError("Delegated Workflow authority requires an Operly user")

        if capability.id == "workflow.list":
            return await self._delegated_list(db, context, arguments)
        if capability.id == "workflow.get":
            return await self._delegated_get(db, context, arguments)
        if capability.id == "workflow.run.list":
            return await self._delegated_run_list(db, context, arguments)
        if capability.id == "workflow.trace":
            return await self._delegated_trace(db, context, arguments)

        workflow_scoped = {
            "workflow.version.list",
            "workflow.version.get",
            "workflow.update",
            "workflow.enable",
            "workflow.disable",
            "workflow.archive",
            "workflow.run.start",
        }
        run_scoped = {
            "workflow.run.get",
            "workflow.run.cancel",
            "workflow.run.retry",
        }
        if capability.id in workflow_scoped:
            await self._owned_workflow(
                db, context, str(arguments.get("workflow_id") or "")
            )
        elif capability.id in run_scoped:
            await self._owned_run(db, context, str(arguments.get("run_id") or ""))

        # workflow.create, schedule.preview and runtime.status do not target another
        # principal's existing resource; their normal capability permission remains
        # the complete authorization boundary for delegated roles.
        return await super().execute(
            db,
            context=context,
            capability=capability,
            arguments=arguments,
            minimum_context=minimum_context,
        )
