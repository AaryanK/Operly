import asyncio
import hashlib
import json
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.actions.policy import PolicyDecision, PolicyDecisionType, evaluate_action
from packages.capabilities.contracts import ApprovalPolicy
from packages.capabilities.providers import ProviderContext
from packages.capabilities.validation import validate_arguments
from packages.company.events import append_event
from packages.database.company_models import BusinessActionRecord
from packages.database.models import Approval


class ActionStatus(StrEnum):
    PROPOSED="PROPOSED"; WAITING_APPROVAL="WAITING_APPROVAL"; APPROVED="APPROVED"; EXECUTING="EXECUTING"
    EXECUTED="EXECUTED"; VERIFYING="VERIFYING"; VERIFIED="VERIFIED"; REJECTED="REJECTED"; FAILED="FAILED"; VERIFICATION_FAILED="VERIFICATION_FAILED"


class ActionService:
    def __init__(self, db: AsyncSession, registry, *, authority: set[str] | None = None, actor_id: str | None = None):
        self.db, self.registry, self.authority, self.actor_id = db, registry, authority, actor_id
    async def _event(self, action, event_type, payload=None):
        event=await append_event(self.db, tenant_id=action.tenant_id, event_type=event_type,
                                  payload={"action_id": action.id, "capability": action.capability, "status": action.status, **(payload or {})},
                                  correlation_id=action.correlation_id, causation_id=action.id, source="actions")
        normalized={
          ("messaging.send","action.proposed"):"message.send_requested",
          ("messaging.send","action.approved"):"message.send_approved",
          ("messaging.send","action.failed"):"message.send_failed",
          ("calendar.create_event","action.failed"):"calendar.event_failed",
        }.get((action.capability,event_type))
        if normalized:await append_event(self.db,tenant_id=action.tenant_id,event_type=normalized,payload={"action_id":action.id,**(payload or {})},correlation_id=action.correlation_id,causation_id=action.id,source="actions")
        return event
    async def propose(self, *, tenant_id: str, objective: str, capability: str, arguments: dict[str, Any], rationale: str,
                      expected_outcome: str, risk_level: str, causation_id: str | None = None,
                      idempotency_key: str | None = None) -> BusinessActionRecord:
        if idempotency_key:
            existing=await self.db.scalar(select(BusinessActionRecord).where(BusinessActionRecord.tenant_id==tenant_id,
                                                                              BusinessActionRecord.idempotency_key==idempotency_key))
            if existing:return existing
        provider=self.registry.resolve(tenant_id,capability,authority=self.authority)
        definition=next(item for item in provider.capabilities if item.id==capability or item.name==capability)
        validate_arguments(definition.input_schema,arguments)
        risk_level=definition.risk_level
        action = BusinessActionRecord(tenant_id=tenant_id, objective=objective, capability=capability,
                                      arguments_json=json.dumps(arguments, sort_keys=True), rationale=rationale,
                                      expected_outcome=expected_outcome, risk_level=risk_level,causation_id=causation_id,
                                      idempotency_key=idempotency_key)
        self.db.add(action); await self.db.flush(); await self._event(action, "action.proposed")
        decision = evaluate_action(action)
        if definition.approval_policy == ApprovalPolicy.AUTO:
            decision = PolicyDecision(PolicyDecisionType.ALLOW,"Plugin contract allows automatic execution")
        elif definition.approval_policy == ApprovalPolicy.ALWAYS:
            decision = PolicyDecision(PolicyDecisionType.REQUIRE_APPROVAL,"Plugin contract always requires approval")
        action.policy_decision = decision.decision.value
        if decision.decision == PolicyDecisionType.DENY:
            action.status = ActionStatus.REJECTED; await self._event(action, "action.rejected", {"reason": decision.reason})
        elif decision.decision == PolicyDecisionType.REQUIRE_APPROVAL:
            approval = Approval(tenant_id=tenant_id, action=capability,
                                payload_json=json.dumps({"business_action_id": action.id, "rationale": rationale, "arguments": arguments}))
            self.db.add(approval); await self.db.flush(); action.approval_id = approval.id; action.status = ActionStatus.WAITING_APPROVAL
            await self._event(action, "action.waiting_approval", {"approval_id": approval.id})
        else:
            await self.execute(action)
        return action
    async def execute(self, action: BusinessActionRecord) -> BusinessActionRecord:
        provider = self.registry.resolve(action.tenant_id, action.capability, authority=self.authority); action.provider = provider.name
        action.status = ActionStatus.EXECUTING; await self._event(action, "action.executing")
        arguments = json.loads(action.arguments_json)
        if action.approved_arguments_digest and action.approved_arguments_digest!=hashlib.sha256(action.arguments_json.encode()).hexdigest():
            action.status=ActionStatus.FAILED;await self._event(action,"action.failed",{"reason":"approved_payload_changed"});return action
        provider_context=ProviderContext(action.tenant_id,self.db,self.actor_id,
                                         self.registry.provider_config(action.tenant_id,action.capability),action.id)
        try: result = await asyncio.wait_for(provider.execute(provider_context, action.capability, arguments),timeout=30)
        except Exception as error:
            action.status = ActionStatus.FAILED; action.result_json = json.dumps({"error": str(error)}); await self._event(action, "action.failed"); return action
        action.result_json = json.dumps({"success": result.success, "changed": result.changed, "evidence": result.evidence,
                                         "external_reference": result.external_reference}, sort_keys=True)
        if result.success:
            try: validate_arguments(next(item for item in provider.capabilities if item.id==action.capability or item.name==action.capability).output_schema,result.evidence)
            except ValueError as error:
                action.status=ActionStatus.FAILED;action.result_json=json.dumps({"success":False,"error":f"Invalid plugin output: {error}"})
                await self._event(action,"action.failed",{"reason":"invalid_plugin_output"});return action
        if not result.success:
            action.status = ActionStatus.FAILED; await self._event(action, "action.failed", result.evidence); return action
        action.status = ActionStatus.EXECUTED; await self._event(action, "action.executed", result.evidence)
        action.status = ActionStatus.VERIFYING
        await self._event(action, "action.verifying")
        verified = await provider.verify(provider_context, action.capability, arguments, result)
        action.verification_json = json.dumps({"success": verified.success, "changed": verified.changed, "evidence": verified.evidence}, sort_keys=True)
        action.status = ActionStatus.VERIFIED if verified.success else ActionStatus.VERIFICATION_FAILED
        await self._event(action, "action.verified" if verified.success else "action.verification_failed", verified.evidence)
        return action
    async def approve(self, tenant_id: str, action_id: str):
        action = await self._get(tenant_id, action_id)
        if action.status != ActionStatus.WAITING_APPROVAL: raise ValueError("Action is not waiting for approval")
        approval = await self.db.get(Approval, action.approval_id); approval.status = "approved"
        action.approved_arguments_digest=hashlib.sha256(action.arguments_json.encode()).hexdigest()
        action.status = ActionStatus.APPROVED; await self._event(action, "action.approved",{"arguments_digest":action.approved_arguments_digest}); return await self.execute(action)
    async def reject(self, tenant_id: str, action_id: str):
        action = await self._get(tenant_id, action_id)
        if action.status != ActionStatus.WAITING_APPROVAL: raise ValueError("Action is not waiting for approval")
        approval = await self.db.get(Approval, action.approval_id); approval.status = "rejected"
        action.status = ActionStatus.REJECTED; await self._event(action, "action.rejected"); return action
    async def _get(self, tenant_id, action_id):
        action = await self.db.scalar(select(BusinessActionRecord).where(BusinessActionRecord.id == action_id,
                                                                         BusinessActionRecord.tenant_id == tenant_id))
        if action is None: raise LookupError("Action not found")
        return action
