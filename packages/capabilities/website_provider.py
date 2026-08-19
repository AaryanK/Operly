from sqlalchemy import select

from packages.capabilities.contracts import ApprovalPolicy, CapabilityDefinition, CapabilityResult
from packages.capabilities.providers import BaseProvider
from packages.database.studio_models import StudioVersion
from packages.solutions.service import RuntimeType, SolutionService, SolutionType
from packages.studio.schema import SiteSchema
from packages.studio.service import StudioService


class UnifiedWebsiteProvider(BaseProvider):
    """Website operations against the canonical Digital Presence solution."""

    name = "operly_website"
    capabilities = (
        CapabilityDefinition(
            "website.inspect",
            "website_inspect",
            "Inspect the current draft or published Digital Presence website.",
            {
                "type": "object",
                "properties": {"solution_id": {"type": "string"}},
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="read_only",
            permissions=("website:read",),
            approval_policy=ApprovalPolicy.AUTO,
        ),
        CapabilityDefinition(
            "website.edit",
            "website_edit",
            "Create a new draft website version with an updated site title. This never publishes the change.",
            {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "solution_id": {"type": "string"},
                },
                "required": ["title"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="medium",
            permissions=("website:write",),
            approval_policy=ApprovalPolicy.AUTO,
            reversible=True,
        ),
    )

    def __init__(self):
        self.solutions = SolutionService()
        self.studio = StudioService()

    async def _presence(self, context, requested_id=None):
        if requested_id:
            row, runtime = await self.solutions.resolve(
                context.db,
                context.tenant_id,
                str(requested_id),
            )
        else:
            rows = await self.solutions.list(context.db, context.tenant_id)
            row = next(
                (
                    item
                    for item in rows
                    if item.solution_type == SolutionType.DIGITAL_PRESENCE
                ),
                None,
            )
            if row is None:
                raise LookupError("No Digital Presence exists yet")
            row, runtime = await self.solutions.resolve(
                context.db,
                context.tenant_id,
                row.id,
            )

        if row.solution_type != SolutionType.DIGITAL_PRESENCE:
            raise ValueError("Selected solution is not a Digital Presence")
        if row.runtime_type != RuntimeType.STUDIO:
            raise ValueError("Digital Presence is not backed by the Studio runtime")
        return row, runtime

    async def _schema(self, context, project):
        version_id = project.active_draft_version_id or project.published_version_id
        if not version_id:
            raise LookupError("Website has no version to inspect")
        version = await self.studio.version(
            context.db,
            context.tenant_id,
            project.id,
            version_id,
        )
        return version, SiteSchema.model_validate_json(version.schema_json)

    async def execute(self, context, capability_name, arguments):
        try:
            solution, project = await self._presence(
                context,
                arguments.get("solution_id"),
            )
            current, schema = await self._schema(context, project)

            if capability_name == "website.inspect":
                return CapabilityResult(
                    True,
                    False,
                    {
                        "solution_id": solution.id,
                        "project_id": project.id,
                        "version_id": current.id,
                        "site_title": schema.site.title,
                        "description": schema.site.description,
                        "pages": [
                            {"id": page.id, "slug": page.slug, "title": page.title}
                            for page in schema.pages
                        ],
                        "theme": schema.theme.model_dump(mode="json"),
                    },
                    current.id,
                )

            if capability_name == "website.edit":
                if not context.actor_id:
                    return CapabilityResult(
                        False,
                        False,
                        {"reason": "authenticated_actor_required"},
                    )
                title = str(arguments["title"]).strip()[:120]
                if not title:
                    return CapabilityResult(False, False, {"reason": "title is required"})

                before = schema.site.title
                schema.site.title = title
                schema.site.seo.title = title
                for page in schema.pages:
                    if page.slug == "home":
                        page.seo.title = title
                    for section in page.sections:
                        if section.type == "navbar":
                            section.props.site_title = title
                        elif section.type == "footer":
                            section.props.business_name = title

                version = await self.studio.save_schema(
                    context.db,
                    context.tenant_id,
                    project.id,
                    context.actor_id,
                    schema.model_dump(mode="json"),
                    "AI draft: update website title",
                )
                return CapabilityResult(
                    True,
                    before != title,
                    {
                        "solution_id": solution.id,
                        "project_id": project.id,
                        "version_id": version.id,
                        "before_title": before,
                        "title": title,
                        "published": False,
                    },
                    version.id,
                )
        except (LookupError, ValueError) as error:
            return CapabilityResult(False, False, {"reason": str(error)})

        return CapabilityResult(False, False, {"reason": "unsupported_website_capability"})

    async def verify(self, context, capability_name, arguments, result):
        if not result.success:
            return CapabilityResult(False, result.changed, result.evidence, result.external_reference)
        if capability_name == "website.inspect":
            return CapabilityResult(True, False, {"website_observed": True, **result.evidence})
        if not result.external_reference:
            return CapabilityResult(False, result.changed, {"reason": "verification_target_missing"})

        version = await context.db.scalar(
            select(StudioVersion).where(
                StudioVersion.id == result.external_reference,
                StudioVersion.tenant_id == context.tenant_id,
            )
        )
        actual = SiteSchema.model_validate_json(version.schema_json).site.title if version else None
        expected = str(arguments["title"]).strip()[:120]
        return CapabilityResult(
            actual == expected,
            result.changed,
            {
                "version_id": result.external_reference,
                "expected_title": expected,
                "actual_title": actual,
            },
            result.external_reference,
        )
