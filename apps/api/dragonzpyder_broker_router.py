"""Read-only capability broker for DragonZpyder-owned agents.

Private service-to-service entrypoint. Operly authenticates the caller,
validates an expiring user-bound lease, re-resolves live authority and
rechecks every capability through the Kernel before execution.
No mutations or approvals are delegated in this first migration cut.
"""
from __future__ import annotations

import hmac
import json
import os

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import get_db
from packages.agent_runtime.remote_harness import validate_readonly_lease
from packages.kernel.contracts import CapabilityRisk, RuntimeRequest
from packages.kernel.runtime import RuntimeExecutionError
from packages.personal_modules.runtime import build_personal_runtime
from packages.security.execution_context import (
    ExecutionContextError, resolve_execution_context, resolve_personal_execution_context,
)
from packages.security.surfaces import SurfaceKind
from packages.workspace_modules.tools.runtime import build_workspace_runtime

router = APIRouter(prefix="/api/internal/agent-tools", tags=["agent-tools-internal"])


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(default="", max_length=200)


class ExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capability_id: str = Field(min_length=2, max_length=160)
    arguments: dict = Field(default_factory=dict)
    request_id: str = Field(min_length=1, max_length=160)


def _authorize(authorization: str, lease: str) -> dict:
    key = os.getenv("OPERLY_DRAGONZPYDER_BROKER_TOKEN", "")
    if (os.getenv("OPERLY_DRAGONZPYDER_AGENT_BROKER_ENABLED") != "1" or
            len(key) < 40 or not authorization.startswith("Bearer ") or
            not hmac.compare_digest(key, authorization[7:])):
        raise HTTPException(401, "Agent broker unavailable")
    try:
        return validate_readonly_lease(lease)
    except PermissionError as exc:
        raise HTTPException(403, "Agent delegation is not authorized") from exc


async def _runtime_and_context(db: AsyncSession, claims: dict):
    try:
        if claims["scope_kind"] == "personal":
            context = await resolve_personal_execution_context(
                db, user_id=claims["user_id"], channel="web",
                surface=SurfaceKind.PERSONAL_PRIVATE,
                conversation_id=claims.get("conversation_id"),
            )
            runtime = build_personal_runtime()
        else:
            context = await resolve_execution_context(
                db, workspace_id=claims["workspace_id"], user_id=claims["user_id"],
                channel="web", surface=SurfaceKind.WORKSPACE_PRIVATE,
                conversation_id=claims.get("conversation_id"), require_membership=True,
            )
            runtime = await build_workspace_runtime().request_runtime(db, context=context)
        return runtime, context
    except ExecutionContextError as exc:
        raise HTTPException(403, "Delegated user authority no longer exists") from exc


async def _allowed(db: AsyncSession, runtime, context, claims, query: str | None):
    if not claims["capability_ids"]:
        return ()
    candidates = await runtime.available_capabilities(
        db, context=context, query=query, limit=50,
    )
    allowed = frozenset(claims["capability_ids"])
    return tuple(spec for spec in candidates
                 if spec.id in allowed and spec.risk is CapabilityRisk.READ_ONLY
                 and context.scope_kind.value in spec.scopes)


@router.post("/search")
async def search_delegated_tools(
    payload: SearchRequest,
    authorization: str = Header(default=""),
    x_operly_agent_lease: str = Header(default=""),
    db: AsyncSession = Depends(get_db),
):
    claims = _authorize(authorization, x_operly_agent_lease)
    runtime, context = await _runtime_and_context(db, claims)
    candidates = await _allowed(db, runtime, context, claims, payload.query or None)
    return {"tools": [{
        "id": spec.id, "description": spec.description[:700],
        "display_name": spec.display_name,
        "input_schema": dict(spec.input_schema),
        "risk": "read_only",
    } for spec in candidates[:30]]}


@router.post("/execute")
async def execute_delegated_tool(
    payload: ExecuteRequest,
    authorization: str = Header(default=""),
    x_operly_agent_lease: str = Header(default=""),
    db: AsyncSession = Depends(get_db),
):
    claims = _authorize(authorization, x_operly_agent_lease)
    # Reject unchecked payloads before passing anything to a provider.
    if len(json.dumps(payload.arguments, separators=(",", ":"))) > 20_000:
        raise HTTPException(413, "Capability arguments too large")
    runtime, context = await _runtime_and_context(db, claims)
    candidates = await _allowed(db, runtime, context, claims, payload.capability_id)
    selected = next((x for x in candidates if x.id == payload.capability_id), None)
    if selected is None:
        raise HTTPException(403, "Capability is unavailable or outside delegated authority")
    try:
        response = await runtime.execute(
            db, context=context,
            request=RuntimeRequest(
                goal="Read-only delegated operation for DragonZpyder goal " + claims["run_id"][:100],
                capability_id=selected.id,
                arguments=payload.arguments,
                conversation_id=context.conversation_id,
                request_id=payload.request_id,
            ),
        )
        await db.commit()
    except RuntimeExecutionError as exc:
        await db.rollback()
        # Do not return provider exception text, arguments, credentials or trace.
        return {"ok": False, "error_code": exc.code}
    result = dict(response.result or {})
    encoded = json.dumps(result, default=str, ensure_ascii=False)
    if len(encoded.encode("utf-8")) > 24_000:
        return {"ok": False, "error_code": "result_too_large"}
    return {"ok": bool(response.done), "capability_id": selected.id,
            "result": result, "run_id": response.run_id}
