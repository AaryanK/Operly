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


class SoftwareProjectProvider(BaseProvider):
    """Model-visible facade over canonical SoftwareProject and ServiceBinding state.

    Binding writes deliberately retain the existing ``solution:generate`` authority
    and require approval. The dedicated authorization pass may later redefine that
    policy without changing these project/binding contracts.
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
            "Attach a semantic project operation to an installed Operly capability. This stores no provider credential and does not invoke the target capability.",
            {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "semantic_name": {"type": "string"},
                    "capability_id": {"type": "string"},
                    "binding_mode": {"type": "string"},
                    "principal_scope": {"type": "string"},
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

    def __init__(self, registry) -> None:
        self.projects = SoftwareProjectService()
        self.bindings = ServiceBindingStore(registry)

    async def execute(self, context, capability_name, arguments):
        if capability_name == "software.project.list":
            projects = await self.projects.list(context.db, context.tenant_id)
            return CapabilityResult(
                True,
                False,
                {"projects": [_project_json(project) for project in projects]},
            )

        if capability_name == "software.project.inspect":
            try:
                project = await self.projects.get(
                    context.db,
                    context.tenant_id,
                    str(arguments["project_id"]),
                )
            except LookupError as error:
                return CapabilityResult(False, False, {"reason": str(error)})
            return CapabilityResult(True, False, {"project": _project_json(project)}, project.id)

        if capability_name == "software.binding.list":
            try:
                project = await self.projects.get(
                    context.db,
                    context.tenant_id,
                    str(arguments["project_id"]),
                )
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
            try:
                project = await self.projects.get(
                    context.db,
                    context.tenant_id,
                    str(arguments["project_id"]),
                )
                binding = await self.bindings.create(
                    context.db,
                    workspace_id=context.tenant_id,
                    project_id=project.id,
                    user_id=context.actor_id or "OPERLY",
                    semantic_name=str(arguments["semantic_name"]),
                    capability_id=str(arguments["capability_id"]),
                    binding_mode=str(arguments.get("binding_mode") or "capability_gateway"),
                    principal_scope=str(arguments.get("principal_scope") or "project_runtime"),
                    configuration=arguments.get("configuration") or {},
                )
            except (LookupError, ValueError, PermissionError) as error:
                return CapabilityResult(False, False, {"reason": str(error)})
            return CapabilityResult(
                True,
                True,
                {"binding": _binding_json(binding), "target_invoked": False},
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
            return CapabilityResult(
                True,
                True,
                {"binding_id": binding.id, "revoked": True},
                binding.id,
            )

        return CapabilityResult(False, False, {"reason": "unsupported_software_capability"})

    async def verify(self, context, capability_name, arguments, result):
        if not result.success:
            return CapabilityResult(False, result.changed, result.evidence, result.external_reference)
        if capability_name in {
            "software.project.list",
            "software.project.inspect",
            "software.binding.list",
        }:
            return CapabilityResult(True, False, {"observed": True, **result.evidence}, result.external_reference)
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
