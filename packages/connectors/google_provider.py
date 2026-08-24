import asyncio
import base64
import html
import json
import os
import re
from datetime import datetime, timezone
from email.message import EmailMessage
from html.parser import HTMLParser
from urllib.parse import quote

import aiohttp
from sqlalchemy import select

from packages.capabilities.contracts import (
    ApprovalPolicy,
    CapabilityDefinition,
    CapabilityResult,
    ExecutionMode,
)
from packages.capabilities.providers import BaseProvider
from packages.company.events import append_event
from packages.connectors.google_scope import (
    google_access_token_for_context,
    google_connector_any_for_context,
    google_connector_for_context,
)
from packages.connectors.secrets import read_secret, update_secret
from packages.database.business_models import Contact, Lead
from packages.database.connector_models import TenantConnector


GMAIL_SEND = "https://www.googleapis.com/auth/gmail.send"
GMAIL_READONLY = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_MODIFY = "https://www.googleapis.com/auth/gmail.modify"
CALENDAR = "https://www.googleapis.com/auth/calendar.events"
CALENDAR_FREEBUSY = "https://www.googleapis.com/auth/calendar.freebusy"
CALENDAR_LIST_READONLY = "https://www.googleapis.com/auth/calendar.calendarlist.readonly"
CALENDAR_SETTINGS_READONLY = "https://www.googleapis.com/auth/calendar.settings.readonly"

GMAIL_READ_SCOPES = {GMAIL_READONLY, GMAIL_MODIFY}
GMAIL_SEND_SCOPES = {GMAIL_SEND, GMAIL_MODIFY}


class ConnectorRequired(LookupError):
    pass


class ProviderRejected(RuntimeError):
    pass


def _scopes(connector: TenantConnector) -> set[str]:
    return set(json.loads(connector.granted_scopes_json or "[]"))


async def google_connector(db, tenant_id, required_scope):
    """Legacy workspace-only connector resolver kept for existing callers."""
    rows = (
        await db.scalars(
            select(TenantConnector).where(
                TenantConnector.tenant_id == tenant_id,
                TenantConnector.provider == "google",
                TenantConnector.enabled.is_(True),
                TenantConnector.status == "connected",
            )
        )
    ).all()
    required = {required_scope} if isinstance(required_scope, str) else set(required_scope)
    for row in rows:
        granted = _scopes(row)
        if required.issubset(granted):
            return row
    raise ConnectorRequired("Connect Google and grant the required scope")


async def google_connector_any(db, tenant_id, acceptable_scopes):
    """Legacy workspace-only any-scope resolver kept for compatibility."""
    rows = (
        await db.scalars(
            select(TenantConnector).where(
                TenantConnector.tenant_id == tenant_id,
                TenantConnector.provider == "google",
                TenantConnector.enabled.is_(True),
                TenantConnector.status == "connected",
            )
        )
    ).all()
    acceptable = set(acceptable_scopes)
    for row in rows:
        if _scopes(row) & acceptable:
            return row
    raise ConnectorRequired("Reconnect Google with the full assistant permission tier")


async def access_token(db, connector):
    """Legacy workspace-owned Google token resolver."""
    secret = await read_secret(db, connector.tenant_id, connector.credential_reference)
    if float(secret.get("expires_at", 0)) > datetime.now(timezone.utc).timestamp() + 60:
        return secret["access_token"]
    data = {
        "client_id": os.environ["GOOGLE_OAUTH_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
        "refresh_token": secret.get("refresh_token"),
        "grant_type": "refresh_token",
    }
    async with aiohttp.ClientSession() as session:
        async with session.post("https://oauth2.googleapis.com/token", data=data) as response:
            body = await response.json()
    if response.status != 200:
        raise ProviderRejected("Google authorization expired or refresh was rejected")
    secret.update(body)
    secret["expires_at"] = datetime.now(timezone.utc).timestamp() + int(body.get("expires_in", 3600))
    await update_secret(db, connector.tenant_id, connector.credential_reference, secret)
    return secret["access_token"]


async def request_json(method, url, token, payload=None, retries=2, *, params=None, expected_statuses=(200, 201, 204)):
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
                        await asyncio.sleep(0.2 * (attempt + 1))
                        continue
                    if response.status not in expected_statuses:
                        raise ProviderRejected(f"Google rejected the request ({response.status})")
                    return body
        except (aiohttp.ClientError, asyncio.TimeoutError):
            if attempt >= retries:
                raise
            await asyncio.sleep(0.2 * (attempt + 1))


_ALLOWED_EMAIL_TAGS = {
    "a", "b", "blockquote", "br", "code", "div", "em", "h1", "h2", "h3", "h4", "hr", "i",
    "img", "li", "ol", "p", "pre", "span", "strong", "table", "tbody", "td", "th", "thead", "tr", "u", "ul",
}
_ALLOWED_EMAIL_ATTRS = {"a": {"href", "title"}, "img": {"src", "alt", "width", "height", "title"}, "*": {"style"}}
_VOID_TAGS = {"br", "hr", "img"}


class _SafeEmailHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.blocked_depth = 0

    @staticmethod
    def _safe_url(value: str) -> bool:
        return value.strip().lower().startswith(("https://", "http://", "mailto:"))

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in {"script", "iframe", "object", "embed", "form"}:
            self.blocked_depth += 1
            return
        if self.blocked_depth or tag not in _ALLOWED_EMAIL_TAGS:
            return
        allowed = _ALLOWED_EMAIL_ATTRS.get(tag, set()) | _ALLOWED_EMAIL_ATTRS["*"]
        clean_attrs = []
        for key, value in attrs:
            key = key.lower()
            value = value or ""
            if key not in allowed or key.startswith("on"):
                continue
            if key in {"href", "src"} and not self._safe_url(value):
                continue
            clean_attrs.append(f'{key}="{html.escape(value, quote=True)}"')
        suffix = (" " + " ".join(clean_attrs)) if clean_attrs else ""
        self.parts.append(f"<{tag}{suffix}>")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {"script", "iframe", "object", "embed", "form"}:
            if self.blocked_depth:
                self.blocked_depth -= 1
            return
        if not self.blocked_depth and tag in _ALLOWED_EMAIL_TAGS and tag not in _VOID_TAGS:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.blocked_depth:
            self.parts.append(html.escape(data))

    def handle_entityref(self, name):
        if not self.blocked_depth:
            self.parts.append(f"&{name};")

    def handle_charref(self, name):
        if not self.blocked_depth:
            self.parts.append(f"&#{name};")


def sanitize_html_email(value: str) -> str:
    parser = _SafeEmailHTML()
    parser.feed(str(value or ""))
    parser.close()
    return "".join(parser.parts)[:100_000]


def _plain_from_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    return html.unescape(" ".join(text.split()))[:50_000]


def _email_message(*, to, subject, text_body="", html_body="", cc=None, bcc=None, reply_to=None, message_id=None, in_reply_to=None, references=None):
    recipients = [str(item).strip() for item in (to or []) if str(item).strip()]
    if not recipients:
        raise ValueError("At least one recipient is required")
    message = EmailMessage()
    message["To"] = ", ".join(recipients)
    if cc:
        message["Cc"] = ", ".join(str(item).strip() for item in cc if str(item).strip())
    if bcc:
        message["Bcc"] = ", ".join(str(item).strip() for item in bcc if str(item).strip())
    message["Subject"] = str(subject or "").strip()[:998]
    if reply_to:
        message["Reply-To"] = str(reply_to).strip()
    if message_id:
        message["Message-ID"] = message_id
    if in_reply_to:
        message["In-Reply-To"] = str(in_reply_to)
    if references:
        message["References"] = str(references)
    safe_html = sanitize_html_email(html_body) if html_body else ""
    fallback = str(text_body or "").strip()
    if not fallback and safe_html:
        fallback = _plain_from_html(safe_html)
    message.set_content(fallback or " ")
    if safe_html:
        message.add_alternative(safe_html, subtype="html")
    return message


def _raw_message(message: EmailMessage) -> str:
    return base64.urlsafe_b64encode(message.as_bytes()).decode()


def _headers(payload: dict) -> dict[str, str]:
    return {str(item.get("name") or "").lower(): str(item.get("value") or "") for item in payload.get("headers", [])}


def _decode_gmail_data(value: str | None) -> str:
    if not value:
        return ""
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode((value + padding).encode()).decode("utf-8", errors="replace")
    except (ValueError, UnicodeDecodeError):
        return ""


def _message_bodies(payload: dict) -> tuple[str, str]:
    plain = ""
    rich = ""
    mime = payload.get("mimeType")
    body = payload.get("body") or {}
    if mime == "text/plain":
        plain = _decode_gmail_data(body.get("data"))
    elif mime == "text/html":
        rich = _decode_gmail_data(body.get("data"))
    for part in payload.get("parts") or []:
        child_plain, child_rich = _message_bodies(part)
        plain = plain or child_plain
        rich = rich or child_rich
    return plain[:20_000], rich[:40_000]


async def _gmail_send(context, connector, message: EmailMessage, *, event_payload: dict):
    token = await google_access_token_for_context(context, connector)
    body = await request_json(
        "POST",
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        token,
        {"raw": _raw_message(message)},
        retries=0,
    )
    evidence = {
        "provider": "gmail",
        "provider_account": connector.provider_account_id,
        "message_id": body["id"],
        "thread_id": body.get("threadId"),
        "provider_status": "accepted",
        "delivery": "unknown",
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        **event_payload,
    }
    if context.tenant_id:
        await append_event(context.db, tenant_id=context.tenant_id, event_type="message.sent", payload=evidence, source="gmail")
    return CapabilityResult(True, True, evidence, body["id"])


class GmailProvider(BaseProvider):
    name = "gmail"
    capabilities = (
        CapabilityDefinition(
            "messaging.send", "messaging_send",
            "Send an approved lead follow-up through Gmail. Supports static rich HTML as well as plain text.",
            {"type": "object", "properties": {"lead_id": {"type": "string"}, "message": {"type": "string"}, "html_message": {"type": "string"}, "subject": {"type": "string"}}, "required": ["lead_id", "message"], "additionalProperties": False},
            {"type": "object"}, risk_level="high", permissions=("messaging:send",), approval_policy=ApprovalPolicy.ALWAYS,
            execution_mode=ExecutionMode.EXTERNAL, source="external", provider="google", integration_provider="google", credential_scopes=(GMAIL_SEND,),
        ),
        CapabilityDefinition(
            "gmail.send_email", "gmail_send_email",
            "Send an approved Gmail message to explicit recipients. Supports static sanitized HTML; arbitrary JavaScript is not an email capability.",
            {"type": "object", "properties": {
                "to": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 20},
                "cc": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
                "bcc": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
                "subject": {"type": "string"}, "text_body": {"type": "string"}, "html_body": {"type": "string"},
                "reply_to": {"type": "string"}, "thread_id": {"type": "string"}, "in_reply_to": {"type": "string"}, "references": {"type": "string"},
            }, "required": ["to", "subject"], "additionalProperties": False},
            {"type": "object"}, risk_level="high", permissions=("messaging:send",), approval_policy=ApprovalPolicy.ALWAYS,
            execution_mode=ExecutionMode.EXTERNAL, source="external", provider="google", integration_provider="google", credential_scopes=(GMAIL_SEND,),
        ),
        CapabilityDefinition(
            "gmail.search", "gmail_search", "Search the connected Gmail mailbox using Gmail search syntax.",
            {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 10}}, "required": ["query"], "additionalProperties": False},
            {"type": "object"}, risk_level="read_only", permissions=("messaging:read",), approval_policy=ApprovalPolicy.AUTO,
            execution_mode=ExecutionMode.EXTERNAL, source="external", provider="google", integration_provider="google", credential_scopes=(GMAIL_READONLY,),
        ),
        CapabilityDefinition(
            "gmail.read_message", "gmail_read_message", "Read one Gmail message by message ID from the current connected account.",
            {"type": "object", "properties": {"message_id": {"type": "string"}}, "required": ["message_id"], "additionalProperties": False},
            {"type": "object"}, risk_level="read_only", permissions=("messaging:read",), approval_policy=ApprovalPolicy.AUTO,
            execution_mode=ExecutionMode.EXTERNAL, source="external", provider="google", integration_provider="google", credential_scopes=(GMAIL_READONLY,),
        ),
        CapabilityDefinition(
            "gmail.modify_labels", "gmail_modify_labels", "Apply or remove Gmail labels on one message, including read/unread/archive workflows.",
            {"type": "object", "properties": {"message_id": {"type": "string"}, "add_label_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 20}, "remove_label_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 20}}, "required": ["message_id"], "additionalProperties": False},
            {"type": "object"}, risk_level="medium", permissions=("messaging:write",), approval_policy=ApprovalPolicy.ALWAYS,
            execution_mode=ExecutionMode.EXTERNAL, source="external", provider="google", integration_provider="google", credential_scopes=(GMAIL_MODIFY,), reversible=True,
        ),
        CapabilityDefinition(
            "gmail.create_draft", "gmail_create_draft", "Create a Gmail draft with plain text and/or static rich HTML.",
            {"type": "object", "properties": {
                "to": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 20},
                "cc": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
                "bcc": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
                "subject": {"type": "string"}, "text_body": {"type": "string"}, "html_body": {"type": "string"},
            }, "required": ["to", "subject"], "additionalProperties": False},
            {"type": "object"}, risk_level="low", permissions=("messaging:draft",), approval_policy=ApprovalPolicy.AUTO,
            execution_mode=ExecutionMode.EXTERNAL, source="external", provider="google", integration_provider="google", credential_scopes=(GMAIL_MODIFY,), reversible=True,
        ),
    )

    async def execute(self, context, capability_name, arguments):
        if capability_name == "messaging.send":
            if getattr(context, "scope_kind", "workspace") != "workspace" or not context.tenant_id:
                return CapabilityResult(False, False, {"reason": "workspace_lead_context_required"})
            connector = await google_connector_any_for_context(context, GMAIL_SEND_SCOPES)
            lead = await context.db.scalar(select(Lead).where(Lead.id == arguments["lead_id"], Lead.tenant_id == context.tenant_id))
            contact = await context.db.get(Contact, lead.contact_id) if lead and lead.contact_id else None
            if not contact or not contact.email:
                return CapabilityResult(False, False, {"reason": "lead_has_no_email"})
            message = _email_message(
                to=[contact.email], subject=arguments.get("subject") or f"Following up: {lead.title}",
                text_body=arguments["message"], html_body=arguments.get("html_message") or "",
                message_id=f"<{context.execution_id}@operly.local>",
            )
            result = await _gmail_send(context, connector, message, event_payload={"recipient": contact.email, "lead_id": lead.id})
            lead.next_action = f"Follow-up accepted by Gmail ({result.evidence['message_id']})"
            return result

        if capability_name == "gmail.send_email":
            connector = await google_connector_any_for_context(context, GMAIL_SEND_SCOPES)
            message = _email_message(
                to=arguments["to"], cc=arguments.get("cc"), bcc=arguments.get("bcc"), subject=arguments["subject"],
                text_body=arguments.get("text_body") or "", html_body=arguments.get("html_body") or "", reply_to=arguments.get("reply_to"),
                message_id=f"<{context.execution_id}@operly.local>", in_reply_to=arguments.get("in_reply_to"), references=arguments.get("references"),
            )
            result = await _gmail_send(context, connector, message, event_payload={"recipients": list(arguments["to"]), "subject": arguments["subject"], "rich_html": bool(arguments.get("html_body"))})
            if arguments.get("thread_id"):
                result.evidence["requested_thread_id"] = arguments["thread_id"]
            return result

        if capability_name == "gmail.search":
            connector = await google_connector_any_for_context(context, GMAIL_READ_SCOPES)
            token = await google_access_token_for_context(context, connector)
            limit = max(1, min(int(arguments.get("limit", 10)), 10))
            listing = await request_json("GET", "https://gmail.googleapis.com/gmail/v1/users/me/messages", token, params={"q": arguments["query"], "maxResults": limit})
            results = []
            for item in (listing.get("messages") or [])[:limit]:
                detail = await request_json("GET", f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{quote(item['id'], safe='')}", token, params={"format": "metadata"})
                headers = _headers(detail.get("payload") or {})
                results.append({"id": detail.get("id"), "thread_id": detail.get("threadId"), "from": headers.get("from"), "to": headers.get("to"), "subject": headers.get("subject"), "date": headers.get("date"), "snippet": detail.get("snippet", "")[:500], "label_ids": detail.get("labelIds", [])})
            return CapabilityResult(True, False, {"query": arguments["query"], "messages": results})

        if capability_name == "gmail.read_message":
            connector = await google_connector_any_for_context(context, GMAIL_READ_SCOPES)
            token = await google_access_token_for_context(context, connector)
            detail = await request_json("GET", f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{quote(arguments['message_id'], safe='')}", token, params={"format": "full"})
            headers = _headers(detail.get("payload") or {})
            plain, rich = _message_bodies(detail.get("payload") or {})
            return CapabilityResult(True, False, {
                "id": detail.get("id"), "thread_id": detail.get("threadId"), "from": headers.get("from"), "to": headers.get("to"), "cc": headers.get("cc"),
                "subject": headers.get("subject"), "date": headers.get("date"), "snippet": detail.get("snippet", "")[:1000], "text_body": plain, "html_body": rich, "label_ids": detail.get("labelIds", []),
            })

        if capability_name == "gmail.modify_labels":
            connector = await google_connector_for_context(context, GMAIL_MODIFY)
            token = await google_access_token_for_context(context, connector)
            body = await request_json("POST", f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{quote(arguments['message_id'], safe='')}/modify", token, {"addLabelIds": list(arguments.get("add_label_ids") or []), "removeLabelIds": list(arguments.get("remove_label_ids") or [])})
            evidence = {"message_id": body.get("id") or arguments["message_id"], "label_ids": body.get("labelIds", []), "add_label_ids": list(arguments.get("add_label_ids") or []), "remove_label_ids": list(arguments.get("remove_label_ids") or [])}
            if context.tenant_id:
                await append_event(context.db, tenant_id=context.tenant_id, event_type="message.labels_changed", payload=evidence, source="gmail")
            return CapabilityResult(True, True, evidence, evidence["message_id"])

        if capability_name == "gmail.create_draft":
            connector = await google_connector_for_context(context, GMAIL_MODIFY)
            token = await google_access_token_for_context(context, connector)
            message = _email_message(to=arguments["to"], cc=arguments.get("cc"), bcc=arguments.get("bcc"), subject=arguments["subject"], text_body=arguments.get("text_body") or "", html_body=arguments.get("html_body") or "", message_id=f"<{context.execution_id}@operly.local>")
            body = await request_json("POST", "https://gmail.googleapis.com/gmail/v1/users/me/drafts", token, {"message": {"raw": _raw_message(message)}})
            evidence = {"draft_id": body.get("id"), "message_id": (body.get("message") or {}).get("id"), "recipients": list(arguments["to"]), "subject": arguments["subject"], "rich_html": bool(arguments.get("html_body"))}
            return CapabilityResult(True, True, evidence, body.get("id"))

        return CapabilityResult(False, False, {"reason": "unsupported_gmail_capability"})

    async def verify(self, context, capability_name, arguments, result):
        if capability_name in {"gmail.search", "gmail.read_message"}:
            return CapabilityResult(result.success, False, {"observation_available": result.success, **result.evidence})
        if capability_name == "gmail.create_draft":
            return CapabilityResult(bool(result.evidence.get("draft_id")), result.changed, result.evidence)
        if capability_name == "gmail.modify_labels":
            return CapabilityResult(bool(result.evidence.get("message_id")), result.changed, result.evidence)
        return CapabilityResult(bool(result.evidence.get("message_id") and result.evidence.get("provider_status") == "accepted"), result.changed, result.evidence)


class GoogleCalendarProvider(BaseProvider):
    name = "google_calendar"
    capabilities = (
        CapabilityDefinition(
            "calendar.create_event", "calendar_create_event", "Create an approved event through a connected Google Calendar.",
            {"type": "object", "properties": {
                "summary": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"},
                "attendees": {"type": "array", "items": {"type": "string"}, "maxItems": 50}, "lead_id": {"type": "string"},
                "description": {"type": "string"}, "location": {"type": "string"}, "time_zone": {"type": "string"}, "add_video_conference": {"type": "boolean"},
            }, "required": ["summary", "start", "end"], "additionalProperties": False},
            {"type": "object"}, risk_level="high", permissions=("calendar:write",), approval_policy=ApprovalPolicy.ALWAYS,
            execution_mode=ExecutionMode.EXTERNAL, source="external", provider="google", integration_provider="google", credential_scopes=(CALENDAR,),
        ),
        CapabilityDefinition(
            "calendar.list_events", "calendar_list_events", "List Google Calendar events in a time window.",
            {"type": "object", "properties": {"time_min": {"type": "string"}, "time_max": {"type": "string"}, "query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}, "calendar_id": {"type": "string"}}, "required": ["time_min", "time_max"], "additionalProperties": False},
            {"type": "object"}, risk_level="read_only", permissions=("calendar:read",), approval_policy=ApprovalPolicy.AUTO,
            execution_mode=ExecutionMode.EXTERNAL, source="external", provider="google", integration_provider="google", credential_scopes=(CALENDAR,),
        ),
        CapabilityDefinition(
            "calendar.freebusy", "calendar_freebusy", "Check free/busy availability for Google calendars.",
            {"type": "object", "properties": {"time_min": {"type": "string"}, "time_max": {"type": "string"}, "calendar_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 20}, "time_zone": {"type": "string"}}, "required": ["time_min", "time_max", "calendar_ids"], "additionalProperties": False},
            {"type": "object"}, risk_level="read_only", permissions=("calendar:read",), approval_policy=ApprovalPolicy.AUTO,
            execution_mode=ExecutionMode.EXTERNAL, source="external", provider="google", integration_provider="google", credential_scopes=(CALENDAR_FREEBUSY,),
        ),
        CapabilityDefinition(
            "calendar.list_calendars", "calendar_list_calendars", "List calendars available to the connected Google account.",
            {"type": "object", "properties": {}, "additionalProperties": False}, {"type": "object"}, risk_level="read_only", permissions=("calendar:read",), approval_policy=ApprovalPolicy.AUTO,
            execution_mode=ExecutionMode.EXTERNAL, source="external", provider="google", integration_provider="google", credential_scopes=(CALENDAR_LIST_READONLY,),
        ),
        CapabilityDefinition(
            "calendar.update_event", "calendar_update_event", "Update an existing Google Calendar event after approval.",
            {"type": "object", "properties": {"event_id": {"type": "string"}, "calendar_id": {"type": "string"}, "summary": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"}, "description": {"type": "string"}, "location": {"type": "string"}, "attendees": {"type": "array", "items": {"type": "string"}, "maxItems": 50}, "time_zone": {"type": "string"}}, "required": ["event_id"], "additionalProperties": False},
            {"type": "object"}, risk_level="high", permissions=("calendar:write",), approval_policy=ApprovalPolicy.ALWAYS,
            execution_mode=ExecutionMode.EXTERNAL, source="external", provider="google", integration_provider="google", credential_scopes=(CALENDAR,), reversible=True,
        ),
        CapabilityDefinition(
            "calendar.delete_event", "calendar_delete_event", "Delete an existing Google Calendar event after approval.",
            {"type": "object", "properties": {"event_id": {"type": "string"}, "calendar_id": {"type": "string"}}, "required": ["event_id"], "additionalProperties": False},
            {"type": "object"}, risk_level="high", permissions=("calendar:write",), approval_policy=ApprovalPolicy.ALWAYS,
            execution_mode=ExecutionMode.EXTERNAL, source="external", provider="google", integration_provider="google", credential_scopes=(CALENDAR,),
        ),
    )

    @staticmethod
    def _calendar_id(connector, arguments):
        configured = json.loads(connector.configuration_json or "{}").get("calendar_id", "primary")
        return str(arguments.get("calendar_id") or configured or "primary")

    async def execute(self, context, capability_name, arguments):
        if capability_name == "calendar.freebusy":
            connector = await google_connector_for_context(context, CALENDAR_FREEBUSY)
            payload = {"timeMin": arguments["time_min"], "timeMax": arguments["time_max"], "items": [{"id": item} for item in arguments["calendar_ids"]]}
            if arguments.get("time_zone"):
                payload["timeZone"] = arguments["time_zone"]
            body = await request_json("POST", "https://www.googleapis.com/calendar/v3/freeBusy", await google_access_token_for_context(context, connector), payload)
            return CapabilityResult(True, False, {"calendars": body.get("calendars", {}), "groups": body.get("groups", {})})

        if capability_name == "calendar.list_calendars":
            connector = await google_connector_for_context(context, CALENDAR_LIST_READONLY)
            body = await request_json("GET", "https://www.googleapis.com/calendar/v3/users/me/calendarList", await google_access_token_for_context(context, connector))
            return CapabilityResult(True, False, {"calendars": [{"id": item.get("id"), "summary": item.get("summary"), "primary": item.get("primary", False), "access_role": item.get("accessRole"), "time_zone": item.get("timeZone")} for item in body.get("items", [])[:50]]})

        connector = await google_connector_for_context(context, CALENDAR)
        calendar_id = self._calendar_id(connector, arguments)
        encoded_calendar = quote(calendar_id, safe="")
        token = await google_access_token_for_context(context, connector)

        if capability_name == "calendar.list_events":
            params = {"timeMin": arguments["time_min"], "timeMax": arguments["time_max"], "singleEvents": "true", "orderBy": "startTime", "maxResults": max(1, min(int(arguments.get("limit", 20)), 50))}
            if arguments.get("query"):
                params["q"] = arguments["query"]
            body = await request_json("GET", f"https://www.googleapis.com/calendar/v3/calendars/{encoded_calendar}/events", token, params=params)
            events = [{"id": item.get("id"), "summary": item.get("summary"), "description": item.get("description"), "location": item.get("location"), "start": item.get("start"), "end": item.get("end"), "attendees": [{"email": person.get("email"), "response_status": person.get("responseStatus")} for person in item.get("attendees", [])], "status": item.get("status"), "html_link": item.get("htmlLink"), "hangout_link": item.get("hangoutLink")} for item in body.get("items", [])]
            return CapabilityResult(True, False, {"calendar_id": calendar_id, "events": events})

        if capability_name == "calendar.create_event":
            tz = arguments.get("time_zone")
            start = {"dateTime": arguments["start"]}
            end = {"dateTime": arguments["end"]}
            if tz:
                start["timeZone"] = tz
                end["timeZone"] = tz
            payload = {"id": (context.execution_id or "").replace("-", "")[:32] or None, "summary": arguments["summary"], "start": start, "end": end, "attendees": [{"email": item} for item in arguments.get("attendees", [])]}
            if arguments.get("description"):
                payload["description"] = arguments["description"]
            if arguments.get("location"):
                payload["location"] = arguments["location"]
            params = {}
            if arguments.get("add_video_conference"):
                payload["conferenceData"] = {"createRequest": {"requestId": (context.execution_id or "operly").replace("-", "")[:32], "conferenceSolutionKey": {"type": "hangoutsMeet"}}}
                params["conferenceDataVersion"] = "1"
            body = await request_json("POST", f"https://www.googleapis.com/calendar/v3/calendars/{encoded_calendar}/events", token, payload, params=params or None)
            if arguments.get("lead_id") and context.tenant_id:
                lead = await context.db.scalar(select(Lead).where(Lead.id == arguments["lead_id"], Lead.tenant_id == context.tenant_id))
                if lead:
                    lead.next_action = f"Calendar follow-up created ({body['id']})"
            evidence = {"provider": "google_calendar", "event_id": body["id"], "calendar_id": calendar_id, "start": arguments["start"], "end": arguments["end"], "attendees": arguments.get("attendees", []), "provider_status": body.get("status", "confirmed"), "html_link": body.get("htmlLink"), "hangout_link": body.get("hangoutLink")}
            if context.tenant_id:
                await append_event(context.db, tenant_id=context.tenant_id, event_type="calendar.event_created", payload=evidence, source="google_calendar")
            return CapabilityResult(True, True, evidence, body["id"])

        if capability_name == "calendar.update_event":
            event_id = quote(arguments["event_id"], safe="")
            payload = {}
            if "summary" in arguments:
                payload["summary"] = arguments["summary"]
            if "description" in arguments:
                payload["description"] = arguments["description"]
            if "location" in arguments:
                payload["location"] = arguments["location"]
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
            body = await request_json("PATCH", f"https://www.googleapis.com/calendar/v3/calendars/{encoded_calendar}/events/{event_id}", token, payload)
            evidence = {"event_id": body.get("id") or arguments["event_id"], "calendar_id": calendar_id, "provider_status": body.get("status"), "html_link": body.get("htmlLink")}
            if context.tenant_id:
                await append_event(context.db, tenant_id=context.tenant_id, event_type="calendar.event_updated", payload=evidence, source="google_calendar")
            return CapabilityResult(True, True, evidence, evidence["event_id"])

        if capability_name == "calendar.delete_event":
            event_id = quote(arguments["event_id"], safe="")
            await request_json("DELETE", f"https://www.googleapis.com/calendar/v3/calendars/{encoded_calendar}/events/{event_id}", token)
            evidence = {"event_id": arguments["event_id"], "calendar_id": calendar_id, "deleted": True}
            if context.tenant_id:
                await append_event(context.db, tenant_id=context.tenant_id, event_type="calendar.event_deleted", payload=evidence, source="google_calendar")
            return CapabilityResult(True, True, evidence, arguments["event_id"])

        return CapabilityResult(False, False, {"reason": "unsupported_calendar_capability"})

    async def verify(self, context, capability_name, arguments, result):
        if capability_name in {"calendar.list_events", "calendar.freebusy", "calendar.list_calendars"}:
            return CapabilityResult(result.success, False, {"observation_available": result.success, **result.evidence})
        if capability_name == "calendar.delete_event":
            return CapabilityResult(bool(result.evidence.get("deleted")), result.changed, result.evidence)
        return CapabilityResult(bool(result.evidence.get("event_id")), result.changed, result.evidence)