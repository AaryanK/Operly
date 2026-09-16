from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from email.message import EmailMessage
from email.utils import getaddresses
from typing import Any
from urllib.parse import quote

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
    _connector,
    _headers,
    _message_bodies,
    access_token,
    connector_scopes,
    request_json,
)
from packages.security.execution_context import ExecutionContext


GMAIL_SEND_CAPABILITY = "google.gmail.send_email"
GMAIL_READ_CAPABILITY = "google.gmail.read_message"


def _kernel_execution(context: ExecutionContext) -> dict[str, str]:
    raw = dict((context.metadata or {}).get("_kernel_execution") or {})
    identity = {
        "request_id": str(raw.get("request_id") or "").strip(),
        "approval_id": str(raw.get("approval_id") or "").strip(),
        "run_id": str(raw.get("run_id") or "").strip(),
        "capability_id": str(raw.get("capability_id") or "").strip(),
    }
    if not identity["request_id"] or not identity["approval_id"]:
        raise PersonalGoogleProviderRejected(
            "Approved Gmail send is missing trusted Kernel request identity"
        )
    if identity["capability_id"] != GMAIL_SEND_CAPABILITY:
        raise PersonalGoogleProviderRejected("Trusted Kernel capability identity is inconsistent")
    return identity


def _stable_rfc822_message_id(*, owner_user_id: str, request_id: str) -> str:
    digest = hashlib.sha256(f"{owner_user_id}\0{request_id}".encode("utf-8")).hexdigest()[:40]
    return f"<operly-{digest}@operly.invalid>"


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


async def _read_message(token: str, message_id: str) -> dict[str, Any]:
    return await request_json(
        "GET",
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/" + quote(message_id, safe=""),
        token,
        params={"format": "full"},
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


class ReliablePersonalGoogleProvider(PersonalGoogleCompletionProvider):
    """Production Personal Google provider with approval-bound Gmail send recovery."""

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

        identity = _kernel_execution(context)
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