from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext, get_auth_context, get_db
from packages.coding_harness.build_service import SourceRecordError
from packages.coding_harness.engine import build_harness_plan_with_model
from packages.coding_harness.execution_loop import build_with_repair
from packages.coding_harness.model_resolution import CapabilityResolutionError
from packages.coding_harness.opencode_agent import CodingHarnessError
from packages.coding_harness.source_service import edit_source_for_plan, generate_source_for_plan, latest_source, source_record_json
from packages.custom_software.plan_service import owned_plan, plan_version
from packages.custom_software.runner_service import build_json
from packages.custom_software.schema import AgenticProjectInput, GenerateApprovedPlanInput, RunnerBuildInput
from packages.database.custom_software_models import RunnerPreviewRecord
from packages.model_runtime import OllamaError

router = APIRouter(tags=["coding-harness"])


class HarnessSourceEditInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    planId: str
    approvedVersion: int = Field(ge=1)
    instruction: str = Field(min_length=1, max_length=20_000)
    mode: Literal["visual", "frontend", "backend", "source"] = "source"
    context: dict[str, Any] = Field(default_factory=dict)


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
    except OllamaError as error:
        await db.rollback(); raise HTTPException(status_code=503, detail=error.public_message) from error
    except CodingHarnessError as error:
        await db.rollback(); raise HTTPException(status_code=422, detail={"code": "coding_harness_blocked", "message": str(error)}) from error


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
        updated, _ = await edit_source_for_plan(
            db,
            auth.tenant.id,
            auth.user.id,
            row,
            plan,
            source,
            payload.instruction,
            edit_kind=f"{payload.mode}_edit",
            context=payload.context,
        )
        await db.commit(); await db.refresh(updated)
        return source_record_json(updated)
    except OllamaError as error:
        await db.rollback(); raise HTTPException(status_code=503, detail=error.public_message) from error
    except CodingHarnessError as error:
        await db.rollback(); raise HTTPException(status_code=422, detail={"code": "coding_harness_edit_blocked", "message": str(error)}) from error


@router.post("/api/coding-harness/builds", status_code=202)
async def create_harness_build(payload: RunnerBuildInput, auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    """Run build/test/health gates and bounded source repair only in the isolated runner."""
    _assert_owner(auth)
    row, plan = await _approved_plan(db, auth, payload.planId, payload.approvedVersion)
    try:
        build, source, repairs = await build_with_repair(
            db,
            auth.tenant.id,
            auth.user.id,
            row,
            plan,
            payload.idempotencyKey,
        )
    except SourceRecordError as error:
        raise HTTPException(409, str(error)) from error
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
