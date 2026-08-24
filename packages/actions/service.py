import asyncio
import hashlib
import json
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.actions.policy import PolicyDecision, PolicyDecisionType, evaluate_action
from packages.capabilities.contracts import ApprovalPolicy
from packages.capabilities.runtime_context import ProviderContext
from packages.capabilities.validation import validate_arguments
from packages.company.events import append_event
from packages.database.company_models import BusinessActionRecord
from packages.database.models import Approval
from packages.database.runtime_trace_events import emit_runtime_trace_event
from packages.model_runtime.trace_events import RuntimeTraceEvent
from packages.security.execution_context import ScopeKind


class ActionStatus(StrEnum):
    PROPOSED = "PROPOSED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    APPROVED = "APPROVED"
    EXECUTING = "EXECUTING"
    EXECUTED = "EXECUTED"
    VERIFYING = "VERIFYING"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"


def _coerce_scope_kind(value: ScopeKind | str) -> ScopeKind:
    try:
        return value if isinstance(value, ScopeKind) else ScopeKind(str(value))
    except ValueError as error:
        raise ValueError("Unknown action scope") from error


def _validated_scope(
    *,
    scope_kind: ScopeKind | str,
    tenant_id: str | None,
    owner_user_id: str | None,
) -> tuple[ScopeKind, str | None, str | None, str]:
    kind = _coerce_scope_kind(scope_kind)
    workspace_id = str(tenant_id or "").strip() or None
    personal_owner_id = str(owner_user_id or "").strip() or None

    if kind is ScopeKind.WORKSPACE:
        if workspace_id is None or personal_owner_id is not None:
            raise ValueError("Workspace actions require tenant_id and no personal owner")
        return kind, workspace_id, None, workspace_id

    if personal_owner_id is None or workspace_id is not None:
        raise ValueError("Personal actions require owner_user_id and no tenant_id")
    return kind, None, personal_owner_id, f"personal:{personal_owner_id}"


def _action_scope(action: BusinessActionRecord) -> tuple[ScopeKind, str | None, str | None, str]:
    return _validated_scope(
        scope_kind=action.scope_kind,
        tenant_id=action.tenant_id,
        owner_user_id=action.owner_user_id,
    )


def _scope_trace_payload(action: BusinessActionRecord) -> dict[str, str]:
    kind, tenant_id, owner_user_id, scope_id = _action_scope(action)
    payload = {"scope_kind": kind.value, "scope_id": scope_id}
    if tenant_id:
        payload["tenant_id"] = tenant_id
    if owner_user_id:
        payload["owner_user_id"] = owner_user_id
    return payload


class ActionService:
    def __init__(
        self,
        db: AsyncSession,
        registry,
        *,
        authority: set[str] | None = None,
        actor_id: str | None = None,
    ):
        self.db = db
        self.registry = registry
        self.authority = authority
        self.actor_id = actor_id

    async def _event(self, action, event_type, payload=None):
        # BusinessEventRecord is deliberately workspace/business-scoped. Personal
        # actions remain durable in business_actions/approvals and runtime traces;
        # they must not fabricate a Tenant solely to fit the business event stream.
        if action.tenant_id is None:
            return None

        provenance = {
            key: value
            for key, value in {
                "principal_id": action.principal_id,
                "client_id": action.client_id,
                "origin": action.origin,
                "connector_id": action.connector_id,
                "resource_type": action.resource_type,
                "scope_kind": action.scope_kind,
            }.items()
            if value
        }
        event = await append_event(
            self.db,
            tenant_id=action.tenant_id,
            event_type=event_type,
            payload={
                "action_id": action.id,
                "capability": action.capability,
                "status": action.status,
                **provenance,
                **(payload or {}),
            },
            correlation_id=action.correlation_id,
            causation_id=action.id,
            source="actions",
        )
        normalized = {
            ("messaging.send", "action.proposed"): "message.send_requested",
            ("messaging.send", "action.approved"): "message.send_approved",
            ("messaging.send", "action.failed"): "message.send_failed",
            ("calendar.create_event", "action.failed"): "calendar.event_failed",
            ("solution.apply_improvement", "action.rejected"): "solution.change.rejected",
        }.get((action.capability, event_type))
        if normalized:
            await append_event(
                self.db,
                tenant_id=action.tenant_id,
                event_type=normalized,
                payload={"action_id": action.id, **provenance, **(payload or {})},
                correlation_id=action.correlation_id,
                causation_id=action.id,
                source="actions",
            )
        return event

    async def propose(
        self,
        *,
        tenant_id: str | None,
        objective: str,
        capability: str,
        arguments: dict[str, Any],
        rationale: str,
        expected_outcome: str,
        risk_level: str,
        owner_user_id: str | None = None,
        scope_kind: ScopeKind | str = ScopeKind.WORKSPACE,
        causation_id: str | None = None,
        idempotency_key: str | None = None,
        runtime_context: dict[str, Any] | None = None,
    ) -> BusinessActionRecord:
        kind, workspace_id, personal_owner_id, registry_scope_id = _validated_scope(
            scope_kind=scope_kind,
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
        )
        if kind is ScopeKind.PERSONAL and self.actor_id != personal_owner_id:
            raise PermissionError("Personal action owner must match the authenticated actor")

        if idempotency_key:
            scope_predicate = (
                BusinessActionRecord.tenant_id == workspace_id
                if kind is ScopeKind.WORKSPACE
                else BusinessActionRecord.owner_user_id == personal_owner_id
            )
            existing = await self.db.scalar(
                select(BusinessActionRecord).where(
                    BusinessActionRecord.scope_kind == kind.value,
                    scope_predicate,
                    BusinessActionRecord.idempotency_key == idempotency_key,
                )
            )
            if existing:
                return existing

        provider = self.registry.resolve(
            registry_scope_id,
            capability,
            authority=self.authority,
        )
        definition = next(
            item
            for item in provider.capabilities
            if item.id == capability or item.name == capability
        )
        validate_arguments(definition.input_schema, arguments)
        risk_level = definition.risk_level

        runtime = dict(runtime_context or {})
        metadata = runtime.get("metadata") if isinstance(runtime.get("metadata"), dict) else {}
        principal_id = str(metadata.get("principal_id") or "").strip() or (
            f"user:{self.actor_id}" if self.actor_id else None
        )
        client_id = str(metadata.get("client_id") or "").strip() or None
        origin = str(runtime.get("channel") or metadata.get("origin") or "").strip() or None
        connector_id = str(
            metadata.get("connector_id")
            or definition.integration_provider
            or definition.provider
            or ""
        ).strip() or None
        resource_type = str(
            metadata.get("resource_type") or definition.category or ""
        ).strip() or None

        action = BusinessActionRecord(
            tenant_id=workspace_id,
            scope_kind=kind.value,
            owner_user_id=personal_owner_id,
            objective=objective,
            capability=capability,
            arguments_json=json.dumps(arguments, sort_keys=True),
            rationale=rationale,
            expected_outcome=expected_outcome,
            risk_level=risk_level,
            causation_id=causation_id,
            idempotency_key=idempotency_key,
            principal_id=principal_id,
            client_id=client_id,
            origin=origin,
            connector_id=connector_id,
            resource_type=resource_type,
        )
        self.db.add(action)
        await self.db.flush()
        await self._event(action, "action.proposed")
        await emit_runtime_trace_event(
            RuntimeTraceEvent.ACTION_CREATED,
            {
                "action_id": action.id,
                "capability": capability,
                "risk_level": risk_level,
                "connector_id": connector_id,
                "authority_source": runtime.get("authority_source") or metadata.get("authority_source"),
                **_scope_trace_payload(action),
            },
            resource_id=action.id,
        )

        decision = evaluate_action(action)
        if definition.approval_policy == ApprovalPolicy.AUTO:
            decision = PolicyDecision(
                PolicyDecisionType.ALLOW,
                "Plugin contract allows automatic execution",
            )
        elif definition.approval_policy == ApprovalPolicy.ALWAYS:
            decision = PolicyDecision(
                PolicyDecisionType.REQUIRE_APPROVAL,
                "Plugin contract always requires approval",
            )

        action.policy_decision = decision.decision.value
        if decision.decision == PolicyDecisionType.DENY:
            action.status = ActionStatus.REJECTED
            await self._event(action, "action.rejected", {"reason": decision.reason})
        elif decision.decision == PolicyDecisionType.REQUIRE_APPROVAL:
            approval = Approval(
                tenant_id=workspace_id,
                scope_kind=kind.value,
                owner_user_id=personal_owner_id,
                action=capability,
                payload_json=json.dumps(
                    {
                        "business_action_id": action.id,
                        "rationale": rationale,
                        "arguments": arguments,
                        "scope_kind": kind.value,
                        "scope_id": registry_scope_id,
                    }
                ),
            )
            self.db.add(approval)
            await self.db.flush()
            action.approval_id = approval.id
            action.status = ActionStatus.WAITING_APPROVAL
            await self._event(
                action,
                "action.waiting_approval",
                {"approval_id": approval.id},
            )
            await emit_runtime_trace_event(
                RuntimeTraceEvent.APPROVAL_REQUESTED,
                {
                    "action_id": action.id,
                    "approval_id": approval.id,
                    "capability": capability,
                    **_scope_trace_payload(action),
                },
                resource_id=approval.id,
            )
        else:
            await self.execute(action, runtime_context=runtime)
        return action

    async def execute(
        self,
        action: BusinessActionRecord,
        *,
        runtime_context: dict[str, Any] | None = None,
    ) -> BusinessActionRecord:
        kind, workspace_id, personal_owner_id, registry_scope_id = _action_scope(action)
        if kind is ScopeKind.PERSONAL and self.actor_id != personal_owner_id:
            raise PermissionError("Personal action owner must match the authenticated actor")

        provider = self.registry.resolve(
            registry_scope_id,
            action.capability,
            authority=self.authority,
        )
        definition = next(
            item
            for item in provider.capabilities
            if item.id == action.capability or item.name == action.capability
        )
        action.provider = provider.name
        action.status = ActionStatus.EXECUTING
        await self._event(action, "action.executing")
        arguments = json.loads(action.arguments_json)

        if (
            action.approved_arguments_digest
            and action.approved_arguments_digest
            != hashlib.sha256(action.arguments_json.encode()).hexdigest()
        ):
            action.status = ActionStatus.FAILED
            await self._event(action, "action.failed", {"reason": "approved_payload_changed"})
            return action

        provider_context = ProviderContext(
            tenant_id=workspace_id,
            db=self.db,
            actor_id=self.actor_id,
            provider_config=self.registry.provider_config(registry_scope_id, action.capability),
            execution_id=action.id,
            invocation=runtime_context,
            scope_kind=kind.value,
            scope_id=registry_scope_id,
            owner_user_id=personal_owner_id,
        )
        connector = str(definition.integration_provider or "").strip() or None
        if connector:
            await emit_runtime_trace_event(
                RuntimeTraceEvent.CONNECTOR_REQUEST,
                {
                    "action_id": action.id,
                    "capability": action.capability,
                    "connector": connector,
                    "argument_keys": sorted(arguments),
                    **_scope_trace_payload(action),
                },
                resource_id=f"{connector}:{action.capability}",
            )
        try:
            result = await asyncio.wait_for(
                provider.execute(provider_context, action.capability, arguments),
                timeout=30,
            )
        except Exception as error:
            if connector:
                await emit_runtime_trace_event(
                    RuntimeTraceEvent.CONNECTOR_RESPONSE,
                    {
                        "action_id": action.id,
                        "capability": action.capability,
                        "connector": connector,
                        "success": False,
                        "error_type": type(error).__name__,
                        **_scope_trace_payload(action),
                    },
                    resource_id=f"{connector}:{action.capability}",
                    classification=type(error).__name__,
                )
            action.status = ActionStatus.FAILED
            action.result_json = json.dumps({"error": str(error)})
            await self._event(action, "action.failed")
            return action

        if connector:
            await emit_runtime_trace_event(
                RuntimeTraceEvent.CONNECTOR_RESPONSE,
                {
                    "action_id": action.id,
                    "capability": action.capability,
                    "connector": connector,
                    "success": bool(result.success),
                    "changed": bool(result.changed),
                    "external_reference": result.external_reference,
                    "evidence_keys": sorted(result.evidence) if isinstance(result.evidence, dict) else [],
                    **_scope_trace_payload(action),
                },
                resource_id=f"{connector}:{action.capability}",
            )

        action.result_json = json.dumps(
            {
                "success": result.success,
                "changed": result.changed,
                "evidence": result.evidence,
                "external_reference": result.external_reference,
            },
            sort_keys=True,
        )
        if result.success:
            try:
                output_schema = definition.output_schema
                validate_arguments(output_schema, result.evidence)
            except ValueError as error:
                action.status = ActionStatus.FAILED
                action.result_json = json.dumps(
                    {"success": False, "error": f"Invalid plugin output: {error}"}
                )
                await self._event(action, "action.failed", {"reason": "invalid_plugin_output"})
                return action

        if not result.success:
            action.status = ActionStatus.FAILED
            await self._event(action, "action.failed", result.evidence)
            return action

        action.status = ActionStatus.EXECUTED
        await self._event(action, "action.executed", result.evidence)
        action.status = ActionStatus.VERIFYING
        await self._event(action, "action.verifying")

        verified = await provider.verify(provider_context, action.capability, arguments, result)
        action.verification_json = json.dumps(
            {
                "success": verified.success,
                "changed": verified.changed,
                "evidence": verified.evidence,
            },
            sort_keys=True,
        )
        action.status = (
            ActionStatus.VERIFIED if verified.success else ActionStatus.VERIFICATION_FAILED
        )
        await self._event(
            action,
            "action.verified" if verified.success else "action.verification_failed",
            verified.evidence,
        )
        return action

    async def approve(self, tenant_id: str, action_id: str):
        action = await self._get_workspace(tenant_id, action_id)
        return await self._approve_action(action)

    async def reject(self, tenant_id: str, action_id: str):
        action = await self._get_workspace(tenant_id, action_id)
        return await self._reject_action(action)

    async def approve_personal(self, owner_user_id: str, action_id: str):
        if self.actor_id != owner_user_id:
            raise PermissionError("Only the Personal action owner may approve this action")
        action = await self._get_personal(owner_user_id, action_id)
        return await self._approve_action(action)

    async def reject_personal(self, owner_user_id: str, action_id: str):
        if self.actor_id != owner_user_id:
            raise PermissionError("Only the Personal action owner may reject this action")
        action = await self._get_personal(owner_user_id, action_id)
        return await self._reject_action(action)

    async def _approve_action(self, action: BusinessActionRecord):
        if action.status != ActionStatus.WAITING_APPROVAL:
            raise ValueError("Action is not waiting for approval")
        approval = await self._approval_for_action(action)
        approval.status = "approved"
        action.approved_arguments_digest = hashlib.sha256(action.arguments_json.encode()).hexdigest()
        action.status = ActionStatus.APPROVED
        await self._event(
            action,
            "action.approved",
            {"arguments_digest": action.approved_arguments_digest},
        )
        await emit_runtime_trace_event(
            RuntimeTraceEvent.APPROVAL_RESOLVED,
            {
                "action_id": action.id,
                "approval_id": action.approval_id,
                "approved": True,
                **_scope_trace_payload(action),
            },
            resource_id=str(action.approval_id or action.id),
        )
        await emit_runtime_trace_event(
            RuntimeTraceEvent.ACTION_RESUMED,
            {
                "action_id": action.id,
                "capability": action.capability,
                **_scope_trace_payload(action),
            },
            resource_id=action.id,
        )
        return await self.execute(action)

    async def _reject_action(self, action: BusinessActionRecord):
        if action.status != ActionStatus.WAITING_APPROVAL:
            raise ValueError("Action is not waiting for approval")
        approval = await self._approval_for_action(action)
        approval.status = "rejected"
        action.status = ActionStatus.REJECTED
        await self._event(action, "action.rejected")
        await emit_runtime_trace_event(
            RuntimeTraceEvent.APPROVAL_RESOLVED,
            {
                "action_id": action.id,
                "approval_id": action.approval_id,
                "approved": False,
                **_scope_trace_payload(action),
            },
            resource_id=str(action.approval_id or action.id),
        )
        return action

    async def _approval_for_action(self, action: BusinessActionRecord) -> Approval:
        approval = await self.db.get(Approval, action.approval_id)
        if approval is None:
            raise LookupError("Approval not found")
        if (
            approval.scope_kind != action.scope_kind
            or approval.tenant_id != action.tenant_id
            or approval.owner_user_id != action.owner_user_id
        ):
            raise PermissionError("Approval scope does not match action scope")
        return approval

    async def _get_workspace(self, tenant_id: str, action_id: str):
        action = await self.db.scalar(
            select(BusinessActionRecord).where(
                BusinessActionRecord.id == action_id,
                BusinessActionRecord.scope_kind == ScopeKind.WORKSPACE.value,
                BusinessActionRecord.tenant_id == tenant_id,
                BusinessActionRecord.owner_user_id.is_(None),
            )
        )
        if action is None:
            raise LookupError("Action not found")
        return action

    async def _get_personal(self, owner_user_id: str, action_id: str):
        action = await self.db.scalar(
            select(BusinessActionRecord).where(
                BusinessActionRecord.id == action_id,
                BusinessActionRecord.scope_kind == ScopeKind.PERSONAL.value,
                BusinessActionRecord.tenant_id.is_(None),
                BusinessActionRecord.owner_user_id == owner_user_id,
            )
        )
        if action is None:
            raise LookupError("Action not found")
        return action

    async def _get(self, tenant_id, action_id):
        # Compatibility shim for older internal callers. New code should choose an
        # explicit workspace or Personal lookup so scope ownership cannot be guessed.
        return await self._get_workspace(tenant_id, action_id)
