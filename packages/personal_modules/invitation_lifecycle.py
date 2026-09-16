from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from email.utils import getaddresses
from hashlib import sha256
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from packages.agent_runtime.contracts import (
    AgentPlan,
    AgentPlanStep,
    AgentRunResult,
    AgentRunStatus,
    AgentStepResult,
)
from packages.agent_runtime.store import AgentRunStateError, transition_run
from packages.database.account_connector_models import AccountConnector
from packages.database.agent_runtime_models import AgentRuntimeRun
from packages.kernel.contracts import RuntimeRequest
from packages.kernel.runtime import RuntimeExecutionError
from packages.personal_modules.runtime import build_personal_runtime
from packages.security.execution_context import ExecutionContextError, resolve_personal_execution_context
from packages.security.surfaces import SurfaceKind


WAIT_KIND = "gmail_thread_reply"
GMAIL_SEND = "google.gmail.send_email"
GMAIL_SEARCH = "google.gmail.search"
GMAIL_READ = "google.gmail.read_message"
CALENDAR_FREEBUSY = "google.calendar.freebusy"
CALENDAR_CREATE = "google.calendar.create_event"


class InvitationLifecycleError(RuntimeError):
    pass


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _object(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return dict(value) if isinstance(value, dict) else {}


def _array(raw: str) -> list[dict[str, Any]]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _addresses(value: Any) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        source = [str(item) for item in value]
    else:
        source = [str(value or "")]
    return tuple(
        sorted(
            {
                address.strip().lower()
                for _, address in getaddresses(source)
                if address.strip()
            }
        )
    )


def _one_recipient(step: AgentPlanStep) -> str | None:
    recipients = _addresses(step.arguments.get("to"))
    return recipients[0] if len(recipients) == 1 else None


def _calendar_id(arguments: dict[str, Any]) -> str:
    return str(arguments.get("calendar_id") or "primary").strip() or "primary"


def _as_utc(value: Any, *, time_zone: str | None = None) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        if not time_zone:
            return None
        try:
            parsed = parsed.replace(tzinfo=ZoneInfo(str(time_zone)))
        except Exception:
            return None
    return parsed.astimezone(timezone.utc)


def _invitation_steps(
    plan: AgentPlan,
    send_step: AgentPlanStep,
) -> tuple[str, AgentPlanStep, AgentPlanStep] | None:
    try:
        send_index = next(index for index, item in enumerate(plan.steps) if item.step_id == send_step.step_id)
    except StopIteration:
        return None
    expected_sender = _one_recipient(send_step)
    if expected_sender is None:
        return None

    for create_index in range(send_index + 1, len(plan.steps)):
        create = plan.steps[create_index]
        if create.capability_id != CALENDAR_CREATE:
            continue
        if expected_sender not in _addresses(create.arguments.get("attendees")):
            continue
        target_calendar = _calendar_id(dict(create.arguments))
        for freebusy_index in range(send_index + 1, create_index):
            freebusy = plan.steps[freebusy_index]
            if freebusy.capability_id != CALENDAR_FREEBUSY:
                continue
            calendar_ids = {
                str(item).strip()
                for item in freebusy.arguments.get("calendar_ids", [])
                if str(item).strip()
            }
            if target_calendar in calendar_ids:
                return expected_sender, freebusy, create
        raise InvitationLifecycleError(
            "Invitation calendar creation requires a fresh free/busy step after the send and before event creation"
        )
    return None


async def _connector_id_for_send(
    db: AsyncSession,
    *,
    row: AgentRuntimeRun,
    step: AgentPlanStep,
    result: dict[str, Any],
) -> str:
    requested = str(step.arguments.get("connector_id") or "").strip()
    if requested:
        return requested
    provider_account = str(result.get("provider_account") or "").strip()
    if not provider_account or not row.owner_user_id:
        raise InvitationLifecycleError("Approved Gmail send did not preserve provider account identity")
    rows = (
        await db.scalars(
            select(AccountConnector).where(
                AccountConnector.user_id == row.owner_user_id,
                AccountConnector.provider == "google",
                AccountConnector.provider_account_id == provider_account,
                AccountConnector.enabled.is_(True),
                AccountConnector.status == "connected",
            )
        )
    ).all()
    if len(rows) != 1:
        raise InvitationLifecycleError(
            "Approved Gmail send cannot be bound to exactly one connected Personal Google account"
        )
    return str(rows[0].id)


def _default_deadline() -> datetime:
    days = max(1, min(int(os.getenv("OPERLY_INVITATION_REPLY_DEADLINE_DAYS", "7")), 30))
    return datetime.now(timezone.utc) + timedelta(days=days)


def _deadline_expired(value: datetime | None) -> bool:
    if value is None:
        return False
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc) <= datetime.now(timezone.utc)


def _strip_quoted_reply(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n").strip()
    if not text:
        return ""
    text = re.split(r"\nOn .{0,500} wrote:\s*\n", text, maxsplit=1, flags=re.IGNORECASE)[0]
    text = re.split(r"\n-{2,}\s*Original Message\s*-{2,}\s*\n", text, maxsplit=1, flags=re.IGNORECASE)[0]
    lines: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith(">"):
            break
        lines.append(line)
    return " ".join(" ".join(lines).split())[:2000]


def classify_invitation_reply(value: Any) -> str:
    """Conservatively classify only the correspondent's new plain-text reply.

    Retrieved email text is never interpreted as instructions. It can only satisfy the
    narrow affirmative/negative predicate below; everything else remains ambiguous.
    """

    text = _strip_quoted_reply(value).lower()
    if not text:
        return "ambiguous"
    negative = re.search(
        r"\b(no|nope|cannot|can't|cant|won't|wont|not available|doesn't work|does not work|"
        r"can't make|cannot make|different time|another time|reschedule|unfortunately)\b",
        text,
    )
    if negative:
        return "negative"
    if re.search(
        r"^(yes|yep|yeah|sure|ok|okay|sounds good|that works|perfect|confirmed|"
        r"let'?s do it|see you then)\b",
        text,
    ):
        return "affirmative"
    if re.search(r"\b(works for me|time works for me|see you then|confirmed)\b", text):
        return "affirmative"
    return "ambiguous"


def _poll_request_id(run_id: str, lease_token: str, suffix: str) -> str:
    digest = sha256(f"{run_id}\0{lease_token}\0{suffix}".encode("utf-8")).hexdigest()
    return f"agent-wait:{digest}"


def _observation(
    *,
    message: dict[str, Any],
    expected_sender: str,
    classification: str,
) -> dict[str, Any]:
    body = _strip_quoted_reply(message.get("text_body"))
    return {
        "kind": "gmail_reply",
        "message_id": str(message.get("id") or ""),
        "thread_id": str(message.get("thread_id") or ""),
        "sender": expected_sender,
        "classification": classification,
        "body_sha256": sha256(body.encode("utf-8")).hexdigest(),
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }


def _append_observation(row: AgentRuntimeRun, observation: dict[str, Any]) -> bool:
    current = _array(row.verified_observations_json)
    message_id = str(observation.get("message_id") or "")
    if message_id and any(str(item.get("message_id") or "") == message_id for item in current):
        return False
    current.append(observation)
    row.verified_observations_json = _json(current[-100:])
    row.checkpoint_version = int(row.checkpoint_version or 0) + 1
    return True


def _slot_is_free(
    predicate: dict[str, Any],
    *,
    freebusy_step: AgentPlanStep,
    freebusy_result: dict[str, Any],
    create_step: AgentPlanStep,
) -> bool:
    create_arguments = dict(create_step.arguments)
    freebusy_arguments = dict(freebusy_step.arguments)
    target_calendar = str(predicate.get("calendar_id") or "")
    if not target_calendar or target_calendar != _calendar_id(create_arguments):
        return False
    if str(predicate.get("start") or "") != str(create_arguments.get("start") or ""):
        return False
    if str(predicate.get("end") or "") != str(create_arguments.get("end") or ""):
        return False

    create_tz = str(create_arguments.get("time_zone") or "") or None
    freebusy_tz = str(freebusy_arguments.get("time_zone") or create_tz or "") or None
    start = _as_utc(create_arguments.get("start"), time_zone=create_tz)
    end = _as_utc(create_arguments.get("end"), time_zone=create_tz)
    window_start = _as_utc(freebusy_arguments.get("time_min"), time_zone=freebusy_tz)
    window_end = _as_utc(freebusy_arguments.get("time_max"), time_zone=freebusy_tz)
    if not start or not end or not window_start or not window_end or not start < end:
        return False
    if window_start > start or window_end < end:
        return False

    calendars = freebusy_result.get("calendars")
    if not isinstance(calendars, dict):
        return False
    calendar = calendars.get(target_calendar)
    if not isinstance(calendar, dict) or calendar.get("errors"):
        return False
    busy = calendar.get("busy")
    if not isinstance(busy, list):
        return False
    for block in busy:
        if not isinstance(block, dict):
            return False
        busy_start = _as_utc(block.get("start"), time_zone=freebusy_tz)
        busy_end = _as_utc(block.get("end"), time_zone=freebusy_tz)
        if not busy_start or not busy_end:
            return False
        if start < busy_end and end > busy_start:
            return False
    return True


async def invitation_post_step_gate(
    db: AsyncSession,
    row: AgentRuntimeRun,
    plan: AgentPlan,
    step: AgentPlanStep,
    step_result: AgentStepResult,
    records: tuple[AgentStepResult, ...],
) -> AgentRunResult | None:
    """Pause/resume the invitation lifecycle without granting new authority."""

    if row.scope_kind != "personal":
        return None

    if step.capability_id == GMAIL_SEND:
        try:
            lifecycle = _invitation_steps(plan, step)
        except InvitationLifecycleError as error:
            await transition_run(
                db,
                run_id=row.id,
                to_status="failed",
                current_step_id=step.step_id,
                error_code="invitation_recheck_required",
                error_message=str(error),
            )
            await db.commit()
            return AgentRunResult(
                run_id=row.id,
                status=AgentRunStatus.FAILED,
                steps=records,
                next_step_id=step.step_id,
                error_code="invitation_recheck_required",
                error=str(error),
            )
        if lifecycle is None:
            return None
        expected_sender, freebusy_step, create_step = lifecycle
        result = dict(step_result.result or {})
        required = ("message_id", "thread_id", "provider_account", "rfc822_message_id", "approval_arguments_hash")
        if any(not str(result.get(key) or "").strip() for key in required):
            message = "Approved Gmail invitation is missing durable provider/thread identity"
            await transition_run(
                db,
                run_id=row.id,
                to_status="failed",
                current_step_id=step.step_id,
                error_code="invitation_send_identity_missing",
                error_message=message,
            )
            await db.commit()
            return AgentRunResult(
                run_id=row.id,
                status=AgentRunStatus.FAILED,
                steps=records,
                next_step_id=step.step_id,
                error_code="invitation_send_identity_missing",
                error=message,
            )
        try:
            connector_id = await _connector_id_for_send(
                db,
                row=row,
                step=step,
                result=result,
            )
        except InvitationLifecycleError as error:
            await transition_run(
                db,
                run_id=row.id,
                to_status="failed",
                current_step_id=step.step_id,
                error_code="invitation_account_ambiguous",
                error_message=str(error),
            )
            await db.commit()
            return AgentRunResult(
                run_id=row.id,
                status=AgentRunStatus.FAILED,
                steps=records,
                next_step_id=step.step_id,
                error_code="invitation_account_ambiguous",
                error=str(error),
            )

        deadline = row.deadline_at or _default_deadline()
        predicate = {
            "kind": WAIT_KIND,
            "state": "waiting",
            "connector_id": connector_id,
            "provider_account": str(result["provider_account"]),
            "thread_id": str(result["thread_id"]),
            "sent_message_id": str(result["message_id"]),
            "rfc822_message_id": str(result["rfc822_message_id"]),
            "invitation_approval_hash": str(result["approval_arguments_hash"]),
            "expected_sender": expected_sender,
            "freebusy_step_id": freebusy_step.step_id,
            "calendar_event_step_id": create_step.step_id,
            "calendar_id": _calendar_id(dict(create_step.arguments)),
            "start": str(create_step.arguments.get("start") or ""),
            "end": str(create_step.arguments.get("end") or ""),
            "established_at": datetime.now(timezone.utc).isoformat(),
        }
        waiting = await transition_run(
            db,
            run_id=row.id,
            to_status="waiting_event",
            current_step_id=freebusy_step.step_id,
            error_code="waiting_for_invitation_reply",
            error_message="Waiting for a verified reply from the invited recipient",
        )
        waiting.wait_predicate_json = _json(predicate)
        waiting.deadline_at = deadline
        await db.commit()
        return AgentRunResult(
            run_id=row.id,
            status=AgentRunStatus.WAITING_EVENT,
            steps=records,
            next_step_id=freebusy_step.step_id,
            error_code="waiting_for_invitation_reply",
            error="Waiting for a verified reply from the invited recipient",
        )

    predicate = _object(row.wait_predicate_json)
    if predicate.get("kind") != WAIT_KIND:
        return None

    if (
        step.capability_id == CALENDAR_FREEBUSY
        and step.step_id == predicate.get("freebusy_step_id")
        and predicate.get("state") == "reply_verified"
    ):
        create_step = next(
            (item for item in plan.steps if item.step_id == predicate.get("calendar_event_step_id")),
            None,
        )
        if create_step is None or not _slot_is_free(
            predicate,
            freebusy_step=step,
            freebusy_result=dict(step_result.result or {}),
            create_step=create_step,
        ):
            message = "The invited calendar slot is no longer verifiably free after the reply"
            await transition_run(
                db,
                run_id=row.id,
                to_status="failed",
                current_step_id=step.step_id,
                error_code="calendar_slot_no_longer_free",
                error_message=message,
            )
            await db.commit()
            return AgentRunResult(
                run_id=row.id,
                status=AgentRunStatus.FAILED,
                steps=records,
                next_step_id=step.step_id,
                error_code="calendar_slot_no_longer_free",
                error=message,
            )
        predicate["state"] = "calendar_rechecked"
        predicate["calendar_rechecked_at"] = datetime.now(timezone.utc).isoformat()
        row.wait_predicate_json = _json(predicate)
        row.checkpoint_version = int(row.checkpoint_version or 0) + 1
        return None

    if (
        step.capability_id == CALENDAR_CREATE
        and step.step_id == predicate.get("calendar_event_step_id")
        and predicate.get("state") == "calendar_rechecked"
    ):
        predicate["state"] = "calendar_created"
        predicate["calendar_created_at"] = datetime.now(timezone.utc).isoformat()
        row.wait_predicate_json = _json(predicate)
        row.checkpoint_version = int(row.checkpoint_version or 0) + 1
    return None


async def claim_invitation_wait(
    db: AsyncSession,
    *,
    run_id: str,
    lease_token: str,
    lease_seconds: int = 120,
) -> AgentRuntimeRun | None:
    token = str(lease_token or "").strip()
    if not token or len(token) > 80:
        raise ValueError("lease_token must contain 1-80 characters")
    now = datetime.utcnow()
    result = await db.execute(
        update(AgentRuntimeRun)
        .where(
            AgentRuntimeRun.id == run_id,
            AgentRuntimeRun.scope_kind == "personal",
            AgentRuntimeRun.status == "waiting_event",
            AgentRuntimeRun.cancellation_requested.is_(False),
            or_(
                AgentRuntimeRun.lease_until.is_(None),
                AgentRuntimeRun.lease_until < now,
                AgentRuntimeRun.lease_token == token,
            ),
        )
        .values(
            lease_token=token,
            lease_until=now + timedelta(seconds=max(30, min(int(lease_seconds), 900))),
            updated_at=now,
        )
    )
    if result.rowcount != 1:
        return None
    await db.flush()
    return await db.get(AgentRuntimeRun, run_id)


async def _defer_wait(
    db: AsyncSession,
    *,
    row: AgentRuntimeRun,
    lease_token: str,
    seconds: int,
) -> None:
    if row.status != "waiting_event" or row.lease_token != lease_token:
        raise AgentRunStateError("Invitation wait lease was lost")
    row.lease_token = None
    row.lease_until = datetime.utcnow() + timedelta(seconds=max(30, min(int(seconds), 900)))
    row.updated_at = datetime.utcnow()
    await db.commit()


async def _terminalize_wait(
    db: AsyncSession,
    *,
    row: AgentRuntimeRun,
    lease_token: str,
    status: str,
    code: str | None,
    message: str | None,
    result: dict[str, Any],
    predicate: dict[str, Any],
    observation: dict[str, Any] | None = None,
) -> None:
    locked = await db.scalar(
        select(AgentRuntimeRun).where(AgentRuntimeRun.id == row.id).with_for_update()
    )
    if locked is None or locked.status != "waiting_event" or locked.lease_token != lease_token:
        raise AgentRunStateError("Invitation wait lease was lost")
    if locked.cancellation_requested:
        raise AgentRunStateError("Cancelled invitation wait cannot be resumed")
    if observation is not None:
        _append_observation(locked, observation)
    predicate = dict(predicate)
    predicate["state"] = status
    predicate["resolved_at"] = datetime.now(timezone.utc).isoformat()
    locked.wait_predicate_json = _json(predicate)
    locked.status = "queued" if status == "reply_verified" else "completed"
    locked.error_code = code
    locked.error_message = message
    locked.result_json = _json(result)
    locked.lease_token = None
    locked.lease_until = None
    locked.updated_at = datetime.utcnow()
    if locked.status == "completed":
        locked.finished_at = datetime.utcnow()
    await db.commit()


async def _expire_wait(
    db: AsyncSession,
    *,
    row: AgentRuntimeRun,
    lease_token: str,
    predicate: dict[str, Any],
) -> None:
    locked = await db.scalar(
        select(AgentRuntimeRun).where(AgentRuntimeRun.id == row.id).with_for_update()
    )
    if locked is None or locked.status != "waiting_event" or locked.lease_token != lease_token:
        raise AgentRunStateError("Invitation wait lease was lost")
    predicate = dict(predicate)
    predicate["state"] = "expired"
    predicate["resolved_at"] = datetime.now(timezone.utc).isoformat()
    locked.wait_predicate_json = _json(predicate)
    locked.status = "failed"
    locked.error_code = "reply_deadline_elapsed"
    locked.error_message = "The invitation reply deadline elapsed before a verified acceptance"
    locked.lease_token = None
    locked.lease_until = None
    locked.finished_at = datetime.utcnow()
    locked.updated_at = datetime.utcnow()
    await db.commit()


async def _current_personal_context(db: AsyncSession, row: AgentRuntimeRun):
    user_id = str(row.authority_user_id or "").strip()
    if not user_id or row.scope_kind != "personal" or row.workspace_id is not None:
        raise ExecutionContextError("Stored Personal invitation authority is invalid")
    surface = SurfaceKind.coerce(row.source_surface)
    if surface is not SurfaceKind.PERSONAL_PRIVATE:
        raise ExecutionContextError("Stored Personal invitation surface is invalid")
    context = await resolve_personal_execution_context(
        db,
        user_id=user_id,
        channel=row.source_channel,
        surface=surface,
        conversation_id=row.conversation_id,
    )
    if str(context.principal_id or "") != row.principal_id:
        raise ExecutionContextError("Invitation principal changed during authority resolution")
    return context


async def poll_invitation_wait(
    db: AsyncSession,
    *,
    run_id: str,
    lease_token: str,
    lease_seconds: int = 120,
    defer_seconds: int = 60,
) -> bool:
    """Poll one task-specific Gmail reply predicate through Kernel read capabilities."""

    row = await claim_invitation_wait(
        db,
        run_id=run_id,
        lease_token=lease_token,
        lease_seconds=lease_seconds,
    )
    if row is None:
        await db.rollback()
        return False
    await db.commit()
    row = await db.get(AgentRuntimeRun, run_id)
    if row is None:
        return False
    predicate = _object(row.wait_predicate_json)
    if predicate.get("kind") != WAIT_KIND or predicate.get("state") != "waiting":
        await _defer_wait(db, row=row, lease_token=lease_token, seconds=defer_seconds)
        return True
    if _deadline_expired(row.deadline_at):
        await _expire_wait(db, row=row, lease_token=lease_token, predicate=predicate)
        return True

    try:
        context = await _current_personal_context(db, row)
    except ExecutionContextError:
        await _expire_wait(db, row=row, lease_token=lease_token, predicate=predicate)
        return True

    connector_id = str(predicate.get("connector_id") or "")
    expected_sender = str(predicate.get("expected_sender") or "").lower()
    thread_id = str(predicate.get("thread_id") or "")
    sent_message_id = str(predicate.get("sent_message_id") or "")
    if not connector_id or not expected_sender or not thread_id:
        await _expire_wait(db, row=row, lease_token=lease_token, predicate=predicate)
        return True

    runtime = build_personal_runtime()
    try:
        search = await runtime.execute(
            db,
            context=context,
            request=RuntimeRequest(
                goal="Check for the verified reply to the pending invitation",
                capability_id=GMAIL_SEARCH,
                arguments={
                    "connector_id": connector_id,
                    "query": f"from:{expected_sender}",
                    "limit": 20,
                },
                conversation_id=row.conversation_id,
                request_id=_poll_request_id(run_id, lease_token, "search"),
            ),
        )
    except RuntimeExecutionError:
        await _defer_wait(db, row=row, lease_token=lease_token, seconds=defer_seconds)
        return True

    observed_ids = {
        str(item.get("message_id") or "")
        for item in _array(row.verified_observations_json)
        if item.get("message_id")
    }
    candidates = search.result.get("messages") if isinstance(search.result, dict) else None
    if not isinstance(candidates, list):
        candidates = []

    for index, candidate in enumerate(candidates[:20]):
        if not isinstance(candidate, dict):
            continue
        message_id = str(candidate.get("id") or "")
        if not message_id or message_id == sent_message_id or message_id in observed_ids:
            continue
        if str(candidate.get("thread_id") or "") != thread_id:
            continue
        if expected_sender not in _addresses(candidate.get("from")):
            continue
        try:
            detail_response = await runtime.execute(
                db,
                context=context,
                request=RuntimeRequest(
                    goal="Read the candidate reply for the pending invitation",
                    capability_id=GMAIL_READ,
                    arguments={"connector_id": connector_id, "message_id": message_id},
                    conversation_id=row.conversation_id,
                    request_id=_poll_request_id(run_id, lease_token, f"read:{index}:{message_id}"),
                ),
            )
        except RuntimeExecutionError:
            continue
        message = dict(detail_response.result or {})
        if str(message.get("id") or "") != message_id:
            continue
        if str(message.get("thread_id") or "") != thread_id:
            continue
        if expected_sender not in _addresses(message.get("from")):
            continue

        classification = classify_invitation_reply(message.get("text_body"))
        observation = _observation(
            message=message,
            expected_sender=expected_sender,
            classification=classification,
        )
        if classification == "affirmative":
            await _terminalize_wait(
                db,
                row=row,
                lease_token=lease_token,
                status="reply_verified",
                code=None,
                message=None,
                result={"invitation_outcome": "accepted", "reply_message_id": message_id},
                predicate=predicate,
                observation=observation,
            )
            return True
        if classification == "negative":
            await _terminalize_wait(
                db,
                row=row,
                lease_token=lease_token,
                status="declined",
                code=None,
                message=None,
                result={"invitation_outcome": "declined", "reply_message_id": message_id},
                predicate=predicate,
                observation=observation,
            )
            return True
        _append_observation(row, observation)
        observed_ids.add(message_id)

    await _defer_wait(db, row=row, lease_token=lease_token, seconds=defer_seconds)
    return True
