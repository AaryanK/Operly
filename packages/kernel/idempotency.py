from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.kernel_models import KernelRequestClaim
from packages.kernel.approvals import approval_for_context, claim_approved_invocation
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


def _ingress_namespace(context: ExecutionContext) -> str:
    """Bind transport-derived request IDs to the authenticated ingress identity.

    MCP JSON-RPC IDs are stable enough to recognize a transport retry, but they are
    only client-local identifiers. The trusted MCP ingress records the authenticated
    client and grant in ExecutionContext metadata. Include a compact digest of those
    values in the durable idempotency namespace so two grants owned by the same user
    cannot collide merely because their clients reuse the same JSON-RPC ID.
    """

    metadata = getattr(context, "metadata", None) or {}
    if str(metadata.get("ingress") or "").strip().lower() != "operly_mcp":
        return ""
    client_id = str(metadata.get("mcp_client_id") or "").strip()
    grant_id = str(metadata.get("mcp_grant_id") or "").strip()
    if not client_id or not grant_id:
        raise IdempotencyConflict("MCP request is missing its authenticated client/grant identity")
    digest = hashlib.sha256(f"{client_id}\0{grant_id}".encode("utf-8")).hexdigest()[:24]
    return f":mcp:{digest}"


def _idempotency_key(context: ExecutionContext, request_id: str) -> str:
    principal = str(context.principal_id or context.user_id or "anonymous")
    return f"{_scope_identity(context)}:{principal}{_ingress_namespace(context)}:{request_id}"


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


async def find_completed_request(
    db: AsyncSession,
    *,
    context: ExecutionContext,
    request: RuntimeRequest,
) -> RuntimeResponse | None:
    """Return an exact completed replay without reserving execution.

    The Kernel calls this only *after* resolving the capability and re-evaluating the
    caller's current scope/surface/permissions. That prevents a cached response from
    becoming a stale-authority bypass after a role or permission change.
    """

    request_id = str(request.request_id or "").strip()
    if not request_id:
        return None
    key = _idempotency_key(context, request_id)
    existing = await db.scalar(
        select(KernelRequestClaim).where(KernelRequestClaim.idempotency_key == key)
    )
    if existing is None:
        return None
    return _validate_existing(
        existing,
        request=request,
        arguments_hash=_arguments_hash(request),
    )


async def reserve_request(
    db: AsyncSession,
    *,
    context: ExecutionContext,
    request: RuntimeRequest,
    run_id: str,
) -> IdempotencyReservation:
    """Durably claim an authorized mutation immediately before provider execution.

    Mutations are never allowed to bypass idempotency. Every ingress must provide a
    stable request identity (or derive one from its transport, as MCP does). For an
    approval-gated mutation, the same transaction also atomically transitions the
    exact human approval from approved to executing and binds it to its original
    request ID. The reservation is committed before the provider runs, so a crash or
    lost response cannot roll back the at-most-once boundary and resurrect a write.
    """

    request_id = str(request.request_id or "").strip()
    if not request_id:
        raise IdempotencyConflict(
            "Mutating capability execution requires a stable request_id"
        )

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
        await db.commit()
        return IdempotencyReservation(claim=existing)

    if request.approval_id:
        # Resolve the already-authorized capability from the durable approval itself.
        # The Kernel has just validated that approval against the resolved capability;
        # using the stored ID here also supports goal-resolved RuntimeRequests whose
        # request.capability_id is intentionally omitted.
        approval = await approval_for_context(
            db,
            context=context,
            approval_id=request.approval_id,
            lock=True,
        )
        await claim_approved_invocation(
            db,
            context=context,
            approval_id=request.approval_id,
            request_id=request_id,
            capability_id=approval.capability_id,
            arguments=dict(request.arguments),
        )

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
        # This commit is deliberate: the at-most-once reservation (and approval claim,
        # when present) must survive a provider timeout, process crash, or later
        # transaction rollback. Provider/database effects happen only after this point.
        await db.commit()
        return IdempotencyReservation(claim=claim)
    except IntegrityError:
        # Another transaction won the same scoped request key. Rolling back also
        # restores any local approval transition attempted by this losing transaction.
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
    claim.capability_id = response.capability_id
    claim.response_json = json.dumps(
        response.as_dict(), separators=(",", ":"), sort_keys=True, default=str
    )
    await db.flush()