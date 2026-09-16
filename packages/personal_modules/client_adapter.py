from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AccountAuthContext, get_account_auth_context, get_db
from packages.kernel.contracts import AuthorizationDecision, RuntimeRequest, RuntimeResponse
from packages.kernel.idempotency import (
    IdempotencyConflict,
    IdempotencyInProgress,
    complete_request,
    reserve_request,
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


@router.post("/submit")
async def submit_personal_request(
    payload: PersonalClientSubmitInput,
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Submit one Personal Runtime1 turn with durable ingress idempotency.

    The claim is committed before conversation/runtime work begins. If the process loses
    the response after external effects may have happened, a transport retry cannot
    create a duplicate submission. Completed identical retries replay the stored result;
    an unresolved in-flight claim returns a truthful blocker for later reconciliation.
    """

    # Keep Runtime1/httpx dependencies out of lightweight Personal capability imports.
    # The client adapter pays that dependency cost only when the endpoint actually runs.
    from apps.api.agent_runtime_router import _conversation, _run

    context = await resolve_personal_execution_context(
        db,
        user_id=auth.user.id,
        channel="dragonzpyder_cli",
        surface=SurfaceKind.PERSONAL_PRIVATE,
        conversation_id=payload.conversation_id,
    )
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
    context = await resolve_personal_execution_context(
        db,
        user_id=auth.user.id,
        channel="dragonzpyder_cli",
        surface=SurfaceKind.PERSONAL_PRIVATE,
        conversation_id=conversation.id,
    )
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
