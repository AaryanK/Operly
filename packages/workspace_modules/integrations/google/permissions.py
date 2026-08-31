from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from packages.kernel.contracts import CapabilitySpec
from packages.security.execution_context import ExecutionContext
from packages.workspace_modules.integrations.google.provider import (
    CALENDAR,
    CALENDAR_FREEBUSY,
    CALENDAR_LIST_READONLY,
    GMAIL_MODIFY,
    GMAIL_READ_SCOPES,
    GMAIL_SEND_SCOPES,
    WorkspaceGoogleProvider,
    _google_connectors,
    _scopes,
)


class AvailableWorkspaceGoogleProvider(WorkspaceGoogleProvider):
    """Resolve both Operly authority and the live Google OAuth grant."""

    async def is_available(
        self,
        db: AsyncSession,
        *,
        context: ExecutionContext,
        capability: CapabilitySpec,
    ) -> bool:
        if not context.workspace_id:
            return False
        if capability.id == "google.connection.status":
            return True

        rows = await _google_connectors(db, context.workspace_id)
        if not rows:
            return False

        def has_required(required: set[str]) -> bool:
            return any(required.issubset(_scopes(row)) for row in rows)

        def has_any(acceptable: set[str]) -> bool:
            return any(bool(_scopes(row) & acceptable) for row in rows)

        if capability.id in {"google.gmail.search", "google.gmail.read_message"}:
            return has_any(GMAIL_READ_SCOPES)
        if capability.id == "google.gmail.send_email":
            return has_any(GMAIL_SEND_SCOPES)
        if capability.id in {"google.gmail.create_draft", "google.gmail.modify_labels"}:
            return has_required({GMAIL_MODIFY})
        if capability.id == "google.calendar.list_calendars":
            return has_required({CALENDAR_LIST_READONLY})
        if capability.id == "google.calendar.freebusy":
            return has_required({CALENDAR_FREEBUSY})
        if capability.id.startswith("google.calendar."):
            return has_required({CALENDAR})
        return False
