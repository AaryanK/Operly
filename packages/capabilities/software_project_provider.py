from __future__ import annotations

from packages.capabilities.contracts import ApprovalPolicy, CapabilityDefinition, CapabilityResult
from packages.capabilities.providers import BaseProvider
from packages.service_bindings import ServiceBindingStore
from packages.software_projects import SoftwareProjectService


def _project_json(project) -> dict:
    return {
        "id": project.id,
        "workspace_id": project.workspace_id,
        "name": project.name,
        "description": project.description,
        "state": project.state.value,
        "active_source_version_id": project.active_source_version_id,
        "active_runtime_id": project.active_runtime_id,
        "service_binding_ids": list(project.service_binding_ids),
        "metadata": dict(project.metadata),
        "created_at": project.created_at.isoformat() if project.created_at else None,
        "updated_at": project.updated_at.isoformat() if project.updated_at else None,
    }


def _binding_json(binding) -> dict:
    return {
        "id": binding.id,
        "project_id": binding.project_id,
        "workspace_id": binding.workspace_id,
        "semantic_name": binding.semantic_name,
        "capability_id": binding.capability_id,
        "capability_version": binding.capability_version,
        "binding_mode": binding.binding_mode,
        "principal_scope": binding.principal_scope,
        "configuration": dict(binding.configuration),
        "created_at": binding.created_at.isoformat() if binding.created_at else None,
    }


def _plugin_context(context, objective: str):
    # Import lazily: agent_harness imports the default registry, which registers this
    # provider. A module-level import would form defaults -> provider -> harness ->
    # defaults and make the entire capability/task surface fail during collection.
    from packages.capabilities.agent_harness import PluginInvocationContext

    invocation = context.invocation if isinstance(context.invocation, dict) else {}
    metadata = invocation.get("metadata") if isinstance(invocation.get("metadata"), dict) else {}
    return PluginInvocationContext(
        tenant_id=str(context.tenant_id or ""),
        user_id=context.actor_id,
        role="member",
        objective=objective[:12000],
        channel=str(invocation.get("channel") or "software"),
        metadata=dict(metadata),
        surface=metadata.get("_surface_kind") or metadata.get("surface"),
    )


class SoftwareProjectProvider(BaseProvider):
    """Model-visible facade over canonical SoftwareProject and ServiceBinding state.

    Projects are the durable identity shared by conversational Operly AI and Studio.
    A binding is configuration, not authority: creation is approval-gated and its
    target must be currently available to the configuring actor; runtime invocation is
    independently re-authorized through CapabilityGateway -> CapabilityFirewall.
    """

    name = "operly_software_projects"
    capabilities = (
        CapabilityDefinition(
            "software.project.list",
            "software_project_list",
            "List canonical software projects across Studio website, managed-app, generated, and future runtimes in this workspace.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            {"type": "object"},
            risk_level="read_only",
            permissions=("solution:read",),
            approval_policy=ApprovalPolicy.AUTO,
            plugin_id="operly.software",
            category="software",
            tags=frozenset({"software", "studio", "project"}),
            semantic_operations=frozenset({"list software projects", "inspect studio projects"}),
        ),
        CapabilityDefinition(
            "software.project.create",
            "software_project_create",
            "Create a canonical draft SoftwareProject that can be edited from Operly AI or opened in Studio. This does not deploy or publish software.",
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "minLength": 1, "maxLength": 200},
                    "description": {"type": "string", "maxLength": 8000},
                },
                "required": ["name"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="low",
            permissions=("solution:generate",),
            approval_policy=ApprovalPolicy.AUTO,
            reversible=True,
            plugin_id="operly.software",
            category="software",
            tags=frozenset({"software", "studio", "project", "create"}),
            semantic_operations=frozenset({"create software project", "start app project", "start studio project"}),
        ),
        CapabilityDefinition(
            "software.project.inspect",
            "software_project_inspect",
            "Inspect one canonical software project including its current source/runtime and service-binding handles.",
            {
                "type": "object",
                "properties": {"project_id": {"type": "string"}},
                "required": ["project_id"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="read_only",
            permissions=("solution:read",),
            approval_policy=ApprovalPolicy.AUTO,
            plugin_id="operly.software",
            category="software",
            tags=frozenset({"software", "project", "inspect"}),
            semantic_operations=frozenset({"inspect software project", "get project runtime"}),
        ),
        CapabilityDefinition(
            "software.binding.list",
            "software_binding_list",
            "List active service bindings attached to one canonical software project.",
            {
                "type": "object",
                "properties": {"project_id": {"type": "string"}},
                "required": ["project_id"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="read_only",
            permissions=("solution:read",),
            approval_policy=ApprovalPolicy.AUTO,
            plugin_id="operly.software",
            category="software",
            tags=frozenset({"software", "service", "binding", "integration"}),
            semantic_operations=frozenset({"list project integrations", "inspect service bindings"}),
        ),
        CapabilityDefinition(
            "software.binding.create",
            "software_binding_create",
            "Attach one semantic project operation to a currently authorized/configured Operly capability. This stores no provider credential and does not invoke the target capability.",
            {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "semantic_name": {"type": "string"},
                    "capability_id": {"type": "string"},
                    "capability_version": {"type": "string"},
                    "binding_mode": {"type": "string", "enum": ["capability_gateway"]},
                    "principal_scope": {"type": "string", "enum": ["project_runtime"]},
                    "configuration": {"type": "object"},
                },
                "required": ["project_id", "semantic_name", "capability_id"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="medium",
            permissions=("solution:generate",),
            approval_policy=ApprovalPolicy.ALWAYS,
            reversible=True,
            plugin_id="operly.software",
            category="software",
            tags=frozenset({"software", "service", "binding", "integration", "configure"}),
            semantic_operations=frozenset({"bind service to project", "configure project integration"}),
        ),
        CapabilityDefinition(
            "software.binding.revoke",
            "software_binding_revoke",
            "Revoke one project service binding without changing or deleting the underlying provider connection.",
            {
                "type": "object",
                "properties": {"binding_id": {"type": "string"}},
                "required": ["binding_id"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="medium",
            permissions=("solution:generate",),
            approval_policy=ApprovalPolicy.ALWAYS,
            reversible=False,
            plugin_id="operly.software",
            category="software",
            tags=frozenset({"software", "service", "binding", "revoke"}),
            semantic_operations=frozenset({"remove project integration", "revoke service binding"}),
        ),
    )

    def __init__(self) -> None:
        self.projects = SoftwareProjectService()
        self.bindings = ServiceBindingStore()

    async def execute(self, context, capability_name, arguments):
        if capability_name == "software.project.list":
            projects = await self.projects.list(context.db, context.tenant_id)
            return CapabilityResult(True, False, {"projects": [_project_json(project) for project in projects]})

        if capability_name == "software.project.create":
            if not context.actor_id:
                return CapabilityResult(False, False, {"reason": "authenticated_actor_required"})
            try:
                project = await self.projects.create(
                    context.db,
                    workspace_id=str(context.tenant_id or ""),
                    user_id=context.actor_id,
                    name=str(arguments.get("name") or ""),
                    description=str(arguments.get("description") or ""),
                    metadata={"created_surface": "agent_runtime"},
                )
            except ValueError as error:
                return CapabilityResult(False, False, {"reason": str(error)})
            return CapabilityResult(True, True, {"project": _project_json(project)}, project.id)

        if capability_name == "software.project.inspect":
            try:
                project = await self.projects.get(context.db, context.tenant_id, str(arguments["project_id"]))
            except LookupError as error:
                return CapabilityResult(False, False, {"reason": str(error)})
            return CapabilityResult(True, False, {"project": _project_json(project)}, project.id)

        if capability_name == "software.binding.list":
            try:
                project = await self.projects.get(context.db, context.tenant_id, str(arguments["project_id"]))
                bindings = await self.bindings.list(
                    context.db,
                    workspace_id=context.tenant_id,
                    project_id=project.id,
                )
            except LookupError as error:
                return CapabilityResult(False, False, {"reason": str(error)})
            return CapabilityResult(
                True,
                False,
                {"project_id": project.id, "bindings": [_binding_json(item) for item in bindings]},
            )

        if capability_name == "software.binding.create":
            # Availability uses the canonical harness, but import it only at runtime
            # so provider registration itself remains acyclic.
            from packages.capabilities.agent_harness import PluginAgentHarness

            target = str(arguments.get("capability_id") or "").strip()
            availability = await PluginAgentHarness().availability(
                target,
                _plugin_context(context, f"Authorize project binding to {target}"),
            )
            if not bool(availability.get("available")):
                return CapabilityResult(
                    False,
                    False,
                    {
                        "reason": "binding_target_unavailable",
                        "capability_id": target,
                        "availability": availability,
                    },
                )
            try:
                project = await self.projects.get(context.db, context.tenant_id, str(arguments["project_id"]))
                binding = await self.bindings.create(
                    context.db,
                    workspace_id=context.tenant_id,
                    project_id=project.id,
                    user_id=context.actor_id or "OPERLY",
                    semantic_name=str(arguments["semantic_name"]),
                    capability_id=target,
                    capability_version=str(arguments.get("capability_version") or "1.0.0"),
                    binding_mode=str(arguments.get("binding_mode") or "capability_gateway"),
                    principal_scope=str(arguments.get("principal_scope") or "project_runtime"),
                    configuration=arguments.get("configuration") or {},
                )
            except (LookupError, ValueError, PermissionError) as error:
                return CapabilityResult(False, False, {"reason": str(error)})
            return CapabilityResult(
                True,
                True,
                {"binding": _binding_json(binding), "target_invoked": False, "target_availability": availability},
                binding.id,
            )

        if capability_name == "software.binding.revoke":
            try:
                binding = await self.bindings.get(
                    context.db,
                    workspace_id=context.tenant_id,
                    binding_id=str(arguments["binding_id"]),
                )
                await self.bindings.revoke(
                    context.db,
                    workspace_id=context.tenant_id,
                    binding_id=binding.id,
                )
            except LookupError as error:
                return CapabilityResult(False, False, {"reason": str(error)})
            return CapabilityResult(True, True, {"binding_id": binding.id, "revoked": True}, binding.id)

        return CapabilityResult(False, False, {"reason": "unsupported_software_capability"})

    async def verify(self, context, capability_name, arguments, result):
        if not result.success:
            return CapabilityResult(False, result.changed, result.evidence, result.external_reference)
        if capability_name in {"software.project.list", "software.project.inspect", "software.binding.list"}:
            return CapabilityResult(True, False, {"observed": True, **result.evidence}, result.external_reference)
        if capability_name == "software.project.create":
            try:
                project = await self.projects.get(
                    context.db,
                    str(context.tenant_id or ""),
                    str(result.external_reference or ""),
                )
            except LookupError:
                return CapabilityResult(False, result.changed, {"persisted": False})
            return CapabilityResult(
                True,
                result.changed,
                {"persisted": True, "project_id": project.id, **result.evidence},
                project.id,
            )
        if capability_name == "software.binding.create":
            try:
                binding = await self.bindings.get(
                    context.db,
                    workspace_id=context.tenant_id,
                    binding_id=str(result.external_reference or ""),
                )
            except LookupError:
                return CapabilityResult(False, result.changed, {"persisted": False})
            return CapabilityResult(
                True,
                result.changed,
                {"persisted": True, "binding_id": binding.id, **result.evidence},
                binding.id,
            )
        return CapabilityResult(True, result.changed, {"revoked": True, **result.evidence}, result.external_reference)
