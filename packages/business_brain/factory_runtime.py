"""Workspace binding for the Agent Factory control plane.

This module deliberately keeps the existing PluginAgentHarness as the authority and
execution boundary. The Factory changes orchestration/context distribution only; it
never bypasses capability availability, surface policy, approval, argument validation
or the Action-backed firewall.
"""
from __future__ import annotations

import os
from typing import Any

from packages.agents.control_plane import (
    AgentFactoryControlPlane,
    AuthorizedContextBindings,
    EvidenceBoundedSemanticValidator,
    FactoryCapabilityIntentResolver,
    SandboxPythonValidator,
)
from packages.capabilities.agent_harness import PluginAgentHarness, PluginInvocationContext
from packages.security.execution_context import ExecutionContext


_FACTORY_ENV = "OPERLY_WORKSPACE_AGENT_FACTORY"
_TRUE_VALUES = frozenset({"1", "true", "yes", "on", "enabled"})


def workspace_factory_enabled() -> bool:
    """Return the deployment-controlled Workspace Factory switch.

    This is intentionally environment-only. External channel metadata cannot opt a
    principal into a different control plane.
    """

    return str(os.getenv(_FACTORY_ENV, "0")).strip().lower() in _TRUE_VALUES


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

    authority = set(execution.permissions)
    registry = await plugin_harness.registry_for(plugin_context)
    session_view = await plugin_harness.session_view_for(
        plugin_context,
        authority=authority,
        registry=registry,
    )
    context = AuthorizedContextBindings(
        execution=execution,
        tenant_id=request.tenant_id,
        user_id=plugin_context.user_id,
        conversation_id=conversation_id,
    )
    capability_resolver = FactoryCapabilityIntentResolver(
        registry=registry,
        scope_id=request.tenant_id,
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
                request.tenant_id,
                capability_id,
                authority=authority,
            )
            if not availability.available:
                return False
            session_view.expose([capability_id])
        except (LookupError, PermissionError, ValueError):
            return False
        return capability_id in session_view.exposed_ids

    run_metadata = {
        "tenant_id": request.tenant_id,
        "user_id": plugin_context.user_id,
        "principal_id": request.principal_id,
        "channel": request.channel,
        "surface": execution.surface.value,
        "workspace_mode": execution.workspace_mode,
        "executor_role": "business_agent",
        "_conversation_id": conversation_id,
    }
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

    factory = AgentFactoryControlPlane(
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
