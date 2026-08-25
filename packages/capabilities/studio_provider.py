from sqlalchemy import desc, select

from packages.capabilities.contracts import ApprovalPolicy, CapabilityDefinition, CapabilityResult
from packages.capabilities.providers import BaseProvider
from packages.capabilities.software_build_provider import SoftwareBuildProvider
from packages.database.product_models import SolutionRecord
from packages.database.studio_models import StudioDeployment, StudioProject, StudioVersion
from packages.database.studio_source_models import StudioSourceVersion
from packages.solutions.production import ProductionService
from packages.solutions.service import RuntimeType, SolutionService
from packages.studio.service import StudioService


class StudioProvider(BaseProvider):
    """Compatibility capability surface over the unified software runtime.

    The public Studio capability IDs remain stable during migration, but coding is
    owned exclusively by ``software.build``/``software.edit`` and AgentRuntime.
    Legacy Studio rows remain adapters for reads/publication until their follow-up
    schema retirement; they are never an alternate coding-agent execution path.
    """

    name = "operly_studio"
    capabilities = (
        CapabilityDefinition(
            "studio.list_projects",
            "studio_list_projects",
            "List Studio projects for the current workspace.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            {"type": "object"},
            risk_level="read_only",
            permissions=("website:read",),
            approval_policy=ApprovalPolicy.AUTO,
            plugin_id="operly.studio",
            category="software",
            tags=frozenset({"studio", "project", "website"}),
            semantic_operations=frozenset({"list studio projects", "inspect websites"}),
        ),
        CapabilityDefinition(
            "studio.create_project",
            "studio_create_project",
            "Create an unpublished source-first Studio website project.",
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
            plugin_id="operly.studio",
            category="software",
            tags=frozenset({"studio", "project", "website", "create"}),
            semantic_operations=frozenset({"create website project", "create studio project"}),
        ),
        CapabilityDefinition(
            "studio.generate_site",
            "studio_generate_site",
            "Compatibility alias that builds or edits a Studio project through the canonical software runtime. This never publishes it.",
            {
                "type": "object",
                "properties": {"project_id": {"type": "string"}, "request": {"type": "string"}},
                "required": ["project_id", "request"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="medium",
            permissions=("website:write",),
            approval_policy=ApprovalPolicy.AUTO,
            reversible=True,
            plugin_id="operly.studio",
            category="software",
            tags=frozenset({"studio", "source", "website", "coding"}),
            semantic_operations=frozenset({"generate website", "edit website", "change website source"}),
        ),
        CapabilityDefinition(
            "studio.list_versions",
            "studio_list_versions",
            "List source-first and legacy versions for a Studio project.",
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
            plugin_id="operly.studio",
            category="software",
            tags=frozenset({"studio", "source", "versions"}),
            semantic_operations=frozenset({"list website versions", "inspect source history"}),
        ),
        CapabilityDefinition(
            "studio.publish_version",
            "studio_publish_version",
            "Publish a selected source or legacy Studio version after owner approval.",
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
            plugin_id="operly.studio",
            category="deployment",
            tags=frozenset({"studio", "publish", "deploy"}),
            semantic_operations=frozenset({"publish website", "deploy website"}),
        ),
        CapabilityDefinition(
            "studio.public_url",
            "studio_public_url",
            "Read the active production URL for a Studio project.",
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
            plugin_id="operly.studio",
            category="deployment",
            tags=frozenset({"studio", "website", "url"}),
            semantic_operations=frozenset({"get website url", "inspect deployment"}),
        ),
    )

    def __init__(self):
        self.service = StudioService()
        self.solutions = SolutionService()
        self.software = SoftwareBuildProvider()

    async def _solution_for_project(self, db, tenant_id: str, project_id: str):
        await self.solutions.sync(db, tenant_id)
        return await db.scalar(
            select(SolutionRecord).where(
                SolutionRecord.tenant_id == tenant_id,
                SolutionRecord.runtime_type == RuntimeType.STUDIO,
                SolutionRecord.runtime_reference == project_id,
            )
        )

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
                    {
                        "projects": [
                            {"id": row.id, "name": row.name, "status": row.status}
                            for row in rows
                        ]
                    },
                )

            if capability_name == "studio.create_project":
                row = await self.service.create_project(
                    context.db,
                    context.tenant_id,
                    context.actor_id or "OPERLY",
                    str(arguments["name"])[:200],
                    str(arguments.get("description") or "")[:4000],
                )
                # Make the canonical Solution/SoftwareProject facade aware of it in
                # the same transaction; legacy records remain the persistence owner.
                await self.solutions.sync(context.db, context.tenant_id)
                return CapabilityResult(
                    True,
                    True,
                    {"project_id": row.id, "name": row.name, "published": False},
                    row.id,
                )

            if capability_name == "studio.generate_site":
                legacy_project = await self.service.project(
                    context.db,
                    context.tenant_id,
                    str(arguments["project_id"]),
                )
                # Resolving through SoftwareProjectService syncs the legacy Studio
                # facade and keeps tenant ownership authoritative on the backend.
                project = await self.software.projects.get(
                    context.db,
                    context.tenant_id,
                    legacy_project.id,
                )
                request = " ".join(
                    str(arguments.get("request") or "").replace("\x00", "").split()
                ).strip()[:12000]
                if not request:
                    return CapabilityResult(False, False, {"reason": "request_required"})
                current = await self.software.sources.latest(
                    context.db,
                    context.tenant_id,
                    project.id,
                )
                if current is None:
                    delegated_name = "software.build"
                    delegated_arguments = {
                        "project_id": project.id,
                        "objective": request,
                        "name": project.name,
                        "return_source_archive": True,
                    }
                    action = "build_queued"
                else:
                    delegated_name = "software.edit"
                    delegated_arguments = {
                        "project_id": project.id,
                        "instruction": request,
                        "studio_context": {
                            "source_version_id": current.id,
                        },
                    }
                    action = "edited"
                result = await self.software.execute(
                    context,
                    delegated_name,
                    delegated_arguments,
                )
                if not result.success:
                    return result
                result = await self.software.verify(
                    context,
                    delegated_name,
                    delegated_arguments,
                    result,
                )
                if not result.success:
                    return result
                return CapabilityResult(
                    True,
                    result.changed,
                    {
                        **result.evidence,
                        "project_id": project.id,
                        "legacy_studio_project_id": legacy_project.id,
                        "action": action,
                        "compatibility_alias": "studio.generate_site",
                        "canonical_runtime": True,
                        "published": False,
                    },
                    result.external_reference,
                )

            if capability_name == "studio.list_versions":
                project = await self.service.project(
                    context.db,
                    context.tenant_id,
                    str(arguments["project_id"]),
                )
                sources = (
                    await context.db.scalars(
                        select(StudioSourceVersion)
                        .where(
                            StudioSourceVersion.tenant_id == context.tenant_id,
                            StudioSourceVersion.project_id == project.id,
                        )
                        .order_by(desc(StudioSourceVersion.source_version))
                    )
                ).all()
                legacy = (
                    await context.db.scalars(
                        select(StudioVersion)
                        .where(
                            StudioVersion.tenant_id == context.tenant_id,
                            StudioVersion.project_id == project.id,
                        )
                        .order_by(desc(StudioVersion.version_number))
                    )
                ).all()
                versions = [
                    {
                        "id": row.id,
                        "number": row.source_version,
                        "kind": "source",
                        "status": row.status,
                        "summary": row.change_summary,
                    }
                    for row in sources
                ]
                versions.extend(
                    {
                        "id": row.id,
                        "number": row.version_number,
                        "kind": "legacy_schema",
                        "status": row.status,
                        "summary": row.change_summary,
                    }
                    for row in legacy
                )
                return CapabilityResult(True, False, {"versions": versions})

            if capability_name == "studio.publish_version":
                project = await self.service.project(
                    context.db,
                    context.tenant_id,
                    str(arguments["project_id"]),
                )
                # Publication still supports legacy Studio versions as an adapter,
                # but it no longer imports or calls the retired Studio source agent.
                latest = await context.db.scalar(
                    select(StudioSourceVersion)
                    .where(
                        StudioSourceVersion.tenant_id == context.tenant_id,
                        StudioSourceVersion.project_id == project.id,
                    )
                    .order_by(desc(StudioSourceVersion.source_version))
                    .limit(1)
                )
                version_id = str(
                    arguments.get("version_id")
                    or (latest.id if latest is not None else None)
                    or project.active_draft_version_id
                    or ""
                )
                if not version_id:
                    return CapabilityResult(
                        False,
                        False,
                        {"reason": "No draft version is available to publish"},
                    )
                solution = await self._solution_for_project(
                    context.db,
                    context.tenant_id,
                    project.id,
                )
                if solution is None:
                    return CapabilityResult(
                        False,
                        False,
                        {"reason": "Studio solution record is unavailable"},
                    )
                job, published = await ProductionService(self.solutions).publish(
                    context.db,
                    context.tenant_id,
                    solution.id,
                    context.actor_id or "OPERLY",
                    version_reference=version_id,
                )
                success = str(job.status) == "succeeded"
                evidence = {
                    "project_id": project.id,
                    "solution_id": published.id,
                    "job_id": job.id,
                    "status": str(job.status),
                    "version_id": version_id,
                    "public_url": published.production_url,
                    "failure_classification": job.failure_classification,
                }
                return CapabilityResult(
                    success,
                    success,
                    evidence,
                    job.id,
                )

            if capability_name == "studio.public_url":
                project = await self.service.project(
                    context.db,
                    context.tenant_id,
                    str(arguments["project_id"]),
                )
                solution = await self._solution_for_project(
                    context.db,
                    context.tenant_id,
                    project.id,
                )
                if solution and solution.production_url:
                    return CapabilityResult(
                        True,
                        False,
                        {
                            "project_id": project.id,
                            "solution_id": solution.id,
                            "public_url": solution.production_url,
                        },
                    )
                # Legacy deployment fallback remains during migration.
                deployment = await context.db.scalar(
                    select(StudioDeployment).where(
                        StudioDeployment.tenant_id == context.tenant_id,
                        StudioDeployment.project_id == project.id,
                        StudioDeployment.status == "active",
                    )
                )
                return CapabilityResult(
                    True,
                    False,
                    {
                        "project_id": project.id,
                        "public_url": (
                            f"/sites/{deployment.public_slug}" if deployment else None
                        ),
                    },
                )
        except (LookupError, ValueError) as error:
            return CapabilityResult(False, False, {"reason": str(error)})
        return CapabilityResult(False, False, {"reason": "unsupported_studio_capability"})

    async def verify(self, context, capability_name, arguments, result):
        if not result.success:
            return CapabilityResult(False, result.changed, result.evidence, result.external_reference)
        if capability_name in {
            "studio.list_projects",
            "studio.list_versions",
            "studio.public_url",
        }:
            return CapabilityResult(
                True,
                False,
                {"observation_available": True, **result.evidence},
            )
        return CapabilityResult(
            True,
            result.changed,
            {"persisted": True, **result.evidence},
            result.external_reference,
        )
