from __future__ import annotations

from types import SimpleNamespace

from packages.business_brain.personal_agent import get_personal_agent_service
from packages.capabilities.agent_harness import PluginInvocationContext
from packages.database.db import session_scope
from packages.tasks.workflow import WorkflowExecutor


class PersonalWorkflowExecutor(WorkflowExecutor):
    """Run declarative personal workflows through Personal AI's provider set.

    This deliberately reuses PersonalAgentService's live provider registry instead of
    maintaining a second personal-tool list. Adding a provider to Personal AI therefore
    makes it available to personal workflows without changing this executor.
    """

    async def _invoke_workspace(
        self,
        capability: str,
        args: dict,
        context: PluginInvocationContext,
        *,
        call_id: str,
    ) -> dict:
        service = get_personal_agent_service()
        resolved = service._definitions.get(capability)
        if resolved is None:
            return {"ok": False, "error": "personal_workflow_capability_not_available"}
        provider, _definition = resolved
        async with session_scope() as db:
            provider_context = SimpleNamespace(
                tenant_id=None,
                actor_id=context.user_id,
                db=db,
                invocation={
                    "channel": context.channel,
                    "temporal_context": context.metadata.get("temporal_context"),
                    "metadata": {
                        **context.metadata,
                        "personal_scope": True,
                        "shared_surface": False,
                        "is_direct": True,
                        "call_id": call_id,
                    },
                },
            )
            result = await provider.execute(provider_context, capability, dict(args))
            verified = await provider.verify(provider_context, capability, dict(args), result)
            await db.commit()
            return {
                "ok": bool(verified.success),
                "status": "VERIFIED" if verified.success else "FAILED",
                "observation": verified.evidence,
                "changed": bool(verified.changed),
            }
