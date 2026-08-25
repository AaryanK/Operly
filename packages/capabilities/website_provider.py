"""Website semantics over canonical SoftwareProject source."""
from __future__ import annotations

import re

from packages.capabilities.contracts import ApprovalPolicy, CapabilityDefinition, CapabilityResult
from packages.capabilities.providers import BaseProvider
from packages.capabilities.software_build_provider import SoftwareBuildProvider
from packages.software_projects import SoftwareProjectService, SoftwareSourceService, files_from_row
from packages.solutions.service import SolutionService


_TITLE_RE = re.compile(r"(?is)<title\b[^>]*>(.*?)</title\s*>")


def _title(html: str) -> str | None:
    match = _TITLE_RE.search(str(html or ""))
    return re.sub(r"\s+", " ", match.group(1)).strip()[:200] if match else None


class UnifiedWebsiteProvider(BaseProvider):
    """Keep website-level intent while using SoftwareProject as the only backend."""

    name = "operly_website"
    capabilities = (
        CapabilityDefinition(
            "website.inspect",
            "website_inspect",
            "Inspect the current canonical source for a website-like SoftwareProject.",
            {
                "type": "object",
                "properties": {"solution_id": {"type": "string"}},
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="read_only",
            permissions=("website:read",),
            approval_policy=ApprovalPolicy.AUTO,
            plugin_id="operly.website",
            category="software",
            tags=frozenset({"website", "software", "source", "inspect"}),
            semantic_operations=frozenset({"inspect website", "read website source", "check current website"}),
        ),
        CapabilityDefinition(
            "website.edit",
            "website_edit",
            "Edit a website-like SoftwareProject through the canonical software.edit AgentRuntime path. This never publishes the change.",
            {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "minLength": 1, "maxLength": 200},
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
            plugin_id="operly.website",
            category="software",
            tags=frozenset({"website", "software", "edit", "source"}),
            semantic_operations=frozenset({"edit website", "change website title", "update website"}),
        ),
    )

    def __init__(self):
        self.solutions = SolutionService()
        self.projects = SoftwareProjectService()
        self.sources = SoftwareSourceService()
        self.software = SoftwareBuildProvider()

    async def _target(self, context, requested_id=None):
        if requested_id:
            solution, project_record = await self.solutions.resolve(
                context.db,
                context.tenant_id,
                str(requested_id),
            )
            project = await self.projects.get(context.db, context.tenant_id, project_record.id)
            source = await self.sources.latest(context.db, context.tenant_id, project.id)
            if source is None:
                raise LookupError("Website source is not available yet")
            return solution, project, source

        solutions = await self.solutions.list(context.db, context.tenant_id)
        for solution in solutions:
            project = await self.projects.get(context.db, context.tenant_id, solution.runtime_reference)
            source = await self.sources.latest(context.db, context.tenant_id, project.id)
            if source is None:
                continue
            files = files_from_row(source)
            if "index.html" in files or source.runtime_profile in {"static-web-js", "react-web", "next-fullstack"}:
                return solution, project, source
        raise LookupError("No website-like SoftwareProject exists yet")

    async def execute(self, context, capability_name, arguments):
        try:
            solution, project, source = await self._target(context, arguments.get("solution_id"))
            files = files_from_row(source)
            html = files.get("index.html", "")

            if capability_name == "website.inspect":
                return CapabilityResult(
                    True,
                    False,
                    {
                        "solution_id": solution.id,
                        "project_id": project.id,
                        "source_version_id": source.id,
                        "source_version": source.source_version,
                        "runtime_profile": source.runtime_profile,
                        "site_title": _title(html),
                        "files": sorted(files),
                        "published": solution.production_state == "live",
                    },
                    source.id,
                )

            if not context.actor_id:
                return CapabilityResult(False, False, {"reason": "authenticated_actor_required"})
            title = " ".join(str(arguments.get("title") or "").split()).strip()[:200]
            if not title:
                return CapabilityResult(False, False, {"reason": "title_required"})
            delegated = {
                "project_id": project.id,
                "instruction": f"Update the website title to {title!r}. Update the HTML document title, visible brand/title text where appropriate, and relevant SEO metadata without changing unrelated behavior.",
                "studio_context": {"source_version_id": source.id},
            }
            result = await self.software.execute(context, "software.edit", delegated)
            if not result.success:
                return result
            verified = await self.software.verify(context, "software.edit", delegated, result)
            if not verified.success:
                return verified
            return CapabilityResult(
                True,
                verified.changed,
                {
                    **verified.evidence,
                    "solution_id": solution.id,
                    "project_id": project.id,
                    "requested_title": title,
                    "published": False,
                    "canonical_runtime": True,
                },
                verified.external_reference,
            )
        except (LookupError, ValueError) as error:
            return CapabilityResult(False, False, {"reason": str(error)})

    async def verify(self, context, capability_name, arguments, result):
        if not result.success:
            return CapabilityResult(False, result.changed, result.evidence, result.external_reference)
        if capability_name == "website.inspect":
            return CapabilityResult(True, False, {"website_observed": True, **result.evidence}, result.external_reference)
        return result
