from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from apps.api.dependencies import AuthContext, get_auth_context
from packages.business_brain.operations_service import get_operations_service
from packages.business_brain.security import AgentSecurityError


router = APIRouter(prefix="/api/operations", tags=["operations"])


class ProfileInput(BaseModel):
    legal_name: str = Field(default="", max_length=250)
    trading_name: str = Field(default="", max_length=250)
    industry: str = Field(default="general", max_length=150)
    description: str = Field(default="", max_length=8_000)
    country: str = Field(default="", max_length=100)
    currency: str = Field(default="USD", max_length=20)
    timezone: str = Field(default="UTC", max_length=100)
    operating_hours: dict[str, Any] = Field(default_factory=dict)
    communication_tone: str = Field(default="professional", max_length=150)
    goals: list[str] = Field(default_factory=list, max_length=20)
    pain_points: list[str] = Field(default_factory=list, max_length=20)
    approval_rules: list[str] = Field(default_factory=list, max_length=20)


class SourceInput(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    source_type: str = Field(default="text", max_length=80)
    content: str = Field(min_length=1, max_length=50_000)


class PlanInput(BaseModel):
    goal: str = Field(default="", max_length=2_000)


class NodeUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=250)
    description: str | None = Field(default=None, max_length=4_000)
    approval_required: bool | None = None
    enabled: bool | None = None
    x: float | None = None
    y: float | None = None


@router.get("/profile")
async def profile(auth: AuthContext = Depends(get_auth_context)):
    return await get_operations_service().get_profile(auth.tenant.id)


@router.put("/profile")
async def save_profile(
    payload: ProfileInput,
    auth: AuthContext = Depends(get_auth_context),
):
    return await get_operations_service().save_profile(
        auth.tenant.id,
        payload.model_dump(),
    )


@router.get("/sources")
async def sources(auth: AuthContext = Depends(get_auth_context)):
    return await get_operations_service().list_sources(auth.tenant.id)


@router.post("/sources")
async def add_source(
    payload: SourceInput,
    auth: AuthContext = Depends(get_auth_context),
):
    return await get_operations_service().add_source(
        auth.tenant.id,
        payload.title,
        payload.source_type,
        payload.content,
    )


@router.get("/snapshot")
async def snapshot(auth: AuthContext = Depends(get_auth_context)):
    return await get_operations_service().snapshot(auth.tenant.id)


@router.get("/alerts")
async def alerts(auth: AuthContext = Depends(get_auth_context)):
    return await get_operations_service().list_alerts(auth.tenant.id)


@router.post("/scan")
async def scan(auth: AuthContext = Depends(get_auth_context)):
    return {
        "alerts": await get_operations_service().run_operational_scan(
            auth.tenant.id,
            auth.user.display_name,
        )
    }


@router.patch("/alerts/{alert_id}/resolve")
async def resolve_alert(
    alert_id: str,
    auth: AuthContext = Depends(get_auth_context),
):
    try:
        return await get_operations_service().resolve_alert(
            auth.tenant.id,
            alert_id,
        )
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/brief")
async def brief(auth: AuthContext = Depends(get_auth_context)):
    try:
        return await get_operations_service().operational_brief(
            auth.tenant.id,
            f"web-user:{auth.user.id}",
        )
    except AgentSecurityError as error:
        raise HTTPException(status_code=429, detail=str(error)) from error


@router.get("/audit/latest")
async def latest_audit(auth: AuthContext = Depends(get_auth_context)):
    return await get_operations_service().latest_audit(auth.tenant.id)


@router.post("/audit/run")
async def run_audit(auth: AuthContext = Depends(get_auth_context)):
    try:
        return await get_operations_service().run_audit(
            auth.tenant.id,
            f"web-user:{auth.user.id}",
        )
    except AgentSecurityError as error:
        raise HTTPException(status_code=429, detail=str(error)) from error


@router.get("/plans/latest")
async def latest_plan(auth: AuthContext = Depends(get_auth_context)):
    return await get_operations_service().latest_plan(auth.tenant.id)


@router.post("/plans/generate")
async def generate_plan(
    payload: PlanInput,
    auth: AuthContext = Depends(get_auth_context),
):
    try:
        return await get_operations_service().generate_plan(
            auth.tenant.id,
            f"web-user:{auth.user.id}",
            payload.goal,
        )
    except AgentSecurityError as error:
        raise HTTPException(status_code=429, detail=str(error)) from error


@router.patch("/plans/nodes/{node_id}")
async def update_node(
    node_id: str,
    payload: NodeUpdate,
    auth: AuthContext = Depends(get_auth_context),
):
    try:
        return await get_operations_service().update_node(
            auth.tenant.id,
            node_id,
            payload.model_dump(exclude_none=True),
        )
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/plans/{plan_id}/approve")
async def approve_plan(
    plan_id: str,
    auth: AuthContext = Depends(get_auth_context),
):
    try:
        return await get_operations_service().approve_plan(
            auth.tenant.id,
            plan_id,
        )
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
