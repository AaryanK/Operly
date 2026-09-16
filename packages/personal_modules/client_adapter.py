from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AccountAuthContext, get_account_auth_context, get_db
from packages.agent_runtime import AgentBudget, AgentRuntimeDisabled
from packages.agent_runtime.inference import AgentInferenceError
from packages.agent_runtime.planning import AgentPlanningError
from packages.agent_runtime.store import (
    AgentRunStateError,
    queue_after_approval,
    request_cancellation,
)
from packages.database.agent_chat_models import AgentChatMessage
from packages.kernel.approvals import ApprovalError, approval_for_context
from packages.kernel.contracts import AuthorizationDecision, RuntimeRequest, RuntimeResponse
from packages.kernel.idempotency import (
    IdempotencyConflict,
    IdempotencyInProgress,
    complete_request,
    reserve_request,
)
from packages.personal_modules.task_service import (
    PersonalTaskConflict,
    find_personal_task_replay,
    personal_task_payload,
    submit_personal_task,
)
from packages.security.execution_context import resolve_personal_execution_context
from packages.security.surfaces import SurfaceKind


router = APIRouter(prefix="/client", tags=["dragonzpyder-personal-client"])

_SUBMISSION_CAPABILITY = "personal.client.submit"


class PersonalClientSubmitInput(BaseModel):
    """One retry-safe Personal task submission from a thin external client.

    Workspace authority is intentionally absent from this contract. A DragonZpyder
    client can continue a Personal conversation, but it cannot ask this adapter to
    select or elevate into a Workspace scope.
    """

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=12_000)
    request_id: str = Field(min_length=8, max_length=120)
    conversation_id: str | None = Field(default=None, max_length=120)


class PersonalClientTaskSubmitInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=12_000)
    request_id: str = Field(min_length=8, max_length=120)
    conversation_id: str | None = Field(default=None, max_length=120)
    max_steps: int = Field(default=8, ge=1, le=24)
    max_mutations: int = Field(default=4, ge=0, le=8)


class PersonalClientTaskResumeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: str | None = Field(default=None, max_length=36)


def _claim_request(payload: PersonalClientSubmitInput) -> RuntimeRequest:
    return RuntimeRequest(
        goal=payload.message,
        capability_id=_SUBMISSION_CAPABILITY,
        arguments={"conversation_id": payload.conversation_id or ""},
        conversation_id=payload.conversation_id,
        # Keep client retry identities in a distinct namespace from Kernel step IDs.
        request_id=f"dragonzpyder:{payload.request_id}",
    )


def _idempotency_error(error: Exception) -> HTTPException:
    if isinstance(error, IdempotencyInProgress):
        return HTTPException(
            status_code=409,
            detail={
                "code": "PERSONAL_REQUEST_IN_PROGRESS",
                "message": (
                    "This Personal request is already being processed. Reuse the same "
                    "request ID rather than creating a second submission."
                ),
            },
        )
    return HTTPException(
        status_code=409,
        detail={
            "code": "PERSONAL_REQUEST_ID_CONFLICT",
            "message": "This request ID was already used for a different Personal submission.",
        },
    )


def _task_error(error: Exception) -> HTTPException:
    if isinstance(error, PersonalTaskConflict):
        return HTTPException(
            409,
            detail={"code": "PERSONAL_TASK_CONFLICT", "message": str(error)},
        )
    if isinstance(error, AgentRuntimeDisabled):
        return HTTPException(
            503,
            detail={"code": "AGENT_RUNTIME_DISABLED", "message": str(error)},
        )
    if isinstance(error, AgentInferenceError):
        return HTTPException(
            503 if error.retryable or error.code == "inference_not_configured" else 502,
            detail={"code": error.code, "message": str(error), "retryable": error.retryable},
        )
    if isinstance(error, AgentPlanningError):
        status = 409 if error.code == "no_authorized_capabilities" else 503 if error.code == "planner_model_failed" else 422
        return HTTPException(status, detail={"code": error.code, "message": str(error)})
    if isinstance(error, (AgentRunStateError, ApprovalError)):
        return HTTPException(
            409,
            detail={"code": "PERSONAL_TASK_STATE_CONFLICT", "message": str(error)},
        )
    if isinstance(error, ValueError):
        return HTTPException(422, detail={"code": "INVALID_PERSONAL_TASK", "message": str(error)})
    return HTTPException(500, detail={"code": "PERSONAL_TASK_FAILED", "message": "Personal task operation failed"})


async def _personal_context(
    db: AsyncSession,
    *,
    auth: AccountAuthContext,
    conversation_id: str | None = None,
):
    return await resolve_personal_execution_context(
        db,
        user_id=auth.user.id,
        channel="dragonzpyder_cli",
        surface=SurfaceKind.PERSONAL_PRIVATE,
        conversation_id=conversation_id,
    )


@router.post("/submit")
async def submit_personal_request(
    payload: PersonalClientSubmitInput,
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Submit one Personal Runtime1 turn with durable ingress idempotency."""

    # Keep Runtime1/httpx dependencies out of lightweight Personal capability imports.
    # The client adapter pays that dependency cost only when the endpoint actually runs.
    from apps.api.agent_runtime_router import _conversation, _run

    context = await _personal_context(db, auth=auth, conversation_id=payload.conversation_id)
    claim_request = _claim_request(payload)
    claim_run_id = str(uuid4())
    try:
        reservation = await reserve_request(
            db,
            context=context,
            request=claim_request,
            run_id=claim_run_id,
        )
    except (IdempotencyConflict, IdempotencyInProgress) as error:
        raise _idempotency_error(error) from error

    if reservation.replay is not None:
        replay = dict(reservation.replay.result or {})
        replay.setdefault("client_request_id", payload.request_id)
        replay["replayed"] = True
        return replay

    conversation = await _conversation(
        db,
        context=context,
        requested_id=payload.conversation_id,
        first_message=payload.message,
    )
    context = await _personal_context(db, auth=auth, conversation_id=conversation.id)
    result = await _run(
        db,
        context=context,
        message=payload.message,
        conversation=conversation,
    )
    result["client_request_id"] = payload.request_id
    result["replayed"] = False

    await complete_request(
        db,
        claim=reservation.claim,
        response=RuntimeResponse(
            run_id=claim_run_id,
            status="completed",
            capability_id=_SUBMISSION_CAPABILITY,
            decision=AuthorizationDecision.ALLOW,
            result=result,
            done=True,
            trace=(),
        ),
    )
    await db.commit()
    return result


@router.post("/tasks", status_code=202)
async def submit_personal_task_request(
    payload: PersonalClientTaskSubmitInput,
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Accept a durable Personal objective and commit it before returning 202."""

    from apps.api.agent_runtime_router import _conversation

    budget = AgentBudget(
        max_steps=payload.max_steps,
        max_mutations=payload.max_mutations,
    )
    initial_context = await _personal_context(
        db,
        auth=auth,
        conversation_id=payload.conversation_id,
    )
    try:
        replay = await find_personal_task_replay(
            db,
            context=initial_context,
            request_id=payload.request_id,
            goal=payload.message,
            budget=budget,
        )
        if replay is not None:
            result = await personal_task_payload(
                db,
                context=initial_context,
                run_id=replay.row.id,
            )
            if result is None:
                raise PersonalTaskConflict("Durable Personal task disappeared during replay")
            result["client_request_id"] = payload.request_id
            result["replayed"] = True
            return result

        conversation = await _conversation(
            db,
            context=initial_context,
            requested_id=payload.conversation_id,
            first_message=payload.message,
        )
        context = await _personal_context(db, auth=auth, conversation_id=conversation.id)
        db.add(
            AgentChatMessage(
                conversation_id=conversation.id,
                role="user",
                content=payload.message,
                created_at=datetime.utcnow(),
            )
        )
        conversation.updated_at = datetime.utcnow()
        submission = await submit_personal_task(
            db,
            context=context,
            request_id=payload.request_id,
            goal=payload.message,
            budget=budget,
        )
        # This commit is the acceptance boundary: once 202 is emitted, the durable
        # task/plan/source conversation already exist independently of this process.
        await db.commit()
        result = await personal_task_payload(db, context=context, run_id=submission.row.id)
        if result is None:
            raise PersonalTaskConflict("Durable Personal task was not visible after commit")
        result["client_request_id"] = payload.request_id
        result["replayed"] = submission.replayed
        return result
    except Exception as error:
        await db.rollback()
        if isinstance(
            error,
            (
                PersonalTaskConflict,
                AgentRuntimeDisabled,
                AgentInferenceError,
                AgentPlanningError,
                AgentRunStateError,
                ApprovalError,
                ValueError,
            ),
        ):
            raise _task_error(error) from error
        raise


@router.get("/tasks/{task_id}")
async def personal_task_status(
    task_id: str,
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    context = await _personal_context(db, auth=auth)
    result = await personal_task_payload(db, context=context, run_id=task_id)
    if result is None:
        raise HTTPException(404, detail="Personal task not found")
    return result


@router.post("/tasks/{task_id}/cancel")
async def cancel_personal_task(
    task_id: str,
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    context = await _personal_context(db, auth=auth)
    try:
        await request_cancellation(db, context=context, run_id=task_id)
        await db.commit()
    except AgentRunStateError as error:
        await db.rollback()
        raise _task_error(error) from error
    result = await personal_task_payload(db, context=context, run_id=task_id)
    if result is None:
        raise HTTPException(404, detail="Personal task not found")
    return result


@router.post("/tasks/{task_id}/resume")
async def resume_personal_task(
    task_id: str,
    payload: PersonalClientTaskResumeInput,
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    context = await _personal_context(db, auth=auth)
    current = await personal_task_payload(db, context=context, run_id=task_id)
    if current is None:
        raise HTTPException(404, detail="Personal task not found")
    if current["cancellation_requested"]:
        raise HTTPException(
            409,
            detail={"code": "PERSONAL_TASK_CANCELLED", "message": "Cancelled Personal task cannot resume"},
        )

    status = str(current["status"])
    if status == "waiting_approval":
        approval_id = str(payload.approval_id or "").strip()
        expected = str(current.get("approval_id") or "").strip()
        if not approval_id or approval_id != expected:
            raise HTTPException(
                409,
                detail={
                    "code": "PERSONAL_TASK_APPROVAL_REQUIRED",
                    "message": "Resume requires the exact approval attached to the waiting task step",
                    "approval_id": expected or None,
                },
            )
        try:
            approval = await approval_for_context(
                db,
                context=context,
                approval_id=approval_id,
            )
            if approval.status != "approved":
                raise AgentRunStateError("Personal task approval has not been approved")
            await queue_after_approval(
                db,
                context=context,
                run_id=task_id,
                approval_id=approval_id,
            )
            await db.commit()
        except (AgentRunStateError, ApprovalError, ValueError) as error:
            await db.rollback()
            raise _task_error(error) from error
    elif status not in {"queued", "running"}:
        raise HTTPException(
            409,
            detail={
                "code": "PERSONAL_TASK_NOT_RESUMABLE",
                "message": f"Personal task in state {status} cannot resume",
            },
        )

    result = await personal_task_payload(db, context=context, run_id=task_id)
    if result is None:
        raise HTTPException(404, detail="Personal task not found")
    return result
