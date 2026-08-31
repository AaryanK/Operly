from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.kernel_models import KernelRequestClaim
from packages.kernel.contracts import AuthorizationDecision, RuntimeRequest, RuntimeResponse
from packages.security.execution_context import ExecutionContext


class IdempotencyConflict(RuntimeError):
    pass


class IdempotencyInProgress(RuntimeError):
    pass


@dataclass(slots=True)
class IdempotencyReservation:
    claim: KernelRequestClaim | None
    replay: RuntimeResponse | None = None


def _scope_identity(context: ExecutionContext) -> str:
    if context.is_personal:
        if not context.user_id:
            raise IdempotencyConflict("Personal request is missing its owner")
        return f"personal:{context.user_id}"
    if not context.workspace_id:
        raise IdempotencyConflict("Workspace request is missing its workspace")
    return f"workspace:{context.workspace_id}"


def _idempotency_key(context: ExecutionContext, request_id: str) -> str:
    principal = str(context.principal_id or context.user_id or "anonymous")
    return f"{_scope_identity(context)}:{principal}:{request_id}"


def _arguments_hash(request: RuntimeRequest) -> str:
    payload = json.dumps(
        {
            "goal": str(request.goal or ""),
            "capability_id": request.capability_id,
            "arguments": dict(request.arguments),
        },
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _response_from_json(value: str) -> RuntimeResponse:
    payload = json.loads(value or "{}")
    return RuntimeResponse(
        run_id=str(payload["run_id"]),
        status=str(payload["status"]),
        capability_id=payload.get("capability_id"),
        decision=AuthorizationDecision(str(payload["decision"])),
        result=dict(payload["result"]) if payload.get("result") is not None else None,
        done=bool(payload.get("done")),
        trace=tuple(dict(step) for step in payload.get("trace", [])),
    )


def _validate_existing(
    claim: KernelRequestClaim,
    *,
    request: RuntimeRequest,
    arguments_hash: str,
) -> RuntimeResponse | None:
    if claim.arguments_hash != arguments_hash:
        raise IdempotencyConflict(
            "request_id was already used with a different goal, capability, or argument payload"
        )
    if claim.status == "completed":
        return _response_from_json(claim.response_json)
    if claim.status == "running":
        raise IdempotencyInProgress("An identical request is already executing")
    return None


async def reserve_request(
    db: AsyncSession,
    *,
    context: ExecutionContext,
    request: RuntimeRequest,
    run_id: str,
) -> IdempotencyReservation:
    request_id = str(request.request_id or "").strip()
    if not request_id:
        return IdempotencyReservation(claim=None)

    key = _idempotency_key(context, request_id)
    arguments_hash = _arguments_hash(request)
    existing = await db.scalar(
        select(KernelRequestClaim).where(KernelRequestClaim.idempotency_key == key)
    )
    if existing is not None:
        replay = _validate_existing(existing, request=request, arguments_hash=arguments_hash)
        if replay is not None:
            return IdempotencyReservation(claim=existing, replay=replay)
        existing.status = "running"
        existing.run_id = run_id
        existing.response_json = "{}"
        await db.flush()
        return IdempotencyReservation(claim=existing)

    claim = KernelRequestClaim(
        idempotency_key=key,
        request_id=request_id,
        scope_kind=context.scope_kind.value,
        workspace_id=context.workspace_id,
        owner_user_id=context.user_id if context.is_personal else None,
        principal_id=context.principal_id,
        capability_id=request.capability_id,
        arguments_hash=arguments_hash,
        status="running",
        run_id=run_id,
        response_json="{}",
    )
    db.add(claim)
    try:
        await db.flush()
        return IdempotencyReservation(claim=claim)
    except IntegrityError:
        # Another transaction won the same scoped request key. Acquiring the unique
        # row is the first database mutation in the Kernel loop, so rollback is safe.
        await db.rollback()
        existing = await db.scalar(
            select(KernelRequestClaim).where(KernelRequestClaim.idempotency_key == key)
        )
        if existing is None:
            raise IdempotencyInProgress("An identical request is being claimed")
        replay = _validate_existing(existing, request=request, arguments_hash=arguments_hash)
        if replay is not None:
            return IdempotencyReservation(claim=existing, replay=replay)
        raise IdempotencyInProgress("An identical request is already executing")


async def complete_request(
    db: AsyncSession,
    *,
    claim: KernelRequestClaim | None,
    response: RuntimeResponse,
) -> None:
    if claim is None:
        return
    claim.status = "completed"
    claim.run_id = response.run_id
    claim.response_json = json.dumps(
        response.as_dict(), separators=(",", ":"), sort_keys=True, default=str
    )
    await db.flush()
