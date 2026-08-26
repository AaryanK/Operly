"""Workspace binding for the Agent Factory control plane.

This module deliberately keeps the existing PluginAgentHarness as the authority and
execution boundary. The Factory changes orchestration/context distribution only; it
never bypasses capability availability, surface policy, approval, argument validation
or the Action-backed firewall.
"""
from __future__ import annotations

import json
import os
from typing import Any

from packages.agents.control_plane import (
    AgentFactoryControlPlane,
    AuthorizedContextBindings,
    EvidenceBoundedSemanticValidator,
    FactoryCapabilityIntentResolver,
    SandboxPythonValidator,
    StageWorkerResult,
)
from packages.agents.control_plane.worker_adapter import factory_run_id_from_causation
from packages.capabilities.agent_harness import PluginAgentHarness, PluginInvocationContext
from packages.database.artifact_models import AgentRunRecord
from packages.database.company_models import BusinessActionRecord
from packages.database.db import session_scope
from packages.security.execution_context import (
    ExecutionContext,
    ExecutionContextError,
    resolve_execution_context,
)
from packages.security.surfaces import SurfaceKind


_FACTORY_ENV = "OPERLY_WORKSPACE_AGENT_FACTORY"
_TRUE_VALUES = frozenset({"1", "true", "yes", "on", "enabled"})


def workspace_factory_enabled() -> bool:
    """Return the deployment-controlled Workspace Factory switch.

    This is intentionally environment-only. External channel metadata cannot opt a
    principal into a different control plane.
    """

    return str(os.getenv(_FACTORY_ENV, "0")).strip().lower() in _TRUE_VALUES


def _json_object(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _strings(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item) for item in value if str(item).strip())
    return ()


def _action_handles(*payloads: dict[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    artifacts: set[str] = set()
    evidence_refs: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                lowered = str(key).lower()
                if lowered in {"artifact_id", "artifact_ref"}:
                    artifacts.update(_strings(item))
                elif lowered in {"artifact_ids", "artifact_refs"}:
                    artifacts.update(_strings(item))
                elif lowered in {"evidence_ref", "evidence_refs"}:
                    evidence_refs.update(_strings(item))
                if isinstance(item, (dict, list, tuple)):
                    walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value[:50]:
                walk(item)

    for payload in payloads:
        walk(payload)
    return tuple(sorted(artifacts)), tuple(sorted(evidence_refs))


def _stage_result_from_action(action: BusinessActionRecord) -> StageWorkerResult:
    """Translate one durable action into stage evidence without replaying it."""

    status = str(action.status or "").strip().upper()
    result = _json_object(action.result_json)
    verification = _json_object(action.verification_json)
    artifacts, evidence_refs = _action_handles(result, verification)
    evidence_set = set(evidence_refs)
    evidence_set.add(f"action:{action.id}")

    if status == "VERIFIED":
        stage_status = "completed"
    elif status == "REJECTED":
        stage_status = "rejected"
    elif status in {"FAILED", "VERIFICATION_FAILED"}:
        stage_status = "failed"
    else:
        # Approval resolution is only a resume signal once the durable action is in a
        # terminal state. Anything else stays pending rather than fabricating success.
        stage_status = "waiting_external"

    verified = status == "VERIFIED"
    return StageWorkerResult(
        status=stage_status,
        strategy=f"durable_action:{action.capability}"[:1000],
        summary=(
            f"Approved action {action.id} verified."
            if verified
            else f"Action {action.id} reached state {status}."
        ),
        artifacts=artifacts,
        evidence={
            "action_id": action.id,
            "approval_id": action.approval_id,
            "capability": action.capability,
            "action_status": status,
            "verified": verified,
            "result": result,
            "verification": verification,
            "approval_decision": "rejected" if status == "REJECTED" else "approved",
            "terminal": status in {"VERIFIED", "REJECTED", "FAILED", "VERIFICATION_FAILED"},
        },
        evidence_refs=tuple(sorted(evidence_set)),
        external_actions=0,
        token_usage=0,
        cost_usd=0.0,
    )


async def _factory_for_workspace(
    *,
    tenant_id: str,
    execution: ExecutionContext,
    plugin_harness: PluginAgentHarness,
    plugin_context: PluginInvocationContext,
) -> AgentFactoryControlPlane:
    authority = set(execution.permissions)
    registry = await plugin_harness.registry_for(plugin_context)
    session_view = await plugin_harness.session_view_for(
        plugin_context,
        authority=authority,
        registry=registry,
    )
    context = AuthorizedContextBindings(
        execution=execution,
        tenant_id=tenant_id,
        user_id=plugin_context.user_id,
        conversation_id=plugin_context.metadata.get("_conversation_id"),
    )
    capability_resolver = FactoryCapabilityIntentResolver(
        registry=registry,
        scope_id=tenant_id,
        authority=authority,
        visible_predicate=lambda capability_id: plugin_harness.capability_authorized(
            capability_id,
            authority,
            plugin_context,
        ),
        session_view=session_view,
    )

    async def schemas():
        # The application-side resolver exposes only the capabilities selected for the
        # current stage. PluginAgentHarness applies authority/surface policy again.
        return await plugin_harness.schemas(plugin_context)

    async def invoke(name: str, arguments: dict, call_id: str | None):
        return await plugin_harness.invoke(
            name,
            arguments,
            plugin_context,
            call_id=call_id,
        )

    def expose_validator(capability_id: str) -> bool:
        if not plugin_harness.capability_authorized(
            capability_id,
            authority,
            plugin_context,
        ):
            return False
        try:
            availability = registry.availability(
                tenant_id,
                capability_id,
                authority=authority,
            )
            if not availability.available:
                return False
            session_view.expose([capability_id])
        except (LookupError, PermissionError, ValueError):
            return False
        return capability_id in session_view.exposed_ids

    return AgentFactoryControlPlane(
        schemas=schemas,
        invoke=invoke,
        context_search=context.search,
        context_materialize=context.materialize,
        capability_resolver=capability_resolver,
        python_validator=SandboxPythonValidator(
            invoke=invoke,
            expose=expose_validator,
        ),
        semantic_validator=EvidenceBoundedSemanticValidator(),
        max_worker_steps=8,
        max_parallelism=4,
    )


def _run_metadata(
    *,
    tenant_id: str,
    user_id: str | None,
    principal_id: str,
    channel: str,
    execution: ExecutionContext,
    conversation_id: str | None,
) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "principal_id": principal_id,
        "channel": channel,
        "surface": execution.surface.value,
        "workspace_mode": execution.workspace_mode,
        "executor_role": "business_agent",
        "_conversation_id": conversation_id,
    }


async def run_workspace_factory(
    *,
    objective: str,
    request,
    conversation_id: str,
    execution: ExecutionContext,
    plugin_harness: PluginAgentHarness,
    plugin_context: PluginInvocationContext,
) -> dict[str, Any]:
    """Run one Workspace/Guest Workspace turn through the Factory."""

    factory = await _factory_for_workspace(
        tenant_id=request.tenant_id,
        execution=execution,
        plugin_harness=plugin_harness,
        plugin_context=plugin_context,
    )
    run_metadata = _run_metadata(
        tenant_id=request.tenant_id,
        user_id=plugin_context.user_id,
        principal_id=request.principal_id,
        channel=request.channel,
        execution=execution,
        conversation_id=conversation_id,
    )
    ingress_metadata = {
        # Names/counts tell the blueprint compiler that attachment context may be
        # needed; actual retained attachment content is discovered/materialized later
        # through AuthorizedContextBindings, not injected here.
        "attachment_count": len(request.attachment_names or ()),
        "attachment_names": list(request.attachment_names or ())[:20],
        "has_images": bool(request.images),
        "channel": request.channel,
        "surface": execution.surface.value,
    }
    facts = {
        "workspace_mode": execution.workspace_mode,
        "surface": execution.surface.value,
        "attachment_names": list(request.attachment_names or ())[:20],
    }

    response = await factory.run(
        objective=objective,
        metadata=run_metadata,
        ingress_metadata=ingress_metadata,
        facts=facts,
    )
    payload = response.as_dict()
    return {
        "message": response.message,
        "runtime_run_id": response.runtime_run_id,
        "stop_reason": response.execution.stop_reason,
        "replans": sum(
            max(0, attempt.attempt - 1)
            for attempt in response.execution.attempts
        ),
        "run_plan": response.blueprint,
        "execution_truth": payload.get("execution_truth"),
        "factory": payload.get("factory"),
    }


async def resume_workspace_factory_after_action(
    *,
    tenant_id: str,
    action_id: str,
) -> dict[str, Any] | None:
    """Resume the exact Factory stage correlated to a terminal workspace action.

    The approving admin is deliberately *not* used as worker authority. The original
    direct user principal is loaded from AgentRunRecord and re-authorized against the
    workspace as it exists now. Delegated/guest principals need their source-specific
    live authority reconstructed by their ingress adapter; this web approval hook fails
    closed for them instead of silently widening to the approver's permissions.
    """

    async with session_scope() as db:
        action = await db.get(BusinessActionRecord, str(action_id))
        if (
            action is None
            or action.scope_kind != "workspace"
            or action.tenant_id != tenant_id
        ):
            return None
        runtime_run_id = factory_run_id_from_causation(action.causation_id)
        if not runtime_run_id:
            return None
        run = await db.get(AgentRunRecord, runtime_run_id)
        if (
            run is None
            or run.scope_kind != "workspace"
            or run.tenant_id != tenant_id
            or run.scope_id != tenant_id
        ):
            return {
                "resumed": False,
                "runtime_run_id": runtime_run_id,
                "reason": "factory_run_unavailable",
            }

        try:
            checkpoint = json.loads(run.checkpoint_json or "{}")
        except json.JSONDecodeError:
            checkpoint = {}
        factory_state = checkpoint.get("factory") if isinstance(checkpoint, dict) else None
        waiting_stages = (
            dict(factory_state.get("waiting_stages") or {})
            if isinstance(factory_state, dict)
            else {}
        )
        matching = [
            stage_id
            for stage_id, details in waiting_stages.items()
            if isinstance(details, dict)
            and str(details.get("action_id") or "") == action.id
        ]
        if len(matching) != 1:
            return {
                "resumed": False,
                "runtime_run_id": runtime_run_id,
                "reason": "factory_waiting_stage_not_found",
            }
        stage_id = matching[0]

        original_user_id = str(run.actor_id or "").strip() or None
        original_principal = str(action.principal_id or "").strip()
        expected_principal = f"user:{original_user_id}" if original_user_id else ""
        if not original_user_id or original_principal != expected_principal:
            return {
                "resumed": False,
                "runtime_run_id": runtime_run_id,
                "stage_id": stage_id,
                "reason": "original_principal_requires_live_ingress_authority",
            }

        try:
            execution = await resolve_execution_context(
                db,
                workspace_id=tenant_id,
                user_id=original_user_id,
                channel=run.channel,
                surface=SurfaceKind.coerce(run.surface),
                conversation_id=run.conversation_id,
                metadata={
                    "principal_id": original_principal,
                    "_surface_kind": run.surface,
                },
                require_membership=True,
            )
        except ExecutionContextError:
            return {
                "resumed": False,
                "runtime_run_id": runtime_run_id,
                "stage_id": stage_id,
                "reason": "original_authority_unavailable",
            }

        action_result = _stage_result_from_action(action)
        objective = str(run.objective or "").strip()
        conversation_id = run.conversation_id
        channel = str(run.channel or "operly")

    plugin_harness = PluginAgentHarness()
    plugin_metadata = {
        "_conversation_id": conversation_id,
        "_surface_kind": execution.surface.value,
        "principal_id": original_principal,
        "workspace_mode": execution.workspace_mode,
        "effective_permissions": sorted(execution.permissions),
    }
    plugin_context = PluginInvocationContext(
        tenant_id=tenant_id,
        user_id=original_user_id,
        role=execution.role,
        objective=objective,
        channel=channel,
        metadata=plugin_metadata,
        surface=execution.surface,
        principal_id=original_principal,
    )
    factory = await _factory_for_workspace(
        tenant_id=tenant_id,
        execution=execution,
        plugin_harness=plugin_harness,
        plugin_context=plugin_context,
    )
    response = await factory.resume(
        runtime_run_id=runtime_run_id,
        metadata=_run_metadata(
            tenant_id=tenant_id,
            user_id=original_user_id,
            principal_id=original_principal,
            channel=channel,
            execution=execution,
            conversation_id=conversation_id,
        ),
        stage_id=stage_id,
        stage_result=action_result,
        facts={
            "workspace_mode": execution.workspace_mode,
            "surface": execution.surface.value,
            "resumed_from_action_id": action_id,
        },
    )
    payload = response.as_dict()
    return {
        "resumed": True,
        "runtime_run_id": runtime_run_id,
        "stage_id": stage_id,
        "stop_reason": response.execution.stop_reason,
        "execution_truth": payload.get("execution_truth"),
        "factory": payload.get("factory"),
    }
