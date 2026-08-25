from __future__ import annotations

from packages.business_brain.personal_agent import get_personal_agent_service
from packages.business_brain.personal_capability_runtime import invoke_personal_capability
from packages.capabilities.agent_harness import PluginInvocationContext
from packages.tasks.workflow import WorkflowExecutor


class PersonalWorkflowExecutor(WorkflowExecutor):
    """Run declarative personal workflows through Personal AI's governed capability set.

    Scheduled execution must use the same ActionService/CapabilityFirewall boundary as
    interactive Personal AI. Provider existence is not authority; account connector
    state, OAuth scopes, surface policy and result verification are resolved again on
    every invocation.
    """

    async def _invoke_workspace(
        self,
        capability: str,
        args: dict,
        context: PluginInvocationContext,
        *,
        call_id: str,
    ) -> dict:
        metadata = context.metadata if isinstance(context.metadata, dict) else {}
        return await invoke_personal_capability(
            get_personal_agent_service(),
            user_id=str(context.user_id or ""),
            capability_id=capability,
            arguments=dict(args),
            objective=str(context.objective or "Personal scheduled workflow"),
            call_id=call_id,
            channel=str(context.channel or "personal_workflow"),
            conversation_id=str(metadata.get("_conversation_id") or "") or None,
            metadata={
                **metadata,
                "scheduled_workflow": True,
            },
            focus_workspace_id=str(metadata.get("focus_workspace_id") or "") or None,
        )
