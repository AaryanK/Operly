"""Durable Workspace workflow orchestration over governed Operly capabilities.

Workflow never executes business/provider actions directly. It queues and traces
workflow runs, while action steps are delegated to the normal Workspace Kernel
runtime with freshly resolved Workspace authority.
"""

from dataclasses import replace

from packages.kernel.contracts import CapabilitySpec
from packages.workflow.access import WorkflowProvider
from packages.workflow.provider import PROVIDER_ID, workflow_capabilities as _workflow_capabilities
from packages.workflow.scheduler import workflow_scheduler


def workflow_capabilities() -> tuple[CapabilitySpec, ...]:
    """Expose Workflow through the same human/agent Workspace capability registry."""

    return tuple(
        replace(spec, tags=frozenset((*spec.tags, "operations")))
        for spec in _workflow_capabilities()
    )


__all__ = ["PROVIDER_ID", "WorkflowProvider", "workflow_capabilities", "workflow_scheduler"]
