import json

from sqlalchemy import desc, select

from packages.actions.service import ActionService, ActionStatus
from packages.capabilities.contracts import ApprovalPolicy, CapabilityDefinition, CapabilityResult
from packages.capabilities.providers import BaseProvider
from packages.database.company_models import BusinessActionRecord
from packages.security.execution_context import resolve_execution_context


def _action_summary(row: BusinessActionRecord) -> dict:
    return {
        "action_id": row.id,
        "capability": row.capability,
        "objective": row.objective,
        "status": row.status,
        "approval_id": row.approval_id,
        "origin": row.origin,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


class ActionLifecycleProvider(BaseProvider):
    """Durable action state exposed to the model through governed capabilities."""

    name = "operly_actions"
    capabilities = (
        CapabilityDefinition(
            "actions.list",
            "actions_list",
            "List recent durable Operly actions in the current workspace. Use this instead of guessing what happened from chat history.",
            {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 25},
                },
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="read_only",
            permissions=("actions:read",),
            approval_policy=ApprovalPolicy.AUTO,
        ),
        CapabilityDefinition(
            "actions.pending",
            "actions_pending",
            "List actions currently waiting for approval in the current workspace. Use this to resolve phrases such as 'approve it' or 'what is pending?'.",
            {
                "type": "object",
                "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 25}},
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="read_only",
            permissions=("actions:read",),
            approval_policy=ApprovalPolicy.AUTO,
        ),
        CapabilityDefinition(
            "actions.get",
            "actions_get",
            "Inspect one durable Operly action by action ID, including its exact arguments and verified result.",
            {
                "type": "object",
                "properties": {"action_id": {"type": "string"}},
                "required": ["action_id"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="read_only",
            permissions=("actions:read",),
            approval_policy=ApprovalPolicy.AUTO,
        ),
        CapabilityDefinition(
            "actions.approve",
            "actions_approve",
            "Approve exactly one action that is currently WAITING_APPROVAL. The target action is executed at most once; terminal actions cannot be approved again.",
            {
                "type": "object",
                "properties": {"action_id": {"type": "string"}},
                "required": ["action_id"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="medium",
            permissions=("actions:approve",),
            approval_policy=ApprovalPolicy.AUTO,
        ),
        CapabilityDefinition(
            "actions.reject",
            "actions_reject",
            "Reject exactly one action that is currently WAITING_APPROVAL.",
            {
                "type": "object",
                "properties": {"action_id": {"type": "string"}},
                "required": ["action_id"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="medium",
            permissions=("actions:approve",),
            approval_policy=ApprovalPolicy.AUTO,
        ),
    )

    async def execute(self, context, capability_name, arguments):
        if capability_name in {"actions.list", "actions.pending"}:
            limit = max(1, min(int(arguments.get("limit", 10)), 25))
            statement = select(BusinessActionRecord).where(
                BusinessActionRecord.tenant_id == context.tenant_id
            )
            status = "WAITING_APPROVAL" if capability_name == "actions.pending" else str(arguments.get("status") or "").strip()
            if status:
                statement = statement.where(BusinessActionRecord.status == status.upper())
            rows = (await context.db.scalars(statement.order_by(desc(BusinessActionRecord.created_at)).limit(limit))).all()
            return CapabilityResult(True, False, {"actions": [_action_summary(row) for row in rows]})

        target_id = str(arguments.get("action_id") or "").strip()
        target = await context.db.scalar(
            select(BusinessActionRecord).where(
                BusinessActionRecord.id == target_id,
                BusinessActionRecord.tenant_id == context.tenant_id,
            )
        )
        if target is None:
            return CapabilityResult(False, False, {"reason": "action_not_found"})

        if capability_name == "actions.get":
            return CapabilityResult(
                True,
                False,
                {
                    **_action_summary(target),
                    "arguments": json.loads(target.arguments_json or "{}"),
                    "result": json.loads(target.result_json or "{}"),
                    "verification": json.loads(target.verification_json or "{}"),
                },
            )

        if target.status != ActionStatus.WAITING_APPROVAL:
            return CapabilityResult(
                False,
                False,
                {"reason": "action_not_waiting_for_approval", "status": target.status, "action_id": target.id},
            )

        # Resolve authority again at execution time. The model never supplies role or workspace authority.
        invocation = context.invocation or {}
        metadata = invocation.get("metadata") if isinstance(invocation.get("metadata"), dict) else {}
        execution = await resolve_execution_context(
            context.db,
            workspace_id=context.tenant_id,
            user_id=context.actor_id or "",
            channel=str(invocation.get("channel") or "operly"),
            conversation_id=str(metadata.get("_conversation_id") or "") or None,
            metadata=metadata,
            require_membership=True,
        )
        if "actions:approve" not in execution.permissions:
            return CapabilityResult(False, False, {"reason": "approval_not_authorized"})

        from packages.capabilities.defaults import default_registry

        service = ActionService(
            context.db,
            default_registry(),
            authority=set(execution.permissions),
            actor_id=context.actor_id,
        )
        if capability_name == "actions.approve":
            resolved = await service.approve(context.tenant_id, target.id)
            return CapabilityResult(
                resolved.status in {ActionStatus.VERIFIED, ActionStatus.VERIFICATION_FAILED, ActionStatus.FAILED},
                True,
                {"action": _action_summary(resolved), "result": json.loads(resolved.result_json or "{}"), "verification": json.loads(resolved.verification_json or "{}")},
                resolved.id,
            )
        if capability_name == "actions.reject":
            resolved = await service.reject(context.tenant_id, target.id)
            return CapabilityResult(True, True, {"action": _action_summary(resolved)}, resolved.id)

        return CapabilityResult(False, False, {"reason": "unsupported_action_capability"})

    async def verify(self, context, capability_name, arguments, result):
        del context, arguments
        if capability_name.startswith("actions."):
            return CapabilityResult(result.success, result.changed, {"observation_available": result.success, **result.evidence}, result.external_reference)
        return result
