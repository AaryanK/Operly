"""Durable Workspace workflow orchestration over governed Operly capabilities.

Workflow never executes business/provider actions directly. It queues and traces
workflow runs, while action steps are delegated to the normal Workspace Kernel
runtime with freshly resolved Workspace authority.
"""

from dataclasses import replace
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
)
from packages.workflow.provider import (
    PROVIDER_ID,
    WorkflowProvider as _WorkflowProvider,
    _loads,
    _run_json,
    _workflow_json,
    workflow_capabilities as _workflow_capabilities,
)
from packages.workflow.scheduler import workflow_scheduler


def workflow_capabilities() -> tuple[CapabilitySpec, ...]:
    # The universal React tool surface already understands the operations tag, so
    # Workflow gets a sensible human-facing home without a second execution API.
    return tuple(
        replace(spec, tags=frozenset((*spec.tags, "operations")))
        for spec in _workflow_capabilities()
    )


class WorkflowProvider(_WorkflowProvider):
    """Scope delegated Workflow authority to the principal's own definitions/runs.

    Workspace owners are the administrative root. A future agent/custom role may be
    explicitly granted workflow permissions, but that grant cannot be used to mutate,
    execute, or inspect another principal's workflow and thereby borrow stronger
    Workspace/connector authority or private trace data.
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
                WorkflowRun.owner_user_id == context.user_id,
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
            await db.scalars(statement.order_by(WorkflowDefinition.updated_at.desc()).limit(limit))
        ).all()
        schedules = {
            row.workflow_id: row
            for row in (
                await db.scalars(
                    select(WorkflowSchedule).where(
                        WorkflowSchedule.workflow_id.in_([item.id for item in rows])
                    )
                )
            ).all()
        } if rows else {}
        return CapabilityExecutionResult(
            value={"workflows": [_workflow_json(row, schedules.get(row.id)) for row in rows]}
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
            WorkflowRun.owner_user_id == context.user_id,
        )
        if arguments.get("workflow_id"):
            statement = statement.where(WorkflowRun.workflow_id == str(arguments["workflow_id"]))
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
        statement = select(WorkflowTraceEvent).where(
            WorkflowTraceEvent.workspace_id == context.workspace_id,
            WorkflowTraceEvent.workflow_id.in_(owned_ids),
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
            await db.scalars(statement.order_by(WorkflowTraceEvent.created_at.desc()).limit(limit))
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
        if capability.id == "workflow.run.list":
            return await self._delegated_run_list(db, context, arguments)
        if capability.id == "workflow.trace":
            return await self._delegated_trace(db, context, arguments)

        workflow_scoped = {
            "workflow.get",
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
            await self._owned_workflow(db, context, str(arguments.get("workflow_id") or ""))
        elif capability.id in run_scoped:
            await self._owned_run(db, context, str(arguments.get("run_id") or ""))

        return await super().execute(
            db,
            context=context,
            capability=capability,
            arguments=arguments,
            minimum_context=minimum_context,
        )


__all__ = ["PROVIDER_ID", "WorkflowProvider", "workflow_capabilities", "workflow_scheduler"]
