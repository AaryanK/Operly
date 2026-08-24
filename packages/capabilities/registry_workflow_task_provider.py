from __future__ import annotations

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
    """Workspace Task compiler that refuses invented workflow capabilities.

    PluginManifest is the extension contract. A future plugin registers its capability
    and event resources there; Task compilation discovers them automatically. The
    persisted declaration is still re-authorized by PluginAgentHarness on every run.
    """

    async def execute(self, context, capability_name, arguments):
        workflow = arguments.get("workflow") if isinstance(arguments, dict) else None
        if capability_name in {"task.create", "task.update"} and workflow is not None:
            try:
                spec = validate_workflow(workflow)
            except WorkflowValidationError as error:
                return CapabilityResult(False, False, {"reason": str(error)})
            if spec is not None and not self._personal_scope(context):
                registry = default_plugin_runtime().manifests
                capabilities = sorted(_invoke_capabilities(spec))
                missing = [
                    capability
                    for capability in capabilities
                    if registry.owner_for_capability(capability) is None
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
        return await super().execute(context, capability_name, arguments)
