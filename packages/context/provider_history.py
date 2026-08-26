from __future__ import annotations

import json
from urllib.parse import quote

from sqlalchemy import select

from packages.capabilities.contracts import (
    ApprovalPolicy,
    CapabilityDefinition,
    CapabilityResult,
    ExecutionMode,
)
from packages.capabilities.providers import BaseProvider
from packages.connectors.google_provider import (
    CALENDAR,
    GMAIL_READ_SCOPES,
    GMAIL_READONLY,
    _headers,
    _message_bodies,
    request_json,
)
from packages.connectors.google_scope import connector_scopes, google_access_token_for_context
from packages.database.account_connector_models import AccountConnector


class PersonalGmailHistoryProvider(BaseProvider):
    """Internal, account-explicit Gmail retrieval used by federated context.

    The ordinary Gmail capability intentionally resolves one eligible connector. A
    federated search must not silently pick the first account, so this provider
    requires one durable AccountConnector id per invocation and revalidates ownership,
    connection state and OAuth read scope before touching Google.
    """

    name = "personal_gmail_history"
    capabilities = (
        CapabilityDefinition(
            "history.gmail.search_account",
            "history_gmail_search_account",
            "Search one explicitly selected, account-owned Gmail mailbox for federated history retrieval.",
            {
                "type": "object",
                "properties": {
                    "connector_id": {"type": "string", "minLength": 1, "maxLength": 64},
                    "query": {"type": "string", "minLength": 1, "maxLength": 1000},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                "required": ["connector_id", "query"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="read_only",
            permissions=("messaging:read",),
            approval_policy=ApprovalPolicy.AUTO,
            execution_mode=ExecutionMode.EXTERNAL,
            source="external",
            provider="google",
            integration_provider="google",
            credential_scopes=(GMAIL_READONLY,),
            category="context",
            tags=("history", "gmail", "federated"),
            semantic_operations=("search history", "search email", "retrieve email context"),
        ),
        CapabilityDefinition(
            "history.gmail.read_account_message",
            "history_gmail_read_account_message",
            "Read one message from one explicitly selected, account-owned Gmail mailbox for federated history materialization.",
            {
                "type": "object",
                "properties": {
                    "connector_id": {"type": "string", "minLength": 1, "maxLength": 64},
                    "message_id": {"type": "string", "minLength": 1, "maxLength": 200},
                },
                "required": ["connector_id", "message_id"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="read_only",
            permissions=("messaging:read",),
            approval_policy=ApprovalPolicy.AUTO,
            execution_mode=ExecutionMode.EXTERNAL,
            source="external",
            provider="google",
            integration_provider="google",
            credential_scopes=(GMAIL_READONLY,),
            category="context",
            tags=("history", "gmail", "federated"),
            semantic_operations=("read history", "read email context"),
        ),
    )

    @staticmethod
    async def _connector(context, connector_id: str) -> AccountConnector:
        owner_user_id = str(getattr(context, "owner_user_id", "") or "").strip()
        actor_id = str(getattr(context, "actor_id", "") or "").strip()
        if not owner_user_id or actor_id != owner_user_id:
            raise PermissionError("Personal Gmail history authority is unavailable")
        connector = await context.db.scalar(
            select(AccountConnector).where(
                AccountConnector.id == str(connector_id),
                AccountConnector.user_id == owner_user_id,
                AccountConnector.provider == "google",
                AccountConnector.enabled.is_(True),
                AccountConnector.status == "connected",
            )
        )
        if connector is None:
            raise PermissionError("Google account is unavailable")
        if not (connector_scopes(connector) & GMAIL_READ_SCOPES):
            raise PermissionError("Google account does not grant Gmail read authority")
        return connector

    async def execute(self, context, capability_name, arguments):
        connector = await self._connector(context, arguments["connector_id"])
        token = await google_access_token_for_context(context, connector)

        if capability_name == "history.gmail.search_account":
            limit = max(1, min(int(arguments.get("limit", 10)), 10))
            listing = await request_json(
                "GET",
                "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                token,
                params={"q": arguments["query"], "maxResults": limit},
            )
            messages = []
            for item in (listing.get("messages") or [])[:limit]:
                detail = await request_json(
                    "GET",
                    f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{quote(item['id'], safe='')}",
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
                        "snippet": detail.get("snippet", "")[:500],
                        "label_ids": detail.get("labelIds", []),
                    }
                )
            return CapabilityResult(
                True,
                False,
                {
                    "connector_id": connector.id,
                    "provider_account_id": connector.provider_account_id,
                    "account_display_name": connector.display_name,
                    "query": arguments["query"],
                    "messages": messages,
                },
            )

        if capability_name == "history.gmail.read_account_message":
            detail = await request_json(
                "GET",
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{quote(arguments['message_id'], safe='')}",
                token,
                params={"format": "full"},
            )
            headers = _headers(detail.get("payload") or {})
            plain, rich = _message_bodies(detail.get("payload") or {})
            return CapabilityResult(
                True,
                False,
                {
                    "connector_id": connector.id,
                    "provider_account_id": connector.provider_account_id,
                    "account_display_name": connector.display_name,
                    "id": detail.get("id"),
                    "thread_id": detail.get("threadId"),
                    "from": headers.get("from"),
                    "to": headers.get("to"),
                    "cc": headers.get("cc"),
                    "subject": headers.get("subject"),
                    "date": headers.get("date"),
                    "snippet": detail.get("snippet", "")[:1000],
                    "text_body": plain,
                    "html_body": rich,
                    "label_ids": detail.get("labelIds", []),
                },
            )

        return CapabilityResult(False, False, {"reason": "unsupported_history_capability"})

    async def verify(self, context, capability_name, arguments, result):
        del context, arguments
        if capability_name == "history.gmail.search_account":
            valid = result.success and isinstance(result.evidence.get("messages"), list)
        elif capability_name == "history.gmail.read_account_message":
            valid = result.success and bool(result.evidence.get("id"))
        else:
            valid = False
        return CapabilityResult(valid, False, result.evidence)


class PersonalGoogleCalendarHistoryProvider(BaseProvider):
    """Account-explicit Google Calendar history provider for federation.

    Calendar federation cannot use the ordinary calendar provider because that resolver
    intentionally picks one eligible account. This provider requires a connector id on
    every call and revalidates ownership plus Calendar scope before provider access.
    """

    name = "personal_google_calendar_history"
    capabilities = (
        CapabilityDefinition(
            "history.calendar.search_account",
            "history_calendar_search_account",
            "Search one explicitly selected, account-owned Google Calendar for federated history retrieval.",
            {
                "type": "object",
                "properties": {
                    "connector_id": {"type": "string", "minLength": 1, "maxLength": 64},
                    "query": {"type": "string", "minLength": 1, "maxLength": 1000},
                    "time_min": {"type": "string", "minLength": 1, "maxLength": 100},
                    "time_max": {"type": "string", "minLength": 1, "maxLength": 100},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                    "calendar_id": {"type": "string", "minLength": 1, "maxLength": 320},
                },
                "required": ["connector_id", "query", "time_min", "time_max"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="read_only",
            permissions=("calendar:read",),
            approval_policy=ApprovalPolicy.AUTO,
            execution_mode=ExecutionMode.EXTERNAL,
            source="external",
            provider="google",
            integration_provider="google",
            credential_scopes=(CALENDAR,),
            category="context",
            tags=("history", "calendar", "federated"),
            semantic_operations=("search history", "search calendar", "retrieve calendar context"),
        ),
        CapabilityDefinition(
            "history.calendar.read_account_event",
            "history_calendar_read_account_event",
            "Read one event from one explicitly selected, account-owned Google Calendar for federated history materialization.",
            {
                "type": "object",
                "properties": {
                    "connector_id": {"type": "string", "minLength": 1, "maxLength": 64},
                    "calendar_id": {"type": "string", "minLength": 1, "maxLength": 320},
                    "event_id": {"type": "string", "minLength": 1, "maxLength": 1024},
                },
                "required": ["connector_id", "calendar_id", "event_id"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="read_only",
            permissions=("calendar:read",),
            approval_policy=ApprovalPolicy.AUTO,
            execution_mode=ExecutionMode.EXTERNAL,
            source="external",
            provider="google",
            integration_provider="google",
            credential_scopes=(CALENDAR,),
            category="context",
            tags=("history", "calendar", "federated"),
            semantic_operations=("read history", "read calendar context"),
        ),
    )

    @staticmethod
    async def _connector(context, connector_id: str) -> AccountConnector:
        owner_user_id = str(getattr(context, "owner_user_id", "") or "").strip()
        actor_id = str(getattr(context, "actor_id", "") or "").strip()
        if not owner_user_id or actor_id != owner_user_id:
            raise PermissionError("Personal Calendar history authority is unavailable")
        connector = await context.db.scalar(
            select(AccountConnector).where(
                AccountConnector.id == str(connector_id),
                AccountConnector.user_id == owner_user_id,
                AccountConnector.provider == "google",
                AccountConnector.enabled.is_(True),
                AccountConnector.status == "connected",
            )
        )
        if connector is None:
            raise PermissionError("Google account is unavailable")
        if CALENDAR not in connector_scopes(connector):
            raise PermissionError("Google account does not grant Calendar read authority")
        return connector

    @staticmethod
    def _calendar_id(connector: AccountConnector, arguments: dict) -> str:
        try:
            configuration = json.loads(connector.configuration_json or "{}")
        except (TypeError, json.JSONDecodeError):
            configuration = {}
        return str(arguments.get("calendar_id") or configuration.get("calendar_id") or "primary")

    @staticmethod
    def _event_payload(item: dict) -> dict:
        return {
            "id": item.get("id"),
            "summary": item.get("summary"),
            "description": item.get("description"),
            "location": item.get("location"),
            "start": item.get("start"),
            "end": item.get("end"),
            "attendees": [
                {
                    "email": person.get("email"),
                    "display_name": person.get("displayName"),
                    "response_status": person.get("responseStatus"),
                }
                for person in item.get("attendees", [])
            ],
            "status": item.get("status"),
            "created": item.get("created"),
            "updated": item.get("updated"),
            "html_link": item.get("htmlLink"),
            "hangout_link": item.get("hangoutLink"),
            "organizer": item.get("organizer"),
            "creator": item.get("creator"),
        }

    async def execute(self, context, capability_name, arguments):
        connector = await self._connector(context, arguments["connector_id"])
        calendar_id = self._calendar_id(connector, arguments)
        encoded_calendar = quote(calendar_id, safe="")
        token = await google_access_token_for_context(context, connector)

        if capability_name == "history.calendar.search_account":
            limit = max(1, min(int(arguments.get("limit", 20)), 20))
            params = {
                "timeMin": arguments["time_min"],
                "timeMax": arguments["time_max"],
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": limit,
                "q": arguments["query"],
            }
            body = await request_json(
                "GET",
                f"https://www.googleapis.com/calendar/v3/calendars/{encoded_calendar}/events",
                token,
                params=params,
            )
            return CapabilityResult(
                True,
                False,
                {
                    "connector_id": connector.id,
                    "provider_account_id": connector.provider_account_id,
                    "account_display_name": connector.display_name,
                    "calendar_id": calendar_id,
                    "query": arguments["query"],
                    "events": [self._event_payload(item) for item in body.get("items", [])],
                },
            )

        if capability_name == "history.calendar.read_account_event":
            event_id = quote(arguments["event_id"], safe="")
            item = await request_json(
                "GET",
                f"https://www.googleapis.com/calendar/v3/calendars/{encoded_calendar}/events/{event_id}",
                token,
            )
            return CapabilityResult(
                True,
                False,
                {
                    "connector_id": connector.id,
                    "provider_account_id": connector.provider_account_id,
                    "account_display_name": connector.display_name,
                    "calendar_id": calendar_id,
                    **self._event_payload(item),
                },
            )

        return CapabilityResult(False, False, {"reason": "unsupported_history_capability"})

    async def verify(self, context, capability_name, arguments, result):
        del context, arguments
        if capability_name == "history.calendar.search_account":
            valid = result.success and isinstance(result.evidence.get("events"), list)
        elif capability_name == "history.calendar.read_account_event":
            valid = result.success and bool(result.evidence.get("id"))
        else:
            valid = False
        return CapabilityResult(valid, False, result.evidence)
