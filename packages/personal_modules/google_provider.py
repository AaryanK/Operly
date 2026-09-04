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

from packages.connectors.account_secrets import read_account_secret, update_account_secret
from packages.database.account_connector_models import AccountConnector
from packages.kernel.contracts import CapabilityExecutionResult, CapabilityRisk, CapabilitySpec
from packages.security.execution_context import ExecutionContext


PROVIDER_ID = "operly.personal.google"

GMAIL_SEND = "https://www.googleapis.com/auth/gmail.send"
GMAIL_READONLY = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_MODIFY = "https://www.googleapis.com/auth/gmail.modify"
CALENDAR = "https://www.googleapis.com/auth/calendar.events"
CALENDAR_FREEBUSY = "https://www.googleapis.com/auth/calendar.freebusy"
CALENDAR_LIST_READONLY = "https://www.googleapis.com/auth/calendar.calendarlist.readonly"

GMAIL_READ_SCOPES = {GMAIL_READONLY, GMAIL_MODIFY}
GMAIL_SEND_SCOPES = {GMAIL_SEND, GMAIL_MODIFY}


class PersonalGoogleConnectorRequired(LookupError):
    pass


class PersonalGoogleProviderRejected(RuntimeError):
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
        scopes=frozenset({"personal"}),
        input_schema=input_schema or _object({}),
        output_schema=output_schema or _object({}, additional=True),
        permissions=(permission,),
        risk=risk,
        approval_required=approval,
        reversible=reversible,
        aliases=(),
        emits=emits,
        tags=frozenset(("personal", "google", "connector", "external", *tags)),
        resource_scope="personal",
    )


def personal_google_capabilities() -> tuple[CapabilitySpec, ...]:
    """Account-owned Google contracts for authorization-aware agent discovery.

    IDs intentionally match Workspace Google semantic IDs. The resolved scope decides
    which connector owner and provider are used, so an agent can discover one semantic
    operation without treating Workspace and Personal authority as interchangeable.
    """
    recipients = _array({"type": "string"}, min_items=1, max_items=20)
    optional_recipients = _array({"type": "string"}, max_items=20)
    connector = {"connector_id": {"type": "string", "maxLength": 80}}
    return (
        _capability(
            "google.connection.status",
            "Read personal Google connection status",
            "Inspect the authenticated user's connected Google accounts, scopes and health without exposing credentials.",
            permission="messaging:read",
            input_schema=_object(connector),
            output_schema=_object({"connections": _array(_object({}, additional=True))}, required=["connections"]),
            tags=("status", "read", "account"),
        ),
        _capability(
            "google.gmail.search",
            "Search personal Gmail",
            "Search an account-owned Gmail mailbox using Gmail search syntax.",
            permission="messaging:read",
            input_schema=_object({**connector, "query": {"type": "string", "minLength": 1, "maxLength": 1000}, "limit": {"type": "integer", "minimum": 1, "maximum": 20}}, required=["query"]),
            output_schema=_object({"messages": _array(_object({}, additional=True))}, required=["messages"]),
            tags=("gmail", "mail", "search", "read"),
        ),
        _capability(
            "google.gmail.read_message",
            "Read personal Gmail message",
            "Read one Gmail message by provider message ID from an account-owned mailbox.",
            permission="messaging:read",
            input_schema=_object({**connector, "message_id": {"type": "string", "minLength": 1, "maxLength": 200}}, required=["message_id"]),
            output_schema=_object({}, additional=True),
            tags=("gmail", "mail", "read"),
        ),
        _capability(
            "google.gmail.create_draft",
            "Create personal Gmail draft",
            "Create a plain-text Gmail draft in the user's account. Drafting does not send the message.",
            permission="messaging:draft",
            input_schema=_object({**connector, "to": recipients, "cc": optional_recipients, "bcc": optional_recipients, "subject": {"type": "string", "maxLength": 998}, "text_body": {"type": "string", "maxLength": 50000}}, required=["to", "subject"]),
            output_schema=_object({}, additional=True),
            risk=CapabilityRisk.LOW,
            reversible=True,
            emits=("gmail.draft.created",),
            tags=("gmail", "mail", "draft", "write"),
        ),
        _capability(
            "google.gmail.send_email",
            "Send personal Gmail message",
            "Send a plain-text Gmail message from the user's account after exact-invocation approval.",
            permission="messaging:send",
            input_schema=_object({**connector, "to": recipients, "cc": optional_recipients, "bcc": optional_recipients, "subject": {"type": "string", "maxLength": 998}, "text_body": {"type": "string", "maxLength": 50000}, "reply_to": {"type": "string", "maxLength": 320}}, required=["to", "subject"]),
            output_schema=_object({}, additional=True),
            risk=CapabilityRisk.HIGH,
            approval=True,
            reversible=False,
            emits=("gmail.message.sent",),
            tags=("gmail", "mail", "send", "write"),
        ),
        _capability(
            "google.gmail.modify_labels",
            "Modify personal Gmail labels",
            "Apply or remove Gmail labels on one account-owned message after exact-invocation approval.",
            permission="messaging:write",
            input_schema=_object({**connector, "message_id": {"type": "string", "minLength": 1, "maxLength": 200}, "add_label_ids": _array({"type": "string"}, max_items=20), "remove_label_ids": _array({"type": "string"}, max_items=20)}, required=["message_id"]),
            output_schema=_object({}, additional=True),
            risk=CapabilityRisk.MEDIUM,
            approval=True,
            reversible=True,
            emits=("gmail.labels.modified",),
            tags=("gmail", "mail", "labels", "write"),
        ),
        _capability(
            "google.calendar.list_calendars",
            "List personal Google calendars",
            "List calendars visible to the authenticated user's connected Google account.",
            permission="calendar:read",
            input_schema=_object(connector),
            output_schema=_object({"calendars": _array(_object({}, additional=True))}, required=["calendars"]),
            tags=("calendar", "read"),
        ),
        _capability(
            "google.calendar.list_events",
            "List personal Google Calendar events",
            "List events from an account-owned Google Calendar within an explicit time window.",
            permission="calendar:read",
            input_schema=_object({**connector, "time_min": {"type": "string", "minLength": 1, "maxLength": 80}, "time_max": {"type": "string", "minLength": 1, "maxLength": 80}, "query": {"type": "string", "maxLength": 500}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}, "calendar_id": {"type": "string", "maxLength": 500}}, required=["time_min", "time_max"]),
            output_schema=_object({"calendar_id": {"type": "string"}, "events": _array(_object({}, additional=True))}, required=["calendar_id", "events"]),
            tags=("calendar", "events", "read"),
        ),
        _capability(
            "google.calendar.freebusy",
            "Check personal Google Calendar free/busy",
            "Check free/busy blocks for explicit account-owned Google calendar IDs in a time window.",
            permission="calendar:read",
            input_schema=_object({**connector, "time_min": {"type": "string", "minLength": 1, "maxLength": 80}, "time_max": {"type": "string", "minLength": 1, "maxLength": 80}, "calendar_ids": _array({"type": "string"}, min_items=1, max_items=20), "time_zone": {"type": "string", "maxLength": 100}}, required=["time_min", "time_max", "calendar_ids"]),
            output_schema=_object({}, additional=True),
            tags=("calendar", "availability", "read"),
        ),
        _capability(
            "google.calendar.create_event",
            "Create personal Google Calendar event",
            "Create an event with explicit time and attendees after exact-invocation approval.",
            permission="calendar:write",
            input_schema=_object({**connector, "summary": {"type": "string", "minLength": 1, "maxLength": 500}, "start": {"type": "string", "minLength": 1, "maxLength": 80}, "end": {"type": "string", "minLength": 1, "maxLength": 80}, "attendees": _array({"type": "string"}, max_items=50), "description": {"type": "string", "maxLength": 10000}, "location": {"type": "string", "maxLength": 1000}, "time_zone": {"type": "string", "maxLength": 100}, "calendar_id": {"type": "string", "maxLength": 500}, "add_video_conference": {"type": "boolean"}}, required=["summary", "start", "end"]),
            output_schema=_object({}, additional=True),
            risk=CapabilityRisk.HIGH,
            approval=True,
            reversible=True,
            emits=("calendar.event.created",),
            tags=("calendar", "events", "write"),
        ),
        _capability(
            "google.calendar.update_event",
            "Update personal Google Calendar event",
            "Patch an existing account-owned Google Calendar event after exact-invocation approval.",
            permission="calendar:write",
            input_schema=_object({**connector, "event_id": {"type": "string", "minLength": 1, "maxLength": 500}, "calendar_id": {"type": "string", "maxLength": 500}, "summary": {"type": "string", "maxLength": 500}, "start": {"type": "string", "maxLength": 80}, "end": {"type": "string", "maxLength": 80}, "description": {"type": "string", "maxLength": 10000}, "location": {"type": "string", "maxLength": 1000}, "attendees": _array({"type": "string"}, max_items=50), "time_zone": {"type": "string", "maxLength": 100}}, required=["event_id"]),
            output_schema=_object({}, additional=True),
            risk=CapabilityRisk.HIGH,
            approval=True,
            reversible=True,
            emits=("calendar.event.updated",),
            tags=("calendar", "events", "write"),
        ),
        _capability(
            "google.calendar.delete_event",
            "Delete personal Google Calendar event",
            "Delete an account-owned Google Calendar event after explicit approval.",
            permission="calendar:write",
            input_schema=_object({**connector, "event_id": {"type": "string", "minLength": 1, "maxLength": 500}, "calendar_id": {"type": "string", "maxLength": 500}}, required=["event_id"]),
            output_schema=_object({"event_id": {"type": "string"}, "calendar_id": {"type": "string"}, "deleted": {"type": "boolean"}}, required=["event_id", "calendar_id", "deleted"]),
            risk=CapabilityRisk.HIGH,
            approval=True,
            reversible=False,
            emits=("calendar.event.deleted",),
            tags=("calendar", "events", "delete", "write"),
        ),
    )


def connector_scopes(connector: AccountConnector) -> set[str]:
    try:
        return set(json.loads(connector.granted_scopes_json or "[]"))
    except (TypeError, json.JSONDecodeError):
        return set()


def supported_capability_ids(scopes: set[str]) -> list[str]:
    result = ["google.connection.status"]
    if scopes & GMAIL_READ_SCOPES:
        result.extend(["google.gmail.search", "google.gmail.read_message"])
    if GMAIL_MODIFY in scopes:
        result.extend(["google.gmail.create_draft", "google.gmail.modify_labels"])
    if scopes & GMAIL_SEND_SCOPES:
        result.append("google.gmail.send_email")
    if CALENDAR in scopes:
        result.extend(["google.calendar.list_events", "google.calendar.create_event", "google.calendar.update_event", "google.calendar.delete_event"])
    if CALENDAR_FREEBUSY in scopes:
        result.append("google.calendar.freebusy")
    if CALENDAR_LIST_READONLY in scopes:
        result.append("google.calendar.list_calendars")
    return sorted(set(result))


def _supports(capability_id: str, scopes: set[str]) -> bool:
    if capability_id == "google.connection.status":
        return True
    if capability_id in {"google.gmail.search", "google.gmail.read_message"}:
        return bool(scopes & GMAIL_READ_SCOPES)
    if capability_id in {"google.gmail.create_draft", "google.gmail.modify_labels"}:
        return GMAIL_MODIFY in scopes
    if capability_id == "google.gmail.send_email":
        return bool(scopes & GMAIL_SEND_SCOPES)
    if capability_id in {"google.calendar.list_events", "google.calendar.create_event", "google.calendar.update_event", "google.calendar.delete_event"}:
        return CALENDAR in scopes
    if capability_id == "google.calendar.freebusy":
        return CALENDAR_FREEBUSY in scopes
    if capability_id == "google.calendar.list_calendars":
        return CALENDAR_LIST_READONLY in scopes
    return False


async def personal_google_connectors(db: AsyncSession, user_id: str) -> list[AccountConnector]:
    return list((await db.scalars(select(AccountConnector).where(AccountConnector.user_id == user_id, AccountConnector.provider == "google", AccountConnector.enabled.is_(True), AccountConnector.status == "connected").order_by(AccountConnector.created_at))).all())


async def _connector(db: AsyncSession, context: ExecutionContext, capability_id: str, requested_id: str | None = None) -> AccountConnector:
    if not context.is_personal or not context.user_id:
        raise PermissionError("Personal Google capability requires Personal authority")
    rows = [row for row in await personal_google_connectors(db, context.user_id) if _supports(capability_id, connector_scopes(row))]
    requested = str(requested_id or "").strip()
    if requested:
        for row in rows:
            if row.id == requested:
                return row
        raise PersonalGoogleConnectorRequired("Requested personal Google connector is unavailable or lacks the required scope")
    if not rows:
        raise PersonalGoogleConnectorRequired("Connect Personal Google and grant the required permission tier")
    if len(rows) > 1:
        raise PersonalGoogleConnectorRequired("Multiple Personal Google accounts match; specify connector_id")
    return rows[0]


async def access_token(db: AsyncSession, connector: AccountConnector) -> str:
    if not connector.credential_reference:
        raise PersonalGoogleConnectorRequired("Google connection has no credential reference")
    secret = await read_account_secret(db, connector.user_id, connector.credential_reference)
    now = datetime.now(timezone.utc).timestamp()
    current = str(secret.get("access_token") or "")
    if current and float(secret.get("expires_at", 0)) > now + 60:
        return current
    refresh_token = str(secret.get("refresh_token") or "")
    if not refresh_token:
        raise PersonalGoogleConnectorRequired("Google connection expired and must be reconnected")
    client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "").strip()
    client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise PersonalGoogleProviderRejected("Personal Google OAuth is not configured")
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
        async with session.post("https://oauth2.googleapis.com/token", data={"client_id": client_id, "client_secret": client_secret, "refresh_token": refresh_token, "grant_type": "refresh_token"}) as response:
            body = await response.json(content_type=None)
    if response.status != 200 or not isinstance(body, dict):
        raise PersonalGoogleProviderRejected("Google authorization refresh was rejected")
    secret.update(body)
    secret["expires_at"] = now + int(body.get("expires_in", 3600))
    await update_account_secret(db, connector.user_id, connector.credential_reference, secret)
    return str(secret["access_token"])


async def request_json(method: str, url: str, token: str, payload: dict[str, Any] | None = None, *, params: dict[str, Any] | None = None, expected_statuses: tuple[int, ...] = (200, 201, 204), retries: int | None = None) -> dict[str, Any]:
    upper = method.upper()
    attempts = (2 if upper in {"GET", "HEAD"} else 0) if retries is None else retries
    for attempt in range(attempts + 1):
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
                async with session.request(upper, url, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, json=payload, params=params) as response:
                    body = {} if response.status == 204 else await response.json(content_type=None)
                    if upper in {"GET", "HEAD"} and response.status in {429, 500, 502, 503, 504} and attempt < attempts:
                        await asyncio.sleep(0.25 * (attempt + 1))
                        continue
                    if response.status not in expected_statuses:
                        raise PersonalGoogleProviderRejected(f"Google rejected the request ({response.status})")
                    return body if isinstance(body, dict) else {}
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            if attempt >= attempts:
                raise PersonalGoogleProviderRejected("Google request failed before a confirmed provider response") from error
            await asyncio.sleep(0.25 * (attempt + 1))
    raise PersonalGoogleProviderRejected("Google request failed")


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
    return {str(item.get("name") or "").lower(): str(item.get("value") or "") for item in payload.get("headers", []) if isinstance(item, dict)}


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
        if isinstance(part, dict):
            child_plain, child_rich = _message_bodies(part)
            plain = plain or child_plain
            rich = rich or child_rich
    return plain[:20000], rich[:40000]


def _calendar_id(connector: AccountConnector, arguments: dict[str, Any]) -> str:
    try:
        configured = json.loads(connector.configuration_json or "{}").get("calendar_id", "primary")
    except (TypeError, json.JSONDecodeError):
        configured = "primary"
    return str(arguments.get("calendar_id") or configured or "primary")


def _event_payload(arguments: dict[str, Any], *, partial: bool = False) -> dict[str, Any]:
    body: dict[str, Any] = {}
    for key in ("summary", "description", "location"):
        if key in arguments:
            body[key] = str(arguments.get(key) or "")
    tz = str(arguments.get("time_zone") or "").strip()
    if arguments.get("start"):
        body["start"] = {"dateTime": str(arguments["start"])}
        if tz:
            body["start"]["timeZone"] = tz
    if arguments.get("end"):
        body["end"] = {"dateTime": str(arguments["end"])}
        if tz:
            body["end"]["timeZone"] = tz
    if "attendees" in arguments:
        body["attendees"] = [{"email": str(item).strip()} for item in arguments.get("attendees", []) if str(item).strip()]
    if not partial and ("start" not in body or "end" not in body):
        raise ValueError("Calendar event start and end are required")
    return body


class PersonalGoogleProvider:
    async def is_available(self, db: AsyncSession, *, context: ExecutionContext, capability: CapabilitySpec) -> bool:
        if not context.is_personal or not context.user_id:
            return False
        rows = await personal_google_connectors(db, context.user_id)
        if capability.id == "google.connection.status":
            return True
        return any(_supports(capability.id, connector_scopes(row)) for row in rows)

    async def execute(self, db: AsyncSession, *, context: ExecutionContext, capability: CapabilitySpec, arguments: dict[str, Any], minimum_context: dict[str, Any]) -> CapabilityExecutionResult:
        del minimum_context
        if not context.is_personal or not context.user_id:
            raise PermissionError("Personal Google capability requires Personal authority")
        capability_id = capability.id
        requested_connector = arguments.get("connector_id")
        if capability_id == "google.connection.status":
            rows = await personal_google_connectors(db, context.user_id)
            if requested_connector:
                rows = [row for row in rows if row.id == requested_connector]
            return CapabilityExecutionResult(value={"connections": [{"id": row.id, "display_name": row.display_name, "provider_account_id": row.provider_account_id, "status": row.status, "enabled": row.enabled, "health_status": row.health_status, "scopes": sorted(connector_scopes(row)), "capabilities": supported_capability_ids(connector_scopes(row)), "last_health_check": row.last_health_check.isoformat() if row.last_health_check else None} for row in rows]}, resource_type="personal_google_connection", resource_id=rows[0].id if len(rows) == 1 else None)

        connector = await _connector(db, context, capability_id, str(requested_connector or "") or None)
        token = await access_token(db, connector)

        if capability_id == "google.gmail.search":
            limit = max(1, min(int(arguments.get("limit") or 10), 20))
            listing = await request_json("GET", "https://gmail.googleapis.com/gmail/v1/users/me/messages", token, params={"q": arguments["query"], "maxResults": limit})
            messages = []
            for item in (listing.get("messages") or [])[:limit]:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                detail = await request_json("GET", "https://gmail.googleapis.com/gmail/v1/users/me/messages/" + quote(str(item["id"]), safe=""), token, params={"format": "metadata"})
                headers = _headers(detail.get("payload") or {})
                messages.append({"id": detail.get("id"), "thread_id": detail.get("threadId"), "from": headers.get("from"), "to": headers.get("to"), "subject": headers.get("subject"), "date": headers.get("date"), "snippet": str(detail.get("snippet") or "")[:500], "label_ids": detail.get("labelIds", [])})
            return CapabilityExecutionResult(value={"messages": messages}, resource_type="gmail_mailbox", resource_id=connector.id)

        if capability_id == "google.gmail.read_message":
            message_id = str(arguments["message_id"])
            detail = await request_json("GET", "https://gmail.googleapis.com/gmail/v1/users/me/messages/" + quote(message_id, safe=""), token, params={"format": "full"})
            headers = _headers(detail.get("payload") or {})
            plain, rich = _message_bodies(detail.get("payload") or {})
            return CapabilityExecutionResult(value={"id": detail.get("id"), "thread_id": detail.get("threadId"), "from": headers.get("from"), "to": headers.get("to"), "cc": headers.get("cc"), "subject": headers.get("subject"), "date": headers.get("date"), "snippet": str(detail.get("snippet") or "")[:1000], "text_body": plain, "html_body": rich, "label_ids": detail.get("labelIds", [])}, resource_type="gmail_message", resource_id=str(detail.get("id") or message_id))

        if capability_id in {"google.gmail.create_draft", "google.gmail.send_email"}:
            message = _email_message(arguments)
            if capability_id.endswith("create_draft"):
                body = await request_json("POST", "https://gmail.googleapis.com/gmail/v1/users/me/drafts", token, {"message": {"raw": _raw_message(message)}})
                draft_id = str(body.get("id") or "")
                message_id = str((body.get("message") or {}).get("id") or "")
                return CapabilityExecutionResult(value={"draft_id": draft_id, "message_id": message_id or None, "provider_account": connector.provider_account_id}, resource_type="gmail_draft", resource_id=draft_id or None, event_payload={"connector_id": connector.id, "provider_account": connector.provider_account_id})
            body = await request_json("POST", "https://gmail.googleapis.com/gmail/v1/users/me/messages/send", token, {"raw": _raw_message(message)})
            message_id = str(body.get("id") or "")
            return CapabilityExecutionResult(value={"message_id": message_id, "thread_id": body.get("threadId"), "provider_account": connector.provider_account_id, "provider_status": "accepted"}, resource_type="gmail_message", resource_id=message_id or None, event_payload={"connector_id": connector.id, "provider_account": connector.provider_account_id})

        if capability_id == "google.gmail.modify_labels":
            message_id = str(arguments["message_id"])
            body = await request_json("POST", "https://gmail.googleapis.com/gmail/v1/users/me/messages/" + quote(message_id, safe="") + "/modify", token, {"addLabelIds": list(arguments.get("add_label_ids") or []), "removeLabelIds": list(arguments.get("remove_label_ids") or [])})
            return CapabilityExecutionResult(value={"message_id": body.get("id") or message_id, "label_ids": body.get("labelIds", [])}, resource_type="gmail_message", resource_id=message_id, event_payload={"connector_id": connector.id})

        calendar_id = _calendar_id(connector, arguments)
        encoded_calendar = quote(calendar_id, safe="")
        if capability_id == "google.calendar.list_calendars":
            body = await request_json("GET", "https://www.googleapis.com/calendar/v3/users/me/calendarList", token, params={"maxResults": 100})
            calendars = [{"id": item.get("id"), "summary": item.get("summary"), "primary": bool(item.get("primary")), "access_role": item.get("accessRole"), "time_zone": item.get("timeZone")} for item in body.get("items") or [] if isinstance(item, dict)]
            return CapabilityExecutionResult(value={"calendars": calendars}, resource_type="calendar_collection", resource_id=connector.id)

        if capability_id == "google.calendar.list_events":
            limit = max(1, min(int(arguments.get("limit") or 20), 50))
            params: dict[str, Any] = {"timeMin": arguments["time_min"], "timeMax": arguments["time_max"], "singleEvents": "true", "orderBy": "startTime", "maxResults": limit}
            if arguments.get("query"):
                params["q"] = arguments["query"]
            body = await request_json("GET", f"https://www.googleapis.com/calendar/v3/calendars/{encoded_calendar}/events", token, params=params)
            return CapabilityExecutionResult(value={"calendar_id": calendar_id, "events": list(body.get("items") or [])}, resource_type="calendar", resource_id=calendar_id)

        if capability_id == "google.calendar.freebusy":
            payload: dict[str, Any] = {"timeMin": arguments["time_min"], "timeMax": arguments["time_max"], "items": [{"id": str(item)} for item in arguments.get("calendar_ids", [])]}
            if arguments.get("time_zone"):
                payload["timeZone"] = arguments["time_zone"]
            body = await request_json("POST", "https://www.googleapis.com/calendar/v3/freeBusy", token, payload)
            return CapabilityExecutionResult(value=body, resource_type="calendar_freebusy", resource_id=connector.id)

        if capability_id == "google.calendar.create_event":
            payload = _event_payload(arguments)
            params = {}
            if arguments.get("add_video_conference"):
                payload["conferenceData"] = {"createRequest": {"requestId": f"operly-{context.user_id}-{int(datetime.now(timezone.utc).timestamp())}", "conferenceSolutionKey": {"type": "hangoutsMeet"}}}
                params["conferenceDataVersion"] = 1
            body = await request_json("POST", f"https://www.googleapis.com/calendar/v3/calendars/{encoded_calendar}/events", token, payload, params=params or None)
            event_id = str(body.get("id") or "")
            return CapabilityExecutionResult(value={"calendar_id": calendar_id, "event": body}, resource_type="calendar_event", resource_id=event_id or None, event_payload={"connector_id": connector.id, "calendar_id": calendar_id, "event_id": event_id})

        if capability_id == "google.calendar.update_event":
            event_id = str(arguments["event_id"])
            payload = _event_payload(arguments, partial=True)
            if not payload:
                raise ValueError("At least one calendar event field must be changed")
            body = await request_json("PATCH", f"https://www.googleapis.com/calendar/v3/calendars/{encoded_calendar}/events/{quote(event_id, safe='')}", token, payload)
            return CapabilityExecutionResult(value={"calendar_id": calendar_id, "event": body}, resource_type="calendar_event", resource_id=event_id, event_payload={"connector_id": connector.id, "calendar_id": calendar_id, "event_id": event_id})

        if capability_id == "google.calendar.delete_event":
            event_id = str(arguments["event_id"])
            await request_json("DELETE", f"https://www.googleapis.com/calendar/v3/calendars/{encoded_calendar}/events/{quote(event_id, safe='')}", token)
            return CapabilityExecutionResult(value={"event_id": event_id, "calendar_id": calendar_id, "deleted": True}, resource_type="calendar_event", resource_id=event_id, event_payload={"connector_id": connector.id, "calendar_id": calendar_id, "event_id": event_id})

        raise LookupError(f"Personal Google capability is not implemented: {capability_id}")
