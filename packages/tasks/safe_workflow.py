from __future__ import annotations

from packages.tasks.workflow import WorkflowExecutor


class ApprovalAwareWorkflowExecutor(WorkflowExecutor):
    """Workspace executor that never treats WAITING_APPROVAL as completed work."""

    async def _invoke_workspace(self, capability, args, context, *, call_id):
        result = await super()._invoke_workspace(
            capability,
            args,
            context,
            call_id=call_id,
        )
        if str(result.get("status") or "").upper() == "WAITING_APPROVAL":
            blocked = dict(result)
            blocked["ok"] = False
            blocked["error"] = (
                "workflow_waiting_approval:"
                + str(result.get("approval_id") or result.get("action_id") or "pending")
            )
            return blocked
        return result
