"""Durable Workspace workflow orchestration over governed Operly capabilities.

Workflow never executes business/provider actions directly. It queues and traces
workflow runs, while action steps are delegated to the normal Workspace Kernel
runtime with freshly resolved Workspace authority.
"""

from dataclasses import replace
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from packages.kernel.contracts import CapabilityExecutionResult, CapabilitySpec
from packages.security.execution_context import ExecutionContext
from packages.workflow.models import WorkflowDefinition, WorkflowRun
from packages.workflow.provider import PROVIDER_ID, WorkflowProvider as _WorkflowProvider, workflow_capabilities as _workflow_capabilities
from packages.workflow.scheduler import workflow_scheduler


def workflow_capabilities() -> tuple[CapabilitySpec, ...]:
    # The universal React tool surface already understands the operations tag, so
    # Workflow gets a sensible human-facing home without a second execution API.
    return tuple(
        replace(spec, tags=frozenset((*spec.tags, "operations")))
        for spec in _workflow_capabilities()
    )


class WorkflowProvider(_WorkflowProvider):
    """Add the cross-workflow ownership guard around the domain provider.

    A custom role may be granted workflow permissions later, but that grant cannot be
    used to run/retry another principal's workflow and inherit its stronger authority.
    Workspace owners remain the administrative root for the Workspace.
    """

    async def execute(
        self,
        db: AsyncSession,
        *,
        context: ExecutionContext,
        capability: CapabilitySpec,
        arguments: dict[str, Any],
        minimum_context: dict[str, Any],
    ) -> CapabilityExecutionResult:
        if capability.id in {"workflow.run.start", "workflow.run.cancel", "workflow.run.retry"} and context.role != "owner":
            if not context.user_id:
                raise PermissionError("Workflow run authority requires an Operly user")
            target_owner: str | None = None
            if capability.id == "workflow.run.start":
                target = await db.get(WorkflowDefinition, str(arguments.get("workflow_id") or ""))
                if target is not None and target.workspace_id == context.workspace_id:
                    target_owner = target.owner_user_id
            else:
                target = await db.get(WorkflowRun, str(arguments.get("run_id") or ""))
                if target is not None and target.workspace_id == context.workspace_id:
                    target_owner = target.owner_user_id
            if target_owner != context.user_id:
                raise PermissionError("A non-owner may only control their own workflow runs")
        return await super().execute(
            db,
            context=context,
            capability=capability,
            arguments=arguments,
            minimum_context=minimum_context,
        )


__all__ = ["PROVIDER_ID", "WorkflowProvider", "workflow_capabilities", "workflow_scheduler"]
