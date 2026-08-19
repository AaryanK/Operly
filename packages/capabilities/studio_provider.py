from sqlalchemy import desc, select

from packages.capabilities.contracts import ApprovalPolicy, CapabilityDefinition, CapabilityResult
from packages.capabilities.providers import BaseProvider
from packages.database.studio_models import StudioDeployment, StudioProject, StudioVersion
from packages.studio.ai import StudioAI
from packages.studio.service import StudioService


class StudioProvider(BaseProvider):
    name = "operly_studio"
    capabilities = (
        CapabilityDefinition(
            "studio.list_projects",
            "studio_list_projects",
            "List website Studio projects for the current tenant.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            {"type": "object"},
            risk_level="read_only",
            permissions=("website:read",),
            approval_policy=ApprovalPolicy.AUTO,
        ),
        CapabilityDefinition(
            "studio.create_project",
            "studio_create_project",
            "Create an unpublished website Studio project.",
            {
                "type": "object",
                "properties": {"name": {"type": "string"}, "description": {"type": "string"}},
                "required": ["name"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="low",
            permissions=("website:write",),
            reversible=True,
        ),
        CapabilityDefinition(
            "studio.generate_site",
            "studio_generate_site",
            "Generate and save a validated website draft. This never publishes it.",
            {
                "type": "object",
                "properties": {"project_id": {"type": "string"}, "request": {"type": "string"}},
                "required": ["project_id", "request"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="medium",
            permissions=("website:write",),
            reversible=True,
        ),
        CapabilityDefinition(
            "studio.list_versions",
            "studio_list_versions",
            "List versions for a website Studio project.",
            {
                "type": "object",
                "properties": {"project_id": {"type": "string"}},
                "required": ["project_id"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="read_only",
            permissions=("website:read",),
            approval_policy=ApprovalPolicy.AUTO,
        ),
        CapabilityDefinition(
            "studio.publish_version",
            "studio_publish_version",
            "Publish a selected website Studio version after owner approval.",
            {
                "type": "object",
                "properties": {"project_id": {"type": "string"}, "version_id": {"type": "string"}},
                "required": ["project_id"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="high",
            permissions=("website:write",),
            approval_policy=ApprovalPolicy.ALWAYS,
            reversible=True,
        ),
        CapabilityDefinition(
            "studio.public_url",
            "studio_public_url",
            "Read the active public deployment slug for a website Studio project.",
            {
                "type": "object",
                "properties": {"project_id": {"type": "string"}},
                "required": ["project_id"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="read_only",
            permissions=("website:read",),
            approval_policy=ApprovalPolicy.AUTO,
        ),
    )

    def __init__(self):
        self.service = StudioService()

    async def execute(self, context, capability_name, arguments):
        try:
            if capability_name == "studio.list_projects":
                rows = (
                    await context.db.scalars(
                        select(StudioProject)
                        .where(
                            StudioProject.tenant_id == context.tenant_id,
                            StudioProject.status != "archived",
                        )
                        .order_by(desc(StudioProject.updated_at))
                    )
                ).all()
                return CapabilityResult(
                    True,
                    False,
                    {"projects": [{"id": row.id, "name": row.name, "status": row.status} for row in rows]},
                )

            if capability_name == "studio.create_project":
                row = await self.service.create_project(
                    context.db,
                    context.tenant_id,
                    context.actor_id or "OPERLY",
                    str(arguments["name"])[:200],
                    str(arguments.get("description") or "")[:4000],
                )
                return CapabilityResult(True, True, {"project_id": row.id, "name": row.name, "published": False}, row.id)

            if capability_name == "studio.generate_site":
                project = await self.service.project(context.db, context.tenant_id, str(arguments["project_id"]))
                schema = await StudioAI().generate(str(arguments["request"]))
                version = await self.service.save_schema(
                    context.db,
                    context.tenant_id,
                    project.id,
                    context.actor_id or "OPERLY",
                    schema.model_dump(mode="json"),
                    "AI agent generation",
                )
                return CapabilityResult(
                    True,
                    True,
                    {
                        "project_id": project.id,
                        "version_id": version.id,
                        "published": False,
                        "pages": [page.title for page in schema.pages],
                    },
                    version.id,
                )

            if capability_name == "studio.list_versions":
                project = await self.service.project(context.db, context.tenant_id, str(arguments["project_id"]))
                rows = (
                    await context.db.scalars(
                        select(StudioVersion)
                        .where(
                            StudioVersion.tenant_id == context.tenant_id,
                            StudioVersion.project_id == project.id,
                        )
                        .order_by(desc(StudioVersion.version_number))
                    )
                ).all()
                return CapabilityResult(
                    True,
                    False,
                    {"versions": [{"id": row.id, "number": row.version_number, "status": row.status} for row in rows]},
                )

            if capability_name == "studio.publish_version":
                project = await self.service.project(context.db, context.tenant_id, str(arguments["project_id"]))
                version_id = str(arguments.get("version_id") or project.active_draft_version_id or "")
                if not version_id:
                    return CapabilityResult(False, False, {"reason": "No draft version is available to publish"})
                deployment, url = await self.service.publish(
                    context.db,
                    context.tenant_id,
                    project.id,
                    version_id,
                    context.actor_id or "OPERLY",
                )
                return CapabilityResult(True, True, {"project_id": project.id, "deployment_id": deployment.id, "public_url": url}, deployment.id)

            if capability_name == "studio.public_url":
                project = await self.service.project(context.db, context.tenant_id, str(arguments["project_id"]))
                deployment = await context.db.scalar(
                    select(StudioDeployment).where(
                        StudioDeployment.tenant_id == context.tenant_id,
                        StudioDeployment.project_id == project.id,
                        StudioDeployment.status == "active",
                    )
                )
                return CapabilityResult(True, False, {"project_id": project.id, "public_slug": deployment.public_slug if deployment else None})
        except (LookupError, ValueError) as error:
            return CapabilityResult(False, False, {"reason": str(error)})
        return CapabilityResult(False, False, {"reason": "unsupported_studio_capability"})

    async def verify(self, context, capability_name, arguments, result):
        if not result.success:
            return CapabilityResult(False, result.changed, result.evidence, result.external_reference)
        if capability_name in {"studio.list_projects", "studio.list_versions", "studio.public_url"}:
            return CapabilityResult(True, False, {"observation_available": True, **result.evidence})
        return CapabilityResult(True, result.changed, {"persisted": True, **result.evidence}, result.external_reference)
