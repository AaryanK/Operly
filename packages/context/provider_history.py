from __future__ import annotations

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
