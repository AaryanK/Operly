from __future__ import annotations

import asyncio
import base64
import json
import os
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Any
from urllib.parse import quote

import aiohttp
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.connectors.secrets import read_secret, update_secret
from packages.database.connector_models import TenantConnector
from packages.kernel.contracts import CapabilityExecutionResult, CapabilityRisk, CapabilitySpec
from packages.security.execution_context import ExecutionContext


PROVIDER_ID = "operly.google"

GMAIL_SEND = "https://www.googleapis.com/auth/gmail.send"
GMAIL_READONLY = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_MODIFY = "https://www.googleapis.com/auth/gmail.modify"
CALENDAR = "https://www.googleapis.com/auth/calendar.events"
CALENDAR_FREEBUSY = "https://www.googleapis.com/auth/calendar.freebusy"
CALENDAR_LIST_READONLY = "https://www.googleapis.com/auth/calendar.calendarlist.readonly"

GMAIL_READ_SCOPES = {GMAIL_READONLY, GMAIL_MODIFY}
GMAIL_SEND_SCOPES = {GMAIL_SEND, GMAIL_MODIFY}


class GoogleConnectorRequired(LookupError):
    pass


class GoogleProviderRejected(RuntimeError):
    pass


def _object(properties: dict[str, Any], *, required: list[str] | None = None, additional: bool = False) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": additional,
    }


def _array(item: dict[str, Any], *, min_items: int | None = None, max_items: int | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "array", "items": item}
    if min_items is not None:
        schema["minItems"] = min_items
    if max_items is not None:
        schema["maxItems"] = max_items
    return schema


def _capability(
    capability_id: str,
    display_name: str,
    description: str,
    *,
    permission: str,
    input_schema: dict[str, Any] | None = None,
    output_schema: dict[str, Any] | None = None,
    risk: CapabilityRisk = CapabilityRisk.READ_ONLY,
    approval: bool = False,
    reversible: bool = False,
    emits: tuple[str, ...] = (),
    tags: tuple[str, ...] = (),
) -> CapabilitySpec:
    return CapabilitySpec(
        id=capability_id,
        version="1.0.0",
        display_name=display_name,
        description=description,
        provider_id=PROVIDER_ID,
        scopes=frozenset({"workspace"}),
        input_schema=input_schema or _object({}),
        output_schema=output_schema or _object({}, additional=True),
        permissions=(permission,),
        risk=risk,
        approval_required=approval,
        reversible=reversible,
        aliases=(),
        emits=emits,
        tags=frozenset(("google", "connector", "external", *tags)),
        resource_scope="workspace",
    )


def workspace_google_capabilities() -> tuple[CapabilitySpec, ...]:
    recipients = _array({"type": "string"}, min_items=1, max_items=20)
    optional_recipients = _array({"type": "string"}, max_items=20)
    return (
        _capability(
            "google.connection.status",
            "Read Google connection status",
            "Inspect connected workspace Google accounts, granted scopes, and health without exposing credentials.",
            permission="integrations:read",
            output_schema=_object({"connections": _array(_object({}, additional=True))}, required=["connections"]),
            tags=("status", "read"),
        ),
        _capability(
            "google.gmail.search",
            "Search Gmail",
            "Search the connected workspace Gmail account using Gmail search syntax.",
            permission="messaging:read",
            input_schema=_object(
                {
                    "query": {"type": "string", "minLength": 1, "maxLength": 1000},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                },
                required=["query"],
            ),
            output_schema=_object({"messages": _array(_object({}, additional=True))}, required=["messages"]),
            tags=("gmail", "mail", "search", "read"),
        ),
        _capability(
            "google.gmail.read_message",
            "Read Gmail message",
            "Read one Gmail message by provider message ID from the connected workspace account.",
            permission="messaging:read",
            input_schema=_object({"message_id": {"type": "string", "minLength": 1, "maxLength": 200}}, required=["message_id"]),
            output_schema=_object({}, additional=True),
            tags=("gmail", "mail", "read"),
        ),
        _capability(
            "google.gmail.create_draft",
            "Create Gmail draft",
            "Create a plain-text Gmail draft. Drafting does not send the message.",
            permission="messaging:draft",
            input_schema=_object(
                {
                    "to": recipients,
                    "cc": optional_recipients,
                    "bcc": optional_recipients,
                    "subject": {"type": "string", "maxLength": 998},
                    "text_body": {"type": "string", "maxLength": 50000},
                },
                required=["to", "subject"],
            ),
            output_schema=_object({}, additional=True),
            risk=CapabilityRisk.LOW,
            reversible=True,
            emits=("gmail.draft.created",),
            tags=("gmail", "mail", "draft", "write"),
        ),
        _capability(
            "google.gmail.send_email",
            "Send Gmail message",
            "Send a plain-text Gmail message to explicit recipients after exact-invocation approval.",
            permission="messaging:send",
            input_schema=_object(
                {
                    "to": recipients,
                    "cc": optional_recipients,
                    "bcc": optional_recipients,
                    "subject": {"type": "string", "maxLength": 998},
                    "text_body": {"type": "string", "maxLength": 50000},
                    "reply_to": {"type": "string", "maxLength": 320},
                },
                required=["to", "subject"],
            ),
            output_schema=_object({}, additional=True),
            risk=CapabilityRisk.HIGH,
            approval=True,
            reversible=False,
            emits=("gmail.message.sent",),
            tags=("gmail", "mail", "send", "write"),
        ),
        _capability(
            "google.gmail.modify_labels",
            "Modify Gmail labels",
            "Apply or remove Gmail labels on one message after approval.",
            permission="messaging:write",
            input_schema=_object(
                {
                    "message_id": {"type": "string", "minLength": 1, "maxLength": 200},
                    "add_label_ids": _array({"type": "string"}, max_items=20),
                    "remove_label_ids": _array({"type": "string"}, max_items=20),
                },
                required=["message_id"],
            ),
            output_schema=_object({}, additional=True),
            risk=CapabilityRisk.MEDIUM,
            approval=True,
            reversible=True,
            emits=("gmail.labels.modified",),
            tags=("gmail", "mail", "labels", "write"),
        ),
        _capability(
            "google.calendar.list_calendars",
            "List Google calendars",
            "List calendars visible to the connected workspace Google account.",
            permission="calendar:read",
            output_schema=_object({"calendars": _array(_object({}, additional=True))}, required=["calendars"]),
            tags=("calendar", "read"),
        ),
        _capability(
            "google.calendar.list_events",
            "List Google Calendar events",
            "List events from a Google Calendar within an explicit time window.",
            permission="calendar:read",
            input_schema=_object(
                {
                    "time_min": {"type": "string", "minLength": 1, "maxLength": 80},
                    "time_max": {"type": "string", "minLength": 1, "maxLength": 80},
                    "query": {"type": "string", "maxLength": 500},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                    "calendar_id": {"type": "string", "maxLength": 500},
                },
                required=["time_min", "time_max"],
            ),
            output_schema=_object({"calendar_id": {"type": "string"}, "events": _array(_object({}, additional=True))}, required=["calendar_id", "events"]),
            tags=("calendar", "events", "read"),
        ),
        _capability(
            "google.calendar.freebusy",
            "Check Google Calendar free/busy",
            "Check free/busy blocks for explicit Google calendar IDs in a time window.",
            permission="calendar:read",
            input_schema=_object(
                {
                    "time_min": {"type": "string", "minLength": 1, "maxLength": 80},
                    "time_max": {"type": "string", "minLength": 1, "maxLength": 80},
                    "calendar_ids": _array({"type": "string"}, min_items=1, max_items=20),
                    "time_zone": {"type": "string", "maxLength": 100},
                },
                required=["time_min", "time_max", "calendar_ids"],
            ),
            output_schema=_object({}, additional=True),
            tags=("calendar", "availability", "read"),
        ),
        _capability(
            "google.calendar.create_event",
            "Create Google Calendar event",
            "Create a Google Calendar event with explicit time, attendees, and optional Meet conference after approval.",
            permission="calendar:write",
            input_schema=_object(
                {
                    "summary": {"type": "string", "minLength": 1, "maxLength": 500},
                    "start": {"type": "string", "minLength": 1, "maxLength": 80},
                    "end": {"type": "string", "minLength": 1, "maxLength": 80},
                    "attendees": _array({"type": "string"}, max_items=50),
                    "description": {"type": "string", "maxLength": 10000},
                    "location": {"type": "string", "maxLength": 1000},
                    "time_zone": {"type": "string", "maxLength": 100},
                    "calendar_id": {"type": "string", "maxLength": 500},
                    "add_video_conference": {"type": "boolean"},
                },
                required=["summary", "start", "end"],
            ),
            output_schema=_object({}, additional=True),
            risk=CapabilityRisk.HIGH,
            approval=True,
            reversible=True,
            emits=("calendar.event.created",),
            tags=("calendar", "events", "write"),
        ),
        _capability(
            "google.calendar.update_event",
            "Update Google Calendar event",
            "Patch an existing Google Calendar event after exact-invocation approval.",
            permission="calendar:write",
            input_schema=_object(
                {
                    "event_id": {"type": "string", "minLength": 1, "maxLength": 500},
                    "calendar_id": {"type": "string", "maxLength": 500},
                    "summary": {"type": "string", "maxLength": 500},
                    "start": {"type": "string", "maxLength": 80},
                    "end": {"type": "string", "maxLength": 80},
                    "description": {"type": "string", "maxLength": 10000},
                    "location": {"type": "string", "maxLength": 1000},
                    "attendees": _array({"type": "string"}, max_items=50),
                    "time_zone": {"type": "string", "maxLength": 100},
                },
                required=["event_id"],
            ),
            output_schema=_object({}, additional=True),
            risk=CapabilityRisk.HIGH,
            approval=True,
            reversible=True,
            emits=("calendar.event.updated",),
            tags=("calendar", "events", "write"),
        ),
        _capability(
            "google.calendar.delete_event",
            "Delete Google Calendar event",
            "Delete an existing Google Calendar event after explicit approval.",
            permission="calendar:write",
            input_schema=_object(
                {
                    "event_id": {"type": "string", "minLength": 1, "maxLength": 500},
                    "calendar_id": {"type": "string", "maxLength": 500},
                },
                required=["event_id"],
            ),
            output_schema=_object({"event_id": {"type": "string"}, "calendar_id": {"type": "string"}, "deleted": {"type": "boolean"}}, required=["event_id", "calendar_id", "deleted"]),
            risk=CapabilityRisk.HIGH,
            approval=True,
            reversible=False,
            emits=("calendar.event.deleted",),
            tags=("calendar", "events", "delete", "write"),
        ),
    )


def _scopes(connector: TenantConnector) -> set[str]:
    try:
        return set(json.loads(connector.granted_scopes_json or "[]"))
    except (TypeError, json.JSONDecodeError):
        return set()


async def _google_connectors(db: AsyncSession, tenant_id: str) -> list[TenantConnector]:
    return list(
        (
            await db.scalars(
                select(TenantConnector).where(
                    TenantConnector.tenant_id == tenant_id,
                    TenantConnector.provider == "google",
                    TenantConnector.enabled.is_(True),
                    TenantConnector.status == "connected",
                )
            )
        ).all()
    )


async def _connector(
    db: AsyncSession,
    tenant_id: str,
    *,
    required: set[str] | None = None,
    acceptable: set[str] | None = None,
) -> TenantConnector:
    for row in await _google_connectors(db, tenant_id):
        granted = _scopes(row)
        if required and required.issubset(granted):
            return row
        if acceptable and granted & acceptable:
            return row
        if not required and not acceptable:
            return row
    raise GoogleConnectorRequired("Connect Google and grant the required workspace scope")


async def _access_token(db: AsyncSession, connector: TenantConnector) -> str:
    if not connector.credential_reference:
        raise GoogleConnectorRequired("Google connection has no credential reference")
    secret = await read_secret(db, connector.tenant_id, connector.credential_reference)
    now = datetime.now(timezone.utc).timestamp()
    access_token = str(secret.get("access_token") or "")
    if access_token and float(secret.get("expires_at", 0)) > now + 60:
        return access_token
    refresh_token = str(secret.get("refresh_token") or "")
    if not refresh_token:
        raise GoogleConnectorRequired("Google connection expired and must be reconnected")
    data = {
        "client_id": os.environ["GOOGLE_OAUTH_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
        async with session.post("https://oauth2.googleapis.com/token", data=data) as response:
            body = await response.json(content_type=None)
    if response.status != 200:
        raise GoogleProviderRejected("Google authorization refresh was rejected")
    secret.update(body)
    secret["expires_at"] = now + int(body.get("expires_in", 3600))
    await update_secret(db, connector.tenant_id, connector.credential_reference, secret)
    return str(secret["access_token"])


async def _request_json(
    method: str,
    url: str,
    token: str,
    payload: dict[str, Any] | None = None,
    *,
    params: dict[str, Any] | None = None,
    expected_statuses: tuple[int, ...] = (200, 201, 204),
    retries: int = 2,
) -> dict[str, Any]:
    for attempt in range(retries + 1):
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
                async with session.request(
                    method,
                    url,
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                    json=payload,
                    params=params,
                ) as response:
                    body = {} if response.status == 204 else await response.json(content_type=None)
                    if response.status in {429, 500, 502, 503, 504} and attempt < retries:
                        await asyncio.sleep(0.25 * (attempt + 1))
                        continue
                    if response.status not in expected_statuses:
                        raise GoogleProviderRejected(f"Google rejected the request ({response.status})")
                    return body if isinstance(body, dict) else {}
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            if attempt >= retries:
                raise GoogleProviderRejected("Google request failed") from error
            await asyncio.sleep(0.25 * (attempt + 1))
    raise GoogleProviderRejected("Google request failed")


def _email_message(arguments: dict[str, Any]) -> EmailMessage:
    recipients = [str(item).strip() for item in arguments.get("to", []) if str(item).strip()]
    if not recipients:
        raise ValueError("At least one recipient is required")
    message = EmailMessage()
    message["To"] = ", ".join(recipients)
    cc = [str(item).strip() for item in arguments.get("cc", []) if str(item).strip()]
    bcc = [str(item).strip() for item in arguments.get("bcc", []) if str(item).strip()]
    if cc:
        message["Cc"] = ", ".join(cc)
    if bcc:
        message["Bcc"] = ", ".join(bcc)
    message["Subject"] = str(arguments.get("subject") or "").strip()[:998]
    if arguments.get("reply_to"):
        message["Reply-To"] = str(arguments["reply_to"]).strip()
    message.set_content(str(arguments.get("text_body") or " ")[:50000])
    return message


def _raw_message(message: EmailMessage) -> str:
    return base64.urlsafe_b64encode(message.as_bytes()).decode()


def _headers(payload: dict[str, Any]) -> dict[str, str]:
    return {
        str(item.get("name") or "").lower(): str(item.get("value") or "")
        for item in payload.get("headers", [])
        if isinstance(item, dict)
    }


def _decode_gmail_data(value: str | None) -> str:
    if not value:
        return ""
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode((value + padding).encode()).decode("utf-8", errors="replace")
    except (ValueError, UnicodeDecodeError):
        return ""


def _message_bodies(payload: dict[str, Any]) -> tuple[str, str]:
    plain = ""
    rich = ""
    mime = payload.get("mimeType")
    body = payload.get("body") or {}
    if mime == "text/plain":
        plain = _decode_gmail_data(body.get("data"))
    elif mime == "text/html":
        rich = _decode_gmail_data(body.get("data"))
    for part in payload.get("parts") or []:
        if not isinstance(part, dict):
            continue
        child_plain, child_rich = _message_bodies(part)
        plain = plain or child_plain
        rich = rich or child_rich
    return plain[:20000], rich[:40000]


def _calendar_id(connector: TenantConnector, arguments: dict[str, Any]) -> str:
    try:
        configured = json.loads(connector.configuration_json or "{}").get("calendar_id", "primary")
    except (TypeError, json.JSONDecodeError):
        configured = "primary"
    return str(arguments.get("calendar_id") or configured or "primary")


class WorkspaceGoogleProvider:
    async def execute(
        self,
        db: AsyncSession,
        *,
        context: ExecutionContext,
        capability: CapabilitySpec,
        arguments: dict[str, Any],
        minimum_context: dict[str, Any],
    ) -> CapabilityExecutionResult:
        del minimum_context
        if not context.workspace_id:
            raise PermissionError("Google workspace capability requires workspace authority")
        tenant_id = context.workspace_id
        capability_id = capability.id

        if capability_id == "google.connection.status":
            rows = await _google_connectors(db, tenant_id)
            return CapabilityExecutionResult(
                value={
                    "connections": [
                        {
                            "id": row.id,
                            "display_name": row.display_name,
                            "provider_account_id": row.provider_account_id,
                            "status": row.status,
                            "enabled": row.enabled,
                            "health_status": row.health_status,
                            "scopes": sorted(_scopes(row)),
                            "last_health_check": row.last_health_check.isoformat() if row.last_health_check else None,
                        }
                        for row in rows
                    ]
                },
                resource_type="google_connection",
                resource_id=rows[0].id if len(rows) == 1 else None,
            )

        if capability_id == "google.gmail.search":
            connector = await _connector(db, tenant_id, acceptable=GMAIL_READ_SCOPES)
            token = await _access_token(db, connector)
            limit = max(1, min(int(arguments.get("limit") or 10), 20))
            listing = await _request_json(
                "GET",
                "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                token,
                params={"q": arguments["query"], "maxResults": limit},
            )
            messages = []
            for item in (listing.get("messages") or [])[:limit]:
                detail = await _request_json(
                    "GET",
                    f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{quote(str(item['id']), safe='')}",
                    token,
                    params={"format": "metadata"},
                )
                headers = _headers(detail.get("payload") or {})
                messages.append(
                    {
                        "id": detail.get("id"),
                        "thread_id": detail.get("threadId"),
                        "from": headers.get("from"),
                        "to": headers.get("to"),
                        "subject": headers.get("subject"),
                        "date": headers.get("date"),
                        "snippet": str(detail.get("snippet") or "")[:500],
                        "label_ids": detail.get("labelIds", []),
                    }
                )
            return CapabilityExecutionResult(value={"messages": messages}, resource_type="gmail_mailbox", resource_id=connector.id)

        if capability_id == "google.gmail.read_message":
            connector = await _connector(db, tenant_id, acceptable=GMAIL_READ_SCOPES)
            token = await _access_token(db, connector)
            message_id = str(arguments["message_id"])
            detail = await _request_json(
                "GET",
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{quote(message_id, safe='')}",
                token,
                params={"format": "full"},
            )
            headers = _headers(detail.get("payload") or {})
            plain, rich = _message_bodies(detail.get("payload") or {})
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
                },
                resource_type="gmail_message",
                resource_id=str(detail.get("id") or message_id),
            )

        if capability_id in {"google.gmail.create_draft", "google.gmail.send_email"}:
            acceptable = GMAIL_SEND_SCOPES if capability_id.endswith("send_email") else None
            connector = await _connector(
                db,
                tenant_id,
                acceptable=acceptable,
                required={GMAIL_MODIFY} if capability_id.endswith("create_draft") else None,
            )
            token = await _access_token(db, connector)
            message = _email_message(arguments)
            if capability_id.endswith("create_draft"):
                body = await _request_json(
                    "POST",
                    "https://gmail.googleapis.com/gmail/v1/users/me/drafts",
                    token,
                    {"message": {"raw": _raw_message(message)}},
                    retries=0,
                )
                draft_id = str(body.get("id") or "")
                return CapabilityExecutionResult(
                    value={
                        "draft_id": draft_id,
                        "message_id": (body.get("message") or {}).get("id"),
                        "recipients": list(arguments["to"]),
                        "subject": arguments["subject"],
                    },
                    resource_type="gmail_draft",
                    resource_id=draft_id or None,
                    event_payload={"draft_id": draft_id, "subject": arguments["subject"]},
                )
            body = await _request_json(
                "POST",
                "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                token,
                {"raw": _raw_message(message)},
                retries=0,
            )
            message_id = str(body.get("id") or "")
            if not message_id:
                raise GoogleProviderRejected("Gmail did not return a message ID")
            return CapabilityExecutionResult(
                value={
                    "message_id": message_id,
                    "thread_id": body.get("threadId"),
                    "recipients": list(arguments["to"]),
                    "subject": arguments["subject"],
                    "provider_status": "accepted",
                },
                resource_type="gmail_message",
                resource_id=message_id,
                event_payload={"message_id": message_id, "recipients": list(arguments["to"]), "subject": arguments["subject"]},
            )

        if capability_id == "google.gmail.modify_labels":
            connector = await _connector(db, tenant_id, required={GMAIL_MODIFY})
            token = await _access_token(db, connector)
            message_id = str(arguments["message_id"])
            body = await _request_json(
                "POST",
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{quote(message_id, safe='')}/modify",
                token,
                {
                    "addLabelIds": list(arguments.get("add_label_ids") or []),
                    "removeLabelIds": list(arguments.get("remove_label_ids") or []),
                },
            )
            resolved_id = str(body.get("id") or message_id)
            return CapabilityExecutionResult(
                value={"message_id": resolved_id, "label_ids": body.get("labelIds", [])},
                resource_type="gmail_message",
                resource_id=resolved_id,
                event_payload={"message_id": resolved_id},
            )

        if capability_id == "google.calendar.list_calendars":
            connector = await _connector(db, tenant_id, required={CALENDAR_LIST_READONLY})
            token = await _access_token(db, connector)
            body = await _request_json(
                "GET",
                "https://www.googleapis.com/calendar/v3/users/me/calendarList",
                token,
            )
            return CapabilityExecutionResult(
                value={
                    "calendars": [
                        {
                            "id": item.get("id"),
                            "summary": item.get("summary"),
                            "primary": item.get("primary", False),
                            "access_role": item.get("accessRole"),
                            "time_zone": item.get("timeZone"),
                        }
                        for item in body.get("items", [])[:50]
                    ]
                },
                resource_type="google_calendar_collection",
                resource_id=connector.id,
            )

        if capability_id == "google.calendar.freebusy":
            connector = await _connector(db, tenant_id, required={CALENDAR_FREEBUSY})
            token = await _access_token(db, connector)
            payload = {
                "timeMin": arguments["time_min"],
                "timeMax": arguments["time_max"],
                "items": [{"id": item} for item in arguments["calendar_ids"]],
            }
            if arguments.get("time_zone"):
                payload["timeZone"] = arguments["time_zone"]
            body = await _request_json(
                "POST",
                "https://www.googleapis.com/calendar/v3/freeBusy",
                token,
                payload,
            )
            return CapabilityExecutionResult(
                value={"calendars": body.get("calendars", {}), "groups": body.get("groups", {})},
                resource_type="google_calendar_collection",
                resource_id=connector.id,
            )

        if capability_id.startswith("google.calendar."):
            connector = await _connector(db, tenant_id, required={CALENDAR})
            token = await _access_token(db, connector)
            calendar_id = _calendar_id(connector, arguments)
            encoded_calendar = quote(calendar_id, safe="")

            if capability_id == "google.calendar.list_events":
                params: dict[str, Any] = {
                    "timeMin": arguments["time_min"],
                    "timeMax": arguments["time_max"],
                    "singleEvents": "true",
                    "orderBy": "startTime",
                    "maxResults": max(1, min(int(arguments.get("limit") or 20), 50)),
                }
                if arguments.get("query"):
                    params["q"] = arguments["query"]
                body = await _request_json(
                    "GET",
                    f"https://www.googleapis.com/calendar/v3/calendars/{encoded_calendar}/events",
                    token,
                    params=params,
                )
                events = [
                    {
                        "id": item.get("id"),
                        "summary": item.get("summary"),
                        "description": item.get("description"),
                        "location": item.get("location"),
                        "start": item.get("start"),
                        "end": item.get("end"),
                        "attendees": [
                            {"email": person.get("email"), "response_status": person.get("responseStatus")}
                            for person in item.get("attendees", [])
                        ],
                        "status": item.get("status"),
                        "html_link": item.get("htmlLink"),
                        "hangout_link": item.get("hangoutLink"),
                    }
                    for item in body.get("items", [])
                ]
                return CapabilityExecutionResult(
                    value={"calendar_id": calendar_id, "events": events},
                    resource_type="google_calendar",
                    resource_id=calendar_id,
                )

            if capability_id == "google.calendar.create_event":
                start: dict[str, Any] = {"dateTime": arguments["start"]}
                end: dict[str, Any] = {"dateTime": arguments["end"]}
                if arguments.get("time_zone"):
                    start["timeZone"] = arguments["time_zone"]
                    end["timeZone"] = arguments["time_zone"]
                payload: dict[str, Any] = {
                    "summary": arguments["summary"],
                    "start": start,
                    "end": end,
                    "attendees": [{"email": item} for item in arguments.get("attendees", [])],
                }
                if arguments.get("description"):
                    payload["description"] = arguments["description"]
                if arguments.get("location"):
                    payload["location"] = arguments["location"]
                params = None
                if arguments.get("add_video_conference"):
                    request_id = str(context.conversation_id or context.principal_id or "operly").replace("-", "")[:32]
                    payload["conferenceData"] = {
                        "createRequest": {
                            "requestId": request_id,
                            "conferenceSolutionKey": {"type": "hangoutsMeet"},
                        }
                    }
                    params = {"conferenceDataVersion": "1"}
                body = await _request_json(
                    "POST",
                    f"https://www.googleapis.com/calendar/v3/calendars/{encoded_calendar}/events",
                    token,
                    payload,
                    params=params,
                    retries=0,
                )
                event_id = str(body.get("id") or "")
                if not event_id:
                    raise GoogleProviderRejected("Google Calendar did not return an event ID")
                return CapabilityExecutionResult(
                    value={
                        "event_id": event_id,
                        "calendar_id": calendar_id,
                        "provider_status": body.get("status", "confirmed"),
                        "html_link": body.get("htmlLink"),
                        "hangout_link": body.get("hangoutLink"),
                    },
                    resource_type="calendar_event",
                    resource_id=event_id,
                    event_payload={"event_id": event_id, "calendar_id": calendar_id},
                )

            if capability_id == "google.calendar.update_event":
                event_id = str(arguments["event_id"])
                payload: dict[str, Any] = {}
                for key in ("summary", "description", "location"):
                    if key in arguments:
                        payload[key] = arguments[key]
                if "attendees" in arguments:
                    payload["attendees"] = [{"email": item} for item in arguments["attendees"]]
                tz = arguments.get("time_zone")
                if "start" in arguments:
                    payload["start"] = {"dateTime": arguments["start"]}
                    if tz:
                        payload["start"]["timeZone"] = tz
                if "end" in arguments:
                    payload["end"] = {"dateTime": arguments["end"]}
                    if tz:
                        payload["end"]["timeZone"] = tz
                if not payload:
                    raise ValueError("At least one event field must be updated")
                body = await _request_json(
                    "PATCH",
                    f"https://www.googleapis.com/calendar/v3/calendars/{encoded_calendar}/events/{quote(event_id, safe='')}",
                    token,
                    payload,
                    retries=0,
                )
                resolved_id = str(body.get("id") or event_id)
                return CapabilityExecutionResult(
                    value={"event_id": resolved_id, "calendar_id": calendar_id, "provider_status": body.get("status"), "html_link": body.get("htmlLink")},
                    resource_type="calendar_event",
                    resource_id=resolved_id,
                    event_payload={"event_id": resolved_id, "calendar_id": calendar_id},
                )

            if capability_id == "google.calendar.delete_event":
                event_id = str(arguments["event_id"])
                await _request_json(
                    "DELETE",
                    f"https://www.googleapis.com/calendar/v3/calendars/{encoded_calendar}/events/{quote(event_id, safe='')}",
                    token,
                    expected_statuses=(200, 204),
                    retries=0,
                )
                return CapabilityExecutionResult(
                    value={"event_id": event_id, "calendar_id": calendar_id, "deleted": True},
                    resource_type="calendar_event",
                    resource_id=event_id,
                    event_payload={"event_id": event_id, "calendar_id": calendar_id},
                )

        raise LookupError(f"Google capability is not implemented: {capability_id}")
