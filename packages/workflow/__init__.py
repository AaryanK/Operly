"""Durable Personal/Workspace workflow orchestration over governed Operly capabilities.

Workflow never executes provider actions directly. Runs are durable, while each action
step is delegated to the normal scope-native Kernel runtime with freshly resolved
Personal or Workspace authority.
"""

from dataclasses import replace

from packages.kernel.contracts import CapabilitySpec
from packages.workflow.concurrency import (
    WorkflowProvider,
    extend_workflow_capabilities,
    install_concurrency_scheduler,
)
from packages.workflow.provider import PROVIDER_ID, workflow_capabilities as _workflow_capabilities
from packages.workflow.triggers import workflow_event_dispatcher


workflow_scheduler = install_concurrency_scheduler()


def workflow_capabilities() -> tuple[CapabilitySpec, ...]:
    """Workspace-scoped Workflow contracts."""

    return tuple(
        replace(spec, tags=frozenset((*spec.tags, "operations")))
        for spec in extend_workflow_capabilities(_workflow_capabilities())
    )


def personal_workflow_capabilities() -> tuple[CapabilitySpec, ...]:
    """Account-owned Workflow contracts with identical semantics but Personal scope."""

    return tuple(
        replace(
            spec,
            scopes=frozenset({"personal"}),
            resource_scope="personal",
            tags=frozenset(
                "personal" if tag == "workspace" else tag
                for tag in (*spec.tags, "operations")
            ),
        )
        for spec in extend_workflow_capabilities(_workflow_capabilities())
    )


__all__ = [
    "PROVIDER_ID",
    "WorkflowProvider",
    "personal_workflow_capabilities",
    "workflow_capabilities",
    "workflow_event_dispatcher",
    "workflow_scheduler",
]
