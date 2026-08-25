from __future__ import annotations

from packages.capabilities.agent_harness import PluginAgentHarness, PluginInvocationContext
from packages.capabilities.contracts import CapabilityResult
from packages.capabilities.workflow_task_provider import WorkflowTaskProvider
from packages.plugins import default_plugin_runtime
from packages.tasks.workflow import WorkflowValidationError, validate_workflow


def _invoke_capabilities(workflow: dict) -> set[str]:
    output: set[str] = set()

    def visit(nodes):
        for node in nodes or ():
            if not isinstance(node, dict):
                continue
            node_type = str(node.get("type") or "")
            if node_type == "invoke":
                capability = str(node.get("capability") or "").strip()
                if capability:
                    output.add(capability)
            elif node_type == "model":
                output.add("model.invoke")
            elif node_type == "if":
                visit(node.get("then") or [])
                visit(node.get("else") or [])
            elif node_type == "foreach":
                visit(node.get("steps") or [])

    visit(workflow.get("steps") or [])
    return output


class RegistryWorkflowTaskProvider(WorkflowTaskProvider):
    """Workspace Task compiler that refuses invented or unusable capabilities.

    A workflow is checked against the current actor's real workspace authority,
    connector configuration, OAuth scopes, surface policy and provider health before
    persistence. Runtime execution still re-authorizes every invocation, so a role,
    connector or scope change after creation fails closed rather than inheriting stale
    authority.
    """

    @staticmethod
    def _plugin_context(context) -> PluginInvocationContext:
        invocation = context.invocation if isinstance(context.invocation, dict) else {}
        metadata = invocation.get("metadata") if isinstance(invocation.get("metadata"), dict) else {}
        return PluginInvocationContext(
            tenant_id=str(context.tenant_id or ""),
            user_id=context.actor_id,
            role="member",
            objective=str(metadata.get("task_objective") or "Validate scheduled workflow")[:12000],
            channel=str(invocation.get("channel") or metadata.get("origin_provider") or "task"),
            metadata=dict(metadata),
            surface=metadata.get("_surface_kind") or metadata.get("surface"),
        )

    async def _validate_workflow_capabilities(self, context, spec: dict) -> CapabilityResult | None:
        if self._personal_scope(context):
            return None

        capabilities = sorted(_invoke_capabilities(spec))
        manifests = default_plugin_runtime().manifests
        missing = [
            capability
            for capability in capabilities
            if manifests.owner_for_capability(capability) is None
        ]
        if missing:
            return CapabilityResult(
                False,
                False,
                {
                    "reason": "workflow_capabilities_not_registered",
                    "capabilities": missing,
                    "guidance": "Use capability.search/capability.describe and compile only registered plugin capabilities.",
                },
            )

        harness = PluginAgentHarness()
        plugin_context = self._plugin_context(context)
        unavailable: list[dict] = []
        for capability in capabilities:
            availability = await harness.availability(capability, plugin_context)
            if not bool(availability.get("available")):
                unavailable.append(
                    {
                        "capability": capability,
                        "reason": availability.get("reason"),
                        "configured": availability.get("configured"),
                        "healthy": availability.get("healthy"),
                        "permissionDenied": availability.get("permissionDenied"),
                        "missingConnector": availability.get("missingConnector"),
                        "missingScopes": list(availability.get("missingScopes") or []),
                        "retryable": availability.get("retryable"),
                        "nextAction": availability.get("nextAction"),
                    }
                )
        if unavailable:
            return CapabilityResult(
                False,
                False,
                {
                    "reason": "workflow_capability_unavailable",
                    "unavailable": unavailable,
                    "guidance": "Resolve the reported permission/connector/scope/health blockers before saving this workflow.",
                },
            )
        return None

    async def execute(self, context, capability_name, arguments):
        workflow = arguments.get("workflow") if isinstance(arguments, dict) else None
        if capability_name in {"task.create", "task.update"} and workflow is not None:
            try:
                spec = validate_workflow(workflow)
            except WorkflowValidationError as error:
                return CapabilityResult(False, False, {"reason": str(error)})
            if spec is not None:
                validation = await self._validate_workflow_capabilities(context, spec)
                if validation is not None:
                    return validation
        return await super().execute(context, capability_name, arguments)
