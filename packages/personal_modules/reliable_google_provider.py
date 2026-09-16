from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import getaddresses
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import aiohttp
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.kernel_models import KernelApproval, KernelRequestClaim
from packages.kernel.approvals import arguments_hash, canonical_arguments
from packages.kernel.contracts import CapabilityExecutionResult, CapabilitySpec
from packages.kernel.providers import ProviderExecutionUncertain
from packages.personal_modules.google_completion_provider import PersonalGoogleCompletionProvider
from packages.personal_modules.google_provider import (
    GMAIL_READ_SCOPES,
    PersonalGoogleProviderRejected,
    _calendar_id,
    _connector,
    _event_payload,
    _headers,
    _message_bodies,
    access_token,
    connector_scopes,
    request_json,
)
from packages.security.execution_context import ExecutionContext


GMAIL_SEND_CAPABILITY = "google.gmail.send_email"
GMAIL_READ_CAPABILITY = "google.gmail.read_message"
CALENDAR_CREATE_CAPABILITY = "google.calendar.create_event"


def _kernel_execution(
    context: ExecutionContext,
    *,
    expected_capability: str,
) -> dict[str, str]:
    raw = dict((context.metadata or {}).get("_kernel_execution") or {})
    identity = {
        "request_id": str(raw.get("request_id") or "").strip(),
        "approval_id": str(raw.get("approval_id") or "").strip(),
        "run_id": str(raw.get("run_id") or "").strip(),
        "capability_id": str(raw.get("capability_id") or "").strip(),
    }
    if not identity["request_id"] or not identity["approval_id"]:
        raise PersonalGoogleProviderRejected(
            "Approved Google mutation is missing trusted Kernel request identity"
        )
    if identity["capability_id"] != expected_capability:
        raise PersonalGoogleProviderRejected("Trusted Kernel capability identity is inconsistent")
    return identity


def _stable_rfc822_message_id(*, owner_user_id: str, request_id: str) -> str:
    digest = hashlib.sha256(f"{owner_user_id}\0{request_id}".encode("utf-8")).hexdigest()[:40]
    return f"<operly-{digest}@operly.invalid>"


def _stable_calendar_event_id(*, owner_user_id: str, request_id: str) -> str:
    """Return a Google Calendar-compatible deterministic event ID.

    Lowercase hexadecimal is a strict subset of Google's base32hex event-ID alphabet.
    Binding the ID to the Personal owner + Kernel request identity gives one provider
    resource for one logical approved step across retry/restart reconciliation.
    """

    return hashlib.sha256(f"{owner_user_id}\0{request_id}".encode("utf-8")).hexdigest()[:52]


def _email_message(arguments: dict[str, Any], *, rfc822_message_id: str) -> EmailMessage:
    message = EmailMessage()
    message["Message-ID"] = rfc822_message_id
    message["To"] = ", ".join(arguments.get("to") or [])
    if arguments.get("cc"):
        message["Cc"] = ", ".join(arguments["cc"])
    if arguments.get("bcc"):
        message["Bcc"] = ", ".join(arguments["bcc"])
    message["Subject"] = str(arguments.get("subject") or "")
    if arguments.get("reply_to"):
        message["Reply-To"] = str(arguments["reply_to"])
    message.set_content(str(arguments.get("text_body") or ""))
    return message


def _raw_message(message: EmailMessage) -> str:
    return base64.urlsafe_b64encode(message.as_bytes()).decode()


def _addresses(value: str | None) -> list[str]:
    return sorted(
        {
            address.strip().lower()
            for _, address in getaddresses([str(value or "")])
            if address.strip()
        }
    )


def _body(value: str | None) -> str:
    return str(value or "").replace("\r\n", "\n").rstrip("\n")


def _matches_exact_message(detail: dict[str, Any], expected: dict[str, Any], rfc822_message_id: str) -> bool:
    headers = _headers(detail.get("payload") or {})
    plain, _ = _message_bodies(detail.get("payload") or {})
    return (
        headers.get("message-id", "").strip() == rfc822_message_id
        and _addresses(headers.get("to")) == sorted(item.lower() for item in expected.get("to") or [])
        and _addresses(headers.get("cc")) == sorted(item.lower() for item in expected.get("cc") or [])
        and headers.get("subject", "") == str(expected.get("subject") or "")
        and _body(plain) == _body(expected.get("text_body"))
    )


def _event_instant(value: Any, *, time_zone: str | None = None) -> datetime | None:
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
            parsed = parsed.replace(tzinfo=ZoneInfo(time_zone))
        except Exception:
            return None
    return parsed.astimezone(timezone.utc)


def _calendar_attendees(event: dict[str, Any]) -> list[str]:
    return sorted(
        {
            str(item.get("email") or "").strip().lower()
            for item in event.get("attendees") or []
            if isinstance(item, dict) and str(item.get("email") or "").strip()
        }
    )


def _matches_exact_event(
    event: dict[str, Any],
    expected: dict[str, Any],
    *,
    event_id: str,
) -> bool:
    if str(event.get("id") or "") != event_id or event.get("status") == "cancelled":
        return False
    if not str(event.get("htmlLink") or "").strip():
        return False
    if str(event.get("summary") or "") != str(expected.get("summary") or ""):
        return False
    for key in ("description", "location"):
        if key in expected and str(event.get(key) or "") != str(expected.get(key) or ""):
            return False

    time_zone = str(expected.get("time_zone") or "").strip() or None
    event_start = event.get("start") if isinstance(event.get("start"), dict) else {}
    event_end = event.get("end") if isinstance(event.get("end"), dict) else {}
    expected_start = _event_instant(expected.get("start"), time_zone=time_zone)
    expected_end = _event_instant(expected.get("end"), time_zone=time_zone)
    actual_start = _event_instant(event_start.get("dateTime"), time_zone=event_start.get("timeZone"))
    actual_end = _event_instant(event_end.get("dateTime"), time_zone=event_end.get("timeZone"))
    if not expected_start or not expected_end or actual_start != expected_start or actual_end != expected_end:
        return False
    if time_zone and (
        str(event_start.get("timeZone") or "") != time_zone
        or str(event_end.get("timeZone") or "") != time_zone
    ):
        return False

    expected_attendees = sorted(
        {
            str(item).strip().lower()
            for item in expected.get("attendees") or []
            if str(item).strip()
        }
    )
    return _calendar_attendees(event) == expected_attendees


async def _gmail_send_once(token: str, *, raw_message: str) -> dict[str, Any]:
    """Cross the non-idempotent Gmail send boundary exactly once."""

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.post(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"raw": raw_message},
            ) as response:
                body = await response.json(content_type=None)
                if response.status in {429, 500, 502, 503, 504}:
                    raise ProviderExecutionUncertain(
                        f"Gmail send returned {response.status} after the send boundary"
                    )
                if response.status not in {200, 201} or not isinstance(body, dict):
                    raise PersonalGoogleProviderRejected(
                        f"Google rejected the Gmail send request ({response.status})"
                    )
                return body
    except ProviderExecutionUncertain:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError) as error:
        raise ProviderExecutionUncertain(
            "Gmail send connection ended without a confirmed provider outcome"
        ) from error


async def _calendar_create_once(
    token: str,
    *,
    calendar_id: str,
    payload: dict[str, Any],
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Attempt one stable-ID Calendar insert; any ambiguous boundary is reconciled by GET."""

    url = (
        "https://www.googleapis.com/calendar/v3/calendars/"
        + quote(calendar_id, safe="")
        + "/events"
    )
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.post(
                url,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=payload,
                params=params,
            ) as response:
                body = await response.json(content_type=None)
                if response.status == 409:
                    raise ProviderExecutionUncertain(
                        "Calendar stable event identity already exists and must be reconciled"
                    )
                if response.status in {429, 500, 502, 503, 504}:
                    raise ProviderExecutionUncertain(
                        f"Calendar create returned {response.status} after the mutation boundary"
                    )
                if response.status not in {200, 201} or not isinstance(body, dict):
                    raise PersonalGoogleProviderRejected(
                        f"Google rejected the Calendar create request ({response.status})"
                    )
                return body
    except ProviderExecutionUncertain:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError) as error:
        raise ProviderExecutionUncertain(
            "Calendar create connection ended without a confirmed provider outcome"
        ) from error


async def _read_message(token: str, message_id: str) -> dict[str, Any]:
    return await request_json(
        "GET",
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/" + quote(message_id, safe=""),
        token,
        params={"format": "full"},
    )


async def _read_calendar_event(
    token: str,
    *,
    calendar_id: str,
    event_id: str,
) -> dict[str, Any]:
    return await request_json(
        "GET",
        "https://www.googleapis.com/calendar/v3/calendars/"
        + quote(calendar_id, safe="")
        + "/events/"
        + quote(event_id, safe=""),
        token,
    )


async def _verify_provider_message(
    token: str,
    *,
    message_id: str,
    expected: dict[str, Any],
    rfc822_message_id: str,
) -> dict[str, Any] | None:
    try:
        detail = await _read_message(token, message_id)
    except Exception:
        return None
    if not _matches_exact_message(detail, expected, rfc822_message_id):
        return None
    return detail


async def _verify_calendar_event(
    token: str,
    *,
    calendar_id: str,
    event_id: str,
    expected: dict[str, Any],
) -> dict[str, Any] | None:
    try:
        event = await _read_calendar_event(
            token,
            calendar_id=calendar_id,
            event_id=event_id,
        )
    except Exception:
        return None
    return event if _matches_exact_event(event, expected, event_id=event_id) else None


async def _reconcile_sent_message(
    token: str,
    *,
    expected: dict[str, Any],
    rfc822_message_id: str,
) -> dict[str, Any] | None:
    """Search sent mail by the stable RFC Message-ID; never trigger another send."""

    try:
        listing = await request_json(
            "GET",
            "https://gmail.googleapis.com/gmail/v1/users/me/messages",
            token,
            params={
                "q": f"in:sent rfc822msgid:{rfc822_message_id}",
                "maxResults": 5,
            },
        )
    except Exception:
        return None
    for item in listing.get("messages") or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        detail = await _verify_provider_message(
            token,
            message_id=str(item["id"]),
            expected=expected,
            rfc822_message_id=rfc822_message_id,
        )
        if detail is not None:
            return detail
    return None


async def _persist_send_intent(
    db: AsyncSession,
    *,
    context: ExecutionContext,
    capability: CapabilitySpec,
    arguments: dict[str, Any],
    connector_id: str,
    rfc822_message_id: str,
    identity: dict[str, str],
) -> str:
    """Persist exact reviewed content identity before the Gmail send POST."""

    approval = await db.scalar(
        select(KernelApproval).where(
            KernelApproval.id == identity["approval_id"],
            KernelApproval.scope_kind == "personal",
            KernelApproval.owner_user_id == context.user_id,
            KernelApproval.workspace_id.is_(None),
            KernelApproval.requested_by_principal_id == context.principal_id,
            KernelApproval.capability_id == capability.id,
        )
    )
    expected_hash = arguments_hash(capability.id, arguments)
    if approval is None or approval.status != "executing" or approval.arguments_hash != expected_hash:
        raise PersonalGoogleProviderRejected(
            "Approved Gmail send no longer matches the executing Kernel approval"
        )

    claim = await db.scalar(
        select(KernelRequestClaim).where(
            KernelRequestClaim.request_id == identity["request_id"],
            KernelRequestClaim.scope_kind == "personal",
            KernelRequestClaim.owner_user_id == context.user_id,
            KernelRequestClaim.principal_id == context.principal_id,
            KernelRequestClaim.capability_id == capability.id,
            KernelRequestClaim.status == "running",
        )
    )
    if claim is None:
        raise PersonalGoogleProviderRejected(
            "Approved Gmail send has no durable Kernel mutation claim"
        )

    claim.response_json = json.dumps(
        {
            "intent": "gmail_send",
            "approval_id": approval.id,
            "approval_arguments_hash": approval.arguments_hash,
            "connector_id": connector_id,
            "rfc822_message_id": rfc822_message_id,
            "request_id": identity["request_id"],
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    await db.flush()
    # Deliberate pre-dispatch commit: the logical send identity and approved content
    # hash survive a process/network failure after Gmail may have accepted the message.
    await db.commit()
    return approval.arguments_hash


async def _persist_calendar_intent(
    db: AsyncSession,
    *,
    context: ExecutionContext,
    capability: CapabilitySpec,
    arguments: dict[str, Any],
    connector_id: str,
    calendar_id: str,
    event_id: str,
    identity: dict[str, str],
) -> str:
    """Persist exact approved event identity before crossing Calendar's insert boundary."""

    approval = await db.scalar(
        select(KernelApproval).where(
            KernelApproval.id == identity["approval_id"],
            KernelApproval.scope_kind == "personal",
            KernelApproval.owner_user_id == context.user_id,
            KernelApproval.workspace_id.is_(None),
            KernelApproval.requested_by_principal_id == context.principal_id,
            KernelApproval.capability_id == capability.id,
        )
    )
    expected_hash = arguments_hash(capability.id, arguments)
    if approval is None or approval.status != "executing" or approval.arguments_hash != expected_hash:
        raise PersonalGoogleProviderRejected(
            "Approved Calendar event no longer matches the executing Kernel approval"
        )

    claim = await db.scalar(
        select(KernelRequestClaim).where(
            KernelRequestClaim.request_id == identity["request_id"],
            KernelRequestClaim.scope_kind == "personal",
            KernelRequestClaim.owner_user_id == context.user_id,
            KernelRequestClaim.principal_id == context.principal_id,
            KernelRequestClaim.capability_id == capability.id,
            KernelRequestClaim.status == "running",
        )
    )
    if claim is None:
        raise PersonalGoogleProviderRejected(
            "Approved Calendar event has no durable Kernel mutation claim"
        )

    claim.response_json = json.dumps(
        {
            "intent": "calendar_create",
            "approval_id": approval.id,
            "approval_arguments_hash": approval.arguments_hash,
            "connector_id": connector_id,
            "calendar_id": calendar_id,
            "event_id": event_id,
            "request_id": identity["request_id"],
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    await db.flush()
    await db.commit()
    return approval.arguments_hash


def _calendar_result(
    *,
    connector,
    calendar_id: str,
    event_id: str,
    event: dict[str, Any],
    provider_status: str,
    approved_hash: str,
) -> CapabilityExecutionResult:
    return CapabilityExecutionResult(
        value={
            "calendar_id": calendar_id,
            "event_id": event_id,
            "event_link": str(event.get("htmlLink") or ""),
            "event": event,
            "provider_account": connector.provider_account_id,
            "provider_status": provider_status,
            "verification_status": "read_back",
            "approval_arguments_hash": approved_hash,
        },
        resource_type="calendar_event",
        resource_id=event_id,
        event_payload={
            "connector_id": connector.id,
            "calendar_id": calendar_id,
            "event_id": event_id,
            "verification_status": "read_back",
        },
    )


class ReliablePersonalGoogleProvider(PersonalGoogleCompletionProvider):
    """Production Personal Google provider with mutation identity and reconciliation."""

    async def execute(
        self,
        db: AsyncSession,
        *,
        context: ExecutionContext,
        capability: CapabilitySpec,
        arguments: dict[str, Any],
        minimum_context: dict[str, Any],
    ) -> CapabilityExecutionResult:
        if capability.id == GMAIL_READ_CAPABILITY:
            del minimum_context
            if not context.is_personal or not context.user_id:
                raise PermissionError("Personal Gmail read requires Personal authority")
            connector = await _connector(
                db,
                context,
                capability.id,
                str(arguments.get("connector_id") or "") or None,
            )
            token = await access_token(db, connector)
            message_id = str(arguments["message_id"])
            detail = await _read_message(token, message_id)
            headers = _headers(detail.get("payload") or {})
            plain, rich = _message_bodies(detail.get("payload") or {})
            internal_date = str(detail.get("internalDate") or "").strip()
            internal_date_ms = int(internal_date) if internal_date.isdigit() else None
            return CapabilityExecutionResult(
                value={
                    "id": detail.get("id"),
                    "thread_id": detail.get("threadId"),
                    "from": headers.get("from"),
                    "to": headers.get("to"),
                    "cc": headers.get("cc"),
                    "subject": headers.get("subject"),
                    "date": headers.get("date"),
                    "snippet": str(detail.get("snippet") or "")[:1000],
                    "text_body": plain,
                    "html_body": rich,
                    "label_ids": detail.get("labelIds", []),
                    "rfc822_message_id": headers.get("message-id"),
                    "in_reply_to": headers.get("in-reply-to"),
                    "references": headers.get("references"),
                    "internal_date_ms": internal_date_ms,
                },
                resource_type="gmail_message",
                resource_id=str(detail.get("id") or message_id),
            )

        if capability.id == CALENDAR_CREATE_CAPABILITY:
            del minimum_context
            if not context.is_personal or not context.user_id:
                raise PermissionError("Personal Calendar create requires Personal authority")
            identity = _kernel_execution(
                context,
                expected_capability=CALENDAR_CREATE_CAPABILITY,
            )
            canonical = canonical_arguments(capability.id, arguments)
            connector = await _connector(
                db,
                context,
                capability.id,
                str(canonical.get("connector_id") or "") or None,
            )
            token = await access_token(db, connector)
            calendar_id = _calendar_id(connector, canonical)
            event_id = _stable_calendar_event_id(
                owner_user_id=context.user_id,
                request_id=identity["request_id"],
            )
            approved_hash = await _persist_calendar_intent(
                db,
                context=context,
                capability=capability,
                arguments=canonical,
                connector_id=connector.id,
                calendar_id=calendar_id,
                event_id=event_id,
                identity=identity,
            )
            payload = _event_payload(canonical)
            payload["id"] = event_id
            params: dict[str, Any] = {}
            if canonical.get("add_video_conference"):
                payload["conferenceData"] = {
                    "createRequest": {
                        "requestId": event_id,
                        "conferenceSolutionKey": {"type": "hangoutsMeet"},
                    }
                }
                params["conferenceDataVersion"] = 1

            try:
                await _calendar_create_once(
                    token,
                    calendar_id=calendar_id,
                    payload=payload,
                    params=params or None,
                )
                provider_status = "accepted"
            except ProviderExecutionUncertain as create_error:
                reconciled = await _verify_calendar_event(
                    token,
                    calendar_id=calendar_id,
                    event_id=event_id,
                    expected=canonical,
                )
                if reconciled is None:
                    raise ProviderExecutionUncertain(
                        "Calendar creation is uncertain; the stable event identity must be reconciled before any retry",
                        details={
                            "provider": "google_calendar",
                            "calendar_id": calendar_id,
                            "event_id": event_id,
                            "request_id": identity["request_id"],
                            "approval_arguments_hash": approved_hash,
                            "recovery": "read_calendar_event_by_stable_event_id_before_retry",
                        },
                    ) from create_error
                return _calendar_result(
                    connector=connector,
                    calendar_id=calendar_id,
                    event_id=event_id,
                    event=reconciled,
                    provider_status="reconciled_created",
                    approved_hash=approved_hash,
                )

            verified = await _verify_calendar_event(
                token,
                calendar_id=calendar_id,
                event_id=event_id,
                expected=canonical,
            )
            if verified is None:
                raise ProviderExecutionUncertain(
                    "Calendar event was acknowledged but could not be independently verified",
                    details={
                        "provider": "google_calendar",
                        "calendar_id": calendar_id,
                        "event_id": event_id,
                        "request_id": identity["request_id"],
                        "approval_arguments_hash": approved_hash,
                        "recovery": "read_calendar_event_by_stable_event_id_before_retry",
                    },
                )
            return _calendar_result(
                connector=connector,
                calendar_id=calendar_id,
                event_id=event_id,
                event=verified,
                provider_status=provider_status,
                approved_hash=approved_hash,
            )

        if capability.id != GMAIL_SEND_CAPABILITY:
            return await super().execute(
                db,
                context=context,
                capability=capability,
                arguments=arguments,
                minimum_context=minimum_context,
            )

        del minimum_context
        if not context.is_personal or not context.user_id:
            raise PermissionError("Personal Gmail send requires Personal authority")

        identity = _kernel_execution(
            context,
            expected_capability=GMAIL_SEND_CAPABILITY,
        )
        canonical = canonical_arguments(capability.id, arguments)
        connector = await _connector(
            db,
            context,
            capability.id,
            str(canonical.get("connector_id") or "") or None,
        )
        token = await access_token(db, connector)
        rfc822_message_id = _stable_rfc822_message_id(
            owner_user_id=context.user_id,
            request_id=identity["request_id"],
        )
        approved_hash = await _persist_send_intent(
            db,
            context=context,
            capability=capability,
            arguments=canonical,
            connector_id=connector.id,
            rfc822_message_id=rfc822_message_id,
            identity=identity,
        )
        raw_message = _raw_message(
            _email_message(canonical, rfc822_message_id=rfc822_message_id)
        )
        readable = bool(connector_scopes(connector) & GMAIL_READ_SCOPES)

        try:
            body = await _gmail_send_once(token, raw_message=raw_message)
        except ProviderExecutionUncertain as send_error:
            reconciled = None
            if readable:
                reconciled = await _reconcile_sent_message(
                    token,
                    expected=canonical,
                    rfc822_message_id=rfc822_message_id,
                )
            if reconciled is None:
                raise ProviderExecutionUncertain(
                    "Gmail delivery is uncertain; the same logical send must be reconciled before any retry",
                    details={
                        "provider": "gmail",
                        "rfc822_message_id": rfc822_message_id,
                        "request_id": identity["request_id"],
                        "approval_arguments_hash": approved_hash,
                        "recovery": "search_sent_mail_by_rfc822_message_id_before_retry",
                    },
                ) from send_error
            message_id = str(reconciled.get("id") or "")
            return CapabilityExecutionResult(
                value={
                    "message_id": message_id,
                    "thread_id": reconciled.get("threadId"),
                    "provider_account": connector.provider_account_id,
                    "provider_status": "reconciled_sent",
                    "verification_status": "read_back",
                    "rfc822_message_id": rfc822_message_id,
                    "approval_arguments_hash": approved_hash,
                },
                resource_type="gmail_message",
                resource_id=message_id or None,
                event_payload={
                    "connector_id": connector.id,
                    "provider_account": connector.provider_account_id,
                    "verification_status": "read_back",
                },
            )

        message_id = str(body.get("id") or "")
        verification_status = "provider_acknowledged"
        if readable and message_id:
            verified = await _verify_provider_message(
                token,
                message_id=message_id,
                expected=canonical,
                rfc822_message_id=rfc822_message_id,
            )
            if verified is not None:
                verification_status = "read_back"
                body = verified

        return CapabilityExecutionResult(
            value={
                "message_id": message_id,
                "thread_id": body.get("threadId"),
                "provider_account": connector.provider_account_id,
                "provider_status": "accepted",
                "verification_status": verification_status,
                "rfc822_message_id": rfc822_message_id,
                "approval_arguments_hash": approved_hash,
            },
            resource_type="gmail_message",
            resource_id=message_id or None,
            event_payload={
                "connector_id": connector.id,
                "provider_account": connector.provider_account_id,
                "verification_status": verification_status,
            },
        )
