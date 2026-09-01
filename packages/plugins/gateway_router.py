from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import get_db
from packages.kernel.runtime import RuntimeExecutionError
from packages.plugins.budgets import ResourceBudgetExceeded
from packages.plugins.gateway import capability_gateway

router = APIRouter(prefix="/api/capability-gateway", tags=["capability-gateway"])


class CapabilityGatewayInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    arguments: dict[str, Any] = Field(default_factory=dict)
    goal: str = Field(default="Hosted digital workload invocation", max_length=4000)
    request_id: str | None = Field(default=None, max_length=160)
    approval_id: str | None = Field(default=None, max_length=80)


def _runtime_token(authorization: str | None) -> str:
    value = str(authorization or "").strip()
    if not value.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Runtime bearer identity required")
    token = value[7:].strip()
    if not token.startswith("opr_") or len(token) < 32:
        raise HTTPException(status_code=401, detail="Runtime bearer identity is invalid")
    return token


@router.post("/{binding_id}/invoke")
async def invoke_capability_binding(
    binding_id: str,
    payload: CapabilityGatewayInput,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    token = _runtime_token(authorization)
    try:
        result = await capability_gateway.invoke(
            db,
            runtime_token=token,
            binding_id=binding_id,
            arguments=payload.arguments,
            goal=payload.goal,
            request_id=payload.request_id,
            approval_id=payload.approval_id,
        )
        await db.commit()
        return {
            "status": result.status,
            "request_id": result.request_id,
            "run_id": result.run_id,
            "result": dict(result.result or {}),
        }
    except RuntimeExecutionError as error:
        # Preserve the exact request/run/approval lineage so a workload can pause and
        # resume the same invocation after a human decision instead of duplicating it.
        await db.commit()
        if error.code == "approval_required" and error.approval_id:
            return {
                "status": "waiting_for_approval",
                "request_id": payload.request_id,
                "run_id": error.run_id,
                "approval_id": error.approval_id,
                "message": "Human approval is required. Resume this exact request after approval; do not repeat the side effect.",
            }
        raise HTTPException(
            status_code=error.status_code,
            detail={
                "code": error.code,
                "message": str(error),
                "run_id": error.run_id,
                "approval_id": error.approval_id,
            },
        ) from error
    except ResourceBudgetExceeded as error:
        await db.rollback()
        raise HTTPException(status_code=429, detail=str(error)) from error
    except LookupError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except PermissionError as error:
        await db.rollback()
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
