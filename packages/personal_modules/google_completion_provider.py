from __future__ import annotations

from email.utils import getaddresses
from typing import Any
from urllib.parse import quote

from sqlalchemy.ext.asyncio import AsyncSession

from packages.kernel.contracts import CapabilityExecutionResult, CapabilityRisk, CapabilitySpec
from packages.personal_modules.google_provider import (
    GMAIL_READ_SCOPES,
    PROVIDER_ID,
    PersonalGoogleConnectorRequired,
    PersonalGoogleProvider,
    _array,
    _headers,
    _message_bodies,
    _object,
    access_token,
    connector_scopes,
    personal_google_connectors,
    request_json,
)
from packages.security.execution_context import ExecutionContext


CONTACTS_READONLY = "https://www.googleapis.com/auth/contacts.readonly"
CONTACT_SEARCH = "google.people.search_contacts"
GMAIL_READ_DRAFT = "google.gmail.read_draft"
P3_CAPABILITY_IDS = frozenset({CONTACT_SEARCH, GMAIL_READ_DRAFT})


def completion_google_capabilities() -> tuple[CapabilitySpec, ...]:
    connector = {"connector_id": {"type": "string", "maxLength": 80}}
    contact = _object(
        {
            "resource_name": {"type": "string"},
            "display_name": {"type": "string"},
            "emails": _array({"type": "string"}, max_items=20),
        },
        required=["resource_name", "display_name", "emails"],
    )
    return (
        CapabilitySpec(
            id=CONTACT_SEARCH,
            version="1.0.0",
            display_name="Search personal Google contacts",
            description=(
                "Search verified contacts owned by the authenticated user's connected "
                "Google account and return only normalized names and email addresses."
            ),
            provider_id=PROVIDER_ID,
            scopes=frozenset({"personal"}),
            input_schema=_object(
                {
                    **connector,
                    "query": {"type": "string", "minLength": 1, "maxLength": 200},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                },
                required=["query"],
            ),
            output_schema=_object(
                {"contacts": _array(contact, max_items=20)},
                required=["contacts"],
            ),
            permissions=("contacts:read",),
            risk=CapabilityRisk.READ_ONLY,
            resource_scope="personal",
            tags=frozenset(
                {"personal", "google", "contacts", "people", "search", "recipient", "read"}
            ),
        ),
        CapabilitySpec(
            id=GMAIL_READ_DRAFT,
            version="1.0.0",
            display_name="Read personal Gmail draft",
            description=(
                "Retrieve one saved Gmail draft by provider draft ID so recipient, subject "
                "and body can be verified after creation without sending it."
            ),
            provider_id=PROVIDER_ID,
            scopes=frozenset({"personal"}),
            input_schema=_object(
                {
                    **connector,
                    "draft_id": {"type": "string", "minLength": 1, "maxLength": 200},
                },
                required=["draft_id"],
            ),
            output_schema=_object({}, additional=True),
            permissions=("messaging:read",),
            risk=CapabilityRisk.READ_ONLY,
            resource_scope="personal",
            tags=frozenset(
                {"personal", "google", "gmail", "mail", "draft", "read", "verify"}
            ),
        ),
    )


def completion_supported_capability_ids(scopes: set[str]) -> list[str]:
    result: list[str] = []
    if CONTACTS_READONLY in scopes:
        result.append(CONTACT_SEARCH)
    if scopes & GMAIL_READ_SCOPES:
        result.append(GMAIL_READ_DRAFT)
    return result


def _supports(capability_id: str, scopes: set[str]) -> bool:
    if capability_id == CONTACT_SEARCH:
        return CONTACTS_READONLY in scopes
    if capability_id == GMAIL_READ_DRAFT:
        return bool(scopes & GMAIL_READ_SCOPES)
    return False


async def _connector(
    db: AsyncSession,
    context: ExecutionContext,
    capability_id: str,
    requested_id: str | None,
):
    if not context.is_personal or not context.user_id:
        raise PermissionError("Personal Google capability requires Personal authority")
    rows = [
        row
        for row in await personal_google_connectors(db, context.user_id)
        if _supports(capability_id, connector_scopes(row))
    ]
    requested = str(requested_id or "").strip()
    if requested:
        for row in rows:
            if row.id == requested:
                return row
        raise PersonalGoogleConnectorRequired(
            "Requested personal Google connector is unavailable or lacks the required scope"
        )
    if not rows:
        raise PersonalGoogleConnectorRequired(
            "Connect Personal Google and grant the required permission tier"
        )
    if len(rows) > 1:
        raise PersonalGoogleConnectorRequired(
            "Multiple Personal Google accounts match; specify connector_id"
        )
    return rows[0]


def _normalized_contact(person: dict[str, Any]) -> dict[str, Any] | None:
    names = [item for item in person.get("names") or [] if isinstance(item, dict)]
    emails = [item for item in person.get("emailAddresses") or [] if isinstance(item, dict)]
    display_name = next(
        (str(item.get("displayName") or "").strip() for item in names if item.get("displayName")),
        "",
    )
    values = sorted(
        {
            str(item.get("value") or "").strip().lower()
            for item in emails
            if str(item.get("value") or "").strip()
        }
    )
    resource_name = str(person.get("resourceName") or "").strip()
    if not resource_name or not display_name or not values:
        return None
    return {
        "resource_name": resource_name,
        "display_name": display_name,
        "emails": values[:20],
    }


def _matches_contact(contact: dict[str, Any], query: str) -> bool:
    needle = " ".join(str(query or "").lower().split())
    if not needle:
        return False
    name = " ".join(str(contact.get("display_name") or "").lower().split())
    if needle in name:
        return True
    return any(needle in str(value).lower() for value in contact.get("emails") or [])


def _recipient_list(value: str | None) -> list[str]:
    return sorted(
        {
            address.strip().lower()
            for _, address in getaddresses([str(value or "")])
            if address.strip()
        }
    )


class PersonalGoogleCompletionProvider(PersonalGoogleProvider):
    """Personal Google provider with the P3 read/verification operations."""

    async def is_available(
        self,
        db: AsyncSession,
        *,
        context: ExecutionContext,
        capability: CapabilitySpec,
    ) -> bool:
        if capability.id not in P3_CAPABILITY_IDS:
            return await super().is_available(db, context=context, capability=capability)
        if not context.is_personal or not context.user_id:
            return False
        rows = await personal_google_connectors(db, context.user_id)
        return any(_supports(capability.id, connector_scopes(row)) for row in rows)

    async def execute(
        self,
        db: AsyncSession,
        *,
        context: ExecutionContext,
        capability: CapabilitySpec,
        arguments: dict[str, Any],
        minimum_context: dict[str, Any],
    ) -> CapabilityExecutionResult:
        if capability.id not in P3_CAPABILITY_IDS:
            return await super().execute(
                db,
                context=context,
                capability=capability,
                arguments=arguments,
                minimum_context=minimum_context,
            )

        del minimum_context
        connector = await _connector(
            db,
            context,
            capability.id,
            str(arguments.get("connector_id") or "") or None,
        )
        token = await access_token(db, connector)

        if capability.id == CONTACT_SEARCH:
            query = " ".join(str(arguments["query"]).split())
            limit = max(1, min(int(arguments.get("limit") or 10), 20))
            matches: list[dict[str, Any]] = []
            page_token: str | None = None
            pages = 0
            while pages < 3 and len(matches) < limit:
                params: dict[str, Any] = {
                    "personFields": "names,emailAddresses",
                    "pageSize": 500,
                    "sortOrder": "FIRST_NAME_ASCENDING",
                }
                if page_token:
                    params["pageToken"] = page_token
                body = await request_json(
                    "GET",
                    "https://people.googleapis.com/v1/people/me/connections",
                    token,
                    params=params,
                )
                for raw in body.get("connections") or []:
                    if not isinstance(raw, dict):
                        continue
                    normalized = _normalized_contact(raw)
                    if normalized and _matches_contact(normalized, query):
                        matches.append(normalized)
                        if len(matches) >= limit:
                            break
                page_token = str(body.get("nextPageToken") or "").strip() or None
                pages += 1
                if not page_token:
                    break
            return CapabilityExecutionResult(
                value={"contacts": matches[:limit]},
                resource_type="personal_google_contacts",
                resource_id=connector.id,
            )

        draft_id = str(arguments["draft_id"]).strip()
        body = await request_json(
            "GET",
            "https://gmail.googleapis.com/gmail/v1/users/me/drafts/"
            + quote(draft_id, safe=""),
            token,
            params={"format": "full"},
        )
        message = body.get("message") if isinstance(body.get("message"), dict) else {}
        payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
        headers = _headers(payload)
        plain, rich = _message_bodies(payload)
        return CapabilityExecutionResult(
            value={
                "draft_id": str(body.get("id") or draft_id),
                "message_id": message.get("id"),
                "thread_id": message.get("threadId"),
                "to": _recipient_list(headers.get("to")),
                "cc": _recipient_list(headers.get("cc")),
                "subject": str(headers.get("subject") or ""),
                "text_body": plain,
                "html_body": rich,
                "provider_account": connector.provider_account_id,
            },
            resource_type="gmail_draft",
            resource_id=str(body.get("id") or draft_id),
        )
