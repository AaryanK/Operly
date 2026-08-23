"""Intent-first Solution creation shared by UI and future agent surfaces."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from packages.application_builder.schema import BuilderContext, ProposalRequest
from packages.application_builder.service import ApplicationBuilderService, BuilderError
from packages.solutions.service import LifecycleStatus, RuntimeType, SolutionService, SolutionType
from packages.studio.service import StudioService


@dataclass(frozen=True, slots=True)
class SolutionIntent:
    solution_type: str
    runtime_type: str
    reason: str
    confidence: str

    def as_dict(self) -> dict[str, str]:
        return {
            "solutionType": self.solution_type,
            "runtimeType": self.runtime_type,
            "reason": self.reason,
            "confidence": self.confidence,
        }


_WEB_TERMS = {
    "website", "site", "landing", "homepage", "portfolio", "brochure", "marketing",
    "public", "presence", "seo", "pages", "webpage",
}
_APP_TERMS = {
    "app", "application", "attendance", "logger", "logging", "track", "tracking",
    "record", "records", "inventory", "dashboard", "crm", "workflow", "portal",
    "database", "checkin", "checkout", "check-in", "check-out", "arrival", "departure",
    "manage", "management", "internal", "system", "tool", "form", "save", "store",
}
_STATE_TERMS = {
    "save", "store", "record", "records", "logging", "track", "tracking", "inventory",
    "attendance", "database", "manage", "management", "checkin", "checkout", "arrival", "departure",
}


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9][a-z0-9-]+", str(value or "").lower()))


def classify_solution_intent(name: str, objective: str) -> SolutionIntent:
    """Choose the supported runtime before creating it.

    Digital Presence is deliberately opt-in through clear public-site intent. Any
    objective that describes records/state/workflows is routed to the managed app
    runtime so requests such as "Student Attendance Logger" cannot be forced into a
    static website merely because creation started from the Solutions page.
    """

    tokens = _tokens(f"{name} {objective}")
    web_score = len(tokens & _WEB_TERMS)
    app_score = len(tokens & _APP_TERMS)
    stateful = bool(tokens & _STATE_TERMS)

    if stateful or app_score > web_score:
        return SolutionIntent(
            SolutionType.BUSINESS_APP,
            RuntimeType.MANAGED_APP,
            "The request describes state, records, workflows, or application behavior.",
            "high" if stateful or app_score >= 2 else "medium",
        )
    if web_score:
        return SolutionIntent(
            SolutionType.DIGITAL_PRESENCE,
            RuntimeType.STUDIO,
            "The request explicitly describes a public website/digital-presence experience.",
            "high" if web_score >= 2 else "medium",
        )
    return SolutionIntent(
        SolutionType.BUSINESS_APP,
        RuntimeType.MANAGED_APP,
        "No explicit public-website requirement was supplied; preserve functional intent in the app runtime.",
        "low",
    )


async def create_solution_from_intent(
    db,
    *,
    tenant_id: str,
    user_id: str,
    name: str,
    objective: str,
    service: SolutionService | None = None,
):
    service = service or SolutionService()
    clean_name = " ".join(str(name or "").split()).strip()[:200]
    clean_objective = " ".join(str(objective or "").split()).strip()[:8000]
    if not clean_name:
        raise ValueError("Solution name is required")
    if not clean_objective:
        raise ValueError("Describe what this Solution should do")

    decision = classify_solution_intent(clean_name, clean_objective)
    context: dict[str, Any] = {
        "ownerIntent": {"name": clean_name, "objective": clean_objective},
        "creationIntent": {
            "name": clean_name,
            "objective": clean_objective,
            "classification": decision.as_dict(),
        },
        "contextAuthority": ["ownerIntent", "solution", "workspaceInherited"],
    }

    if decision.runtime_type == RuntimeType.STUDIO:
        project = await StudioService.create_project(
            db,
            tenant_id,
            user_id,
            clean_name,
            clean_objective,
        )
        context["source_engine"] = "studio_source_agent_v1"
        row = await service._record(
            db,
            tenant_id,
            RuntimeType.STUDIO,
            project.id,
            name=clean_name,
            description=clean_objective,
            solution_type=SolutionType.DIGITAL_PRESENCE,
            lifecycle_status=LifecycleStatus.DRAFT,
            current_version_reference=project.active_draft_version_id,
            preview_state="ready" if project.active_draft_version_id else "unavailable",
            preview_url="/api/solutions/{solution_id}/preview",
            production_state="offline",
            production_url=None,
            visibility="private",
            context_json=json.dumps(context, ensure_ascii=False, sort_keys=True),
        )
        return row, decision

    app, version = await ApplicationBuilderService.create(
        db,
        tenant_id,
        user_id,
        clean_name,
        clean_objective,
    )
    generation_error = None
    try:
        change = await ApplicationBuilderService.propose(
            db,
            tenant_id,
            user_id,
            "owner",
            ProposalRequest(
                message=clean_objective,
                context=BuilderContext(
                    workspaceId=tenant_id,
                    applicationId=app.id,
                    activeVersionId=version.id,
                    userRole="owner",
                    selectionScope="application",
                ),
            ),
        )
        version = await ApplicationBuilderService.apply(
            db,
            tenant_id,
            user_id,
            "owner",
            change.id,
        )
        context["initialGeneration"] = {
            "changeSetId": change.id,
            "versionId": version.id,
            "status": "applied",
        }
    except Exception as error:
        # Runtime classification and owner intent are still durable even when the
        # configured synthesis model is temporarily unavailable. Studio can retry
        # from the exact creation objective instead of silently changing product type.
        generation_error = str(error)[:1000]
        context["initialGeneration"] = {"status": "retryable", "error": generation_error}

    row = await service._record(
        db,
        tenant_id,
        RuntimeType.MANAGED_APP,
        app.id,
        name=clean_name,
        description=clean_objective,
        solution_type=SolutionType.BUSINESS_APP,
        lifecycle_status=LifecycleStatus.PREVIEW_READY if app.active_version_id else LifecycleStatus.DRAFT,
        current_version_reference=app.active_version_id,
        preview_state="ready" if app.active_version_id else "unavailable",
        preview_url="/api/solutions/{solution_id}/preview" if app.active_version_id else None,
        production_state="offline",
        production_url=None,
        visibility="private",
        context_json=json.dumps(context, ensure_ascii=False, sort_keys=True),
    )
    return row, decision
