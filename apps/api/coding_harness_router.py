import json
import mimetypes
import os
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext, get_auth_context, get_db
from packages.coding_harness.build_service import SourceRecordError
from packages.coding_harness.engine import build_harness_plan_with_model
from packages.coding_harness.execution_loop import build_with_repair
from packages.coding_harness.model_resolution import CapabilityResolutionError
from packages.coding_harness.opencode_agent import CodingAgentNeedsUserInput, CodingHarnessError
from packages.coding_harness.source_service import edit_source_for_plan, generate_source_for_plan, latest_source, source_record_json
from packages.coding_harness.source_jobs import job_json, launch_source_job
from packages.custom_software.source_bundles import BundlePolicyError, normalized_path
from packages.custom_software.live_planning import PlannerUnavailable, PlanningBlocked
from packages.custom_software.plan_service import (
    PlanConflict,
    continue_after_clarification,
    owned_plan,
    pending_clarification,
    plan_json,
    plan_version,
)
from packages.custom_software.planning_orchestrator import PlanningNeedsUserInput
from packages.custom_software.runner_adapters import ExternalRunnerAdapter, LocalSubprocessTestRunner
from packages.custom_software.runner_service import build_json
from packages.custom_software.schema import AgenticProjectInput, GenerateApprovedPlanInput, RunnerBuildInput
from packages.custom_software.sandbox import SandboxFailure, SandboxUnavailable
from packages.database.custom_software_models import GeneratedSourceBundle, RunnerPreviewRecord, SandboxGenerationJob, SandboxJobEvent
from packages.model_runtime import OllamaError

router = APIRouter(tags=["coding-harness"])


class HarnessSourceEditInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    planId: str
    approvedVersion: int = Field(ge=1)
    instruction: str = Field(min_length=1, max_length=20_000)
    mode: Literal["visual", "frontend", "backend", "source"] = "source"
    context: dict[str, Any] = Field(default_factory=dict)


class PlanningClarificationAnswerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str = Field(min_length=1, max_length=4000)


@router.post("/api/coding-harness/plans")
async def create_harness_plan(payload: AgenticProjectInput, auth: AuthContext = Depends(get_auth_context)):
    """Lower intent to reviewable IRs using the configured coding model provider."""
    try:
        result = await build_harness_plan_with_model(payload.prompt)
    except CapabilityResolutionError as error:
        raise HTTPException(status_code=502, detail={"code": "capability_resolution_failed", "message": str(error)}) from error
    except OllamaError as error:
        raise HTTPException(status_code=503, detail=error.public_message) from error
    result["tenantId"] = auth.tenant.id
    result["createdBy"] = auth.user.id
    return result


def _assert_owner(auth: AuthContext) -> None:
    if auth.role != "owner":
        raise HTTPException(403, "Only owners can run the coding harness")


def _coding_clarification(error: CodingAgentNeedsUserInput) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "coding_agent_clarification_required",
            "message": error.question,
            "question": error.question,
            "options": error.options,
        },
    )


def _configured_runner_adapter():
    """Select the test subprocess runner only when explicitly enabled.

    LocalSubprocessTestRunner independently enforces OPERLY_ENV in
    {development,test}, so production never falls back to process-only isolation.
    """
    if os.getenv("OPERLY_ENABLE_TEST_SUBPROCESS_RUNNER", "").strip() == "1":
        return LocalSubprocessTestRunner()
    return ExternalRunnerAdapter()


@router.get("/api/coding-harness/planning-clarification")
async def get_planning_clarification(auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    """Return the owner's most recent planning question that is waiting for an answer."""
    _assert_owner(auth)
    item = await pending_clarification(db, auth.tenant.id, auth.user.id)
    if item is None:
        raise HTTPException(404, "No planning clarification is waiting for an answer")
    return item


@router.post("/api/coding-harness/plans/{plan_id}/clarification")
async def answer_planning_clarification(plan_id: str, payload: PlanningClarificationAnswerInput, auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    """Resume the same SoftwarePlan after the owner answers a material planning question."""
    _assert_owner(auth)
    try:
        row = await owned_plan(db, auth.tenant.id, plan_id)
        row, version, plan = await continue_after_clarification(db, row, auth.user.id, payload.answer)
        return plan_json(row, version, plan)
    except PlanningNeedsUserInput:
        item = await pending_clarification(db, auth.tenant.id, auth.user.id)
        if item is None:
            raise HTTPException(422, detail={"code": "clarification_state_missing", "message": "Planning requested another clarification but no pending state was found"})
        return item
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except PlanConflict as error:
        raise HTTPException(409, str(error)) from error
    except PlannerUnavailable as error:
        raise HTTPException(503, detail={"code": "planner_unavailable", "message": str(error)}) from error
    except PlanningBlocked as error:
        raise HTTPException(422, detail={"code": "planning_blocked", "message": str(error)}) from error


@router.get("/api/coding-harness/runner-capabilities")
async def runner_capabilities(auth: AuthContext = Depends(get_auth_context)):
    """Report what the configured isolated runner actually advertises."""
    _assert_owner(auth)
    try:
        adapter = _configured_runner_adapter()
        capabilities = await adapter.capabilities()
    except SandboxUnavailable as error:
        raise HTTPException(status_code=503, detail={"code": "runner_unavailable", "message": str(error)}) from error
    except SandboxFailure as error:
        raise HTTPException(status_code=502, detail={"code": "runner_capability_probe_failed", "message": str(error)}) from error
    return {
        "runnerImplementation": adapter.implementation,
        "isolationProfile": adapter.isolation_profile,
        "capabilities": capabilities or {"protocolVersion": None, "profiles": {}},
    }


async def _approved_plan(db: AsyncSession, auth: AuthContext, plan_id: str, approved_version: int):
    try:
        row = await owned_plan(db, auth.tenant.id, plan_id)
        _, plan = await plan_version(db, row, approved_version)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    if row.status != "approved" or row.approved_version != approved_version:
        raise HTTPException(409, "Coding requires the explicitly approved current plan version")
    return row, plan


@router.post("/api/coding-harness/plans/{plan_id}/source")
async def create_harness_source(plan_id: str, payload: GenerateApprovedPlanInput, auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    """Author a real source tree from an approved plan without executing it in OPERLY."""
    _assert_owner(auth)
    if payload.planId != plan_id:
        raise HTTPException(422, "Plan identifier mismatch")
    row, plan = await _approved_plan(db, auth, plan_id, payload.approvedVersion)
    try:
        source, _ = await generate_source_for_plan(db, auth.tenant.id, auth.user.id, row, plan)
        await db.commit(); await db.refresh(source)
        return source_record_json(source)
    except CodingAgentNeedsUserInput as error:
        await db.rollback(); raise _coding_clarification(error)
    except OllamaError as error:
        await db.rollback(); raise HTTPException(status_code=503, detail=error.public_message) from error
    except CodingHarnessError as error:
        await db.rollback(); raise HTTPException(status_code=422, detail={"code": "coding_harness_blocked", "message": str(error)}) from error


@router.post("/api/coding-harness/plans/{plan_id}/source-jobs", status_code=202)
async def create_source_job(plan_id: str, payload: GenerateApprovedPlanInput, auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    """Queue source generation so the browser request is never held open by the model loop."""
    _assert_owner(auth)
    if payload.planId != plan_id:
        raise HTTPException(422, "Plan identifier mismatch")
    row, _ = await _approved_plan(db, auth, plan_id, payload.approvedVersion)
    resource = json.dumps({"kind": "coding_source", "version": 1})
    active = await db.scalar(
        select(SandboxGenerationJob).where(
            SandboxGenerationJob.tenant_id == auth.tenant.id,
            SandboxGenerationJob.plan_id == row.id,
            SandboxGenerationJob.approved_plan_version == payload.approvedVersion,
            SandboxGenerationJob.resource_json == resource,
            SandboxGenerationJob.state.in_(["queued", "generating"]),
        ).order_by(SandboxGenerationJob.created_at.desc())
    )
    if active:
        return job_json(active)
    job = SandboxGenerationJob(
        tenant_id=auth.tenant.id,
        plan_id=row.id,
        approved_plan_version=payload.approvedVersion,
        state="queued",
        attempts=1,
        resource_json=resource,
        created_by=auth.user.id,
    )
    db.add(job)
    await db.flush()
    db.add(SandboxJobEvent(tenant_id=auth.tenant.id, job_id=job.id, state="queued", details_json=json.dumps({"message": "Source generation queued"})))
    await db.commit()
    await db.refresh(job)
    launch_source_job(job.id)
    return job_json(job)


@router.get("/api/coding-harness/source-jobs/{job_id}")
async def get_source_job(job_id: str, auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    job = await db.get(SandboxGenerationJob, job_id)
    if job is None or job.tenant_id != auth.tenant.id or json.loads(job.resource_json or "{}").get("kind") != "coding_source":
        raise HTTPException(404, "Source generation job not found")
    return job_json(job)


@router.get("/api/coding-harness/sources/{source_id}/preview/{path:path}")
async def preview_static_source(source_id: str, path: str, auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    """Serve inert static-web bundle files directly; no generated process executes here."""
    source = await db.get(GeneratedSourceBundle, source_id)
    if source is None or source.tenant_id != auth.tenant.id:
        raise HTTPException(404, "Generated source not found")
    provenance = json.loads(source.provenance_json or "{}")
    if provenance.get("detectedRuntimeProfile") != "static-web-js":
        raise HTTPException(409, "This source requires Build & verify before it can be previewed")
    requested = path or "index.html"
    try:
        requested = normalized_path(requested)
    except BundlePolicyError as error:
        raise HTTPException(404, "Preview file not found") from error
    records = {str(item["path"]): str(item["content"]) for item in json.loads(source.files_json)}
    if requested not in records:
        raise HTTPException(404, "Preview file not found")
    media_type = mimetypes.guess_type(requested)[0] or "application/octet-stream"
    return Response(records[requested], media_type=media_type)


@router.get("/api/coding-harness/plans/{plan_id}/source")
async def get_harness_source(plan_id: str, auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    try:
        row = await owned_plan(db, auth.tenant.id, plan_id)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    source = await latest_source(db, auth.tenant.id, plan_id, row.approved_version or row.current_version)
    if source is None:
        raise HTTPException(404, "No coding-harness source exists for this plan version")
    return source_record_json(source)


@router.post("/api/coding-harness/plans/{plan_id}/source/edits")
async def edit_harness_source(plan_id: str, payload: HarnessSourceEditInput, auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    """Apply visual, frontend, backend, or general source edits through the same coding agent."""
    _assert_owner(auth)
    if payload.planId != plan_id:
        raise HTTPException(422, "Plan identifier mismatch")
    row, plan = await _approved_plan(db, auth, plan_id, payload.approvedVersion)
    source = await latest_source(db, auth.tenant.id, plan_id, payload.approvedVersion)
    if source is None:
        raise HTTPException(404, "Generate source before editing it")
    try:
        editor_context = {**payload.context, "editMode": payload.mode}
        updated, _ = await edit_source_for_plan(
            db,
            auth.tenant.id,
            auth.user.id,
            row,
            plan,
            source,
            payload.instruction,
            edit_kind=f"{payload.mode}_edit",
            context=editor_context,
        )
        await db.commit(); await db.refresh(updated)
        return source_record_json(updated)
    except CodingAgentNeedsUserInput as error:
        await db.rollback(); raise _coding_clarification(error)
    except OllamaError as error:
        await db.rollback(); raise HTTPException(status_code=503, detail=error.public_message) from error
    except CodingHarnessError as error:
        await db.rollback(); raise HTTPException(status_code=422, detail={"code": "coding_harness_edit_blocked", "message": str(error)}) from error


@router.post("/api/coding-harness/builds", status_code=202)
async def create_harness_build(payload: RunnerBuildInput, auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    """Run build/test/health gates and bounded source repair only in the configured runner."""
    _assert_owner(auth)
    row, plan = await _approved_plan(db, auth, payload.planId, payload.approvedVersion)
    try:
        adapter = _configured_runner_adapter()
        build, source, repairs = await build_with_repair(
            db,
            auth.tenant.id,
            auth.user.id,
            row,
            plan,
            payload.idempotencyKey,
            adapter=adapter,
        )
    except SandboxUnavailable as error:
        raise HTTPException(status_code=503, detail={"code": "runner_unavailable", "message": str(error)}) from error
    except SourceRecordError as error:
        raise HTTPException(409, str(error)) from error
    except CodingAgentNeedsUserInput as error:
        await db.rollback(); raise _coding_clarification(error)
    except OllamaError as error:
        await db.rollback(); raise HTTPException(status_code=503, detail=error.public_message) from error
    except CodingHarnessError as error:
        await db.rollback(); raise HTTPException(status_code=422, detail={"code": "coding_harness_repair_blocked", "message": str(error)}) from error

    result = build_json(build)
    if build.state == "preview_ready":
        preview = await db.scalar(select(RunnerPreviewRecord).where(RunnerPreviewRecord.build_id == build.id, RunnerPreviewRecord.tenant_id == auth.tenant.id, RunnerPreviewRecord.state == "active"))
        if preview:
            result["preview"] = {"id": preview.id, "url": f"/api/custom-software/previews/{preview.id}/", "expiresAt": preview.expires_at.isoformat()}
    result["source"] = source_record_json(source)
    result["repairAttempts"] = repairs
    result["repairCount"] = len(repairs)
    return result
