"""Deterministic calendar deadline/conflict assessment.

This capability deliberately separates a date-only deadline from an exact time
window. Merely sharing a calendar date is never reported as an exact conflict.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

from packages.capabilities.contracts import (
    ApprovalPolicy,
    CapabilityDefinition,
    CapabilityResult,
    ExecutionMode,
)
from packages.capabilities.providers import BaseProvider
from packages.connectors.google_provider import CALENDAR, access_token, google_connector, request_json


def _parse_date(value: str) -> date:
    return date.fromisoformat(str(value).strip())


def _zone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(str(value or "UTC"))
    except Exception:
        return ZoneInfo("UTC")


def _event_window(event: dict, zone: ZoneInfo) -> tuple[datetime, datetime] | None:
    start = event.get("start") if isinstance(event.get("start"), dict) else {}
    end = event.get("end") if isinstance(event.get("end"), dict) else {}
    start_raw = start.get("dateTime")
    end_raw = end.get("dateTime")
    if not start_raw or not end_raw:
        return None
    try:
        start_dt = datetime.fromisoformat(str(start_raw).replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(str(end_raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=zone)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=zone)
    return start_dt.astimezone(zone), end_dt.astimezone(zone)


class CalendarSemanticsProvider(BaseProvider):
    name = "google_calendar_semantics"
    capabilities = (
        CapabilityDefinition(
            "calendar.assess_deadline_conflicts",
            "calendar_assess_deadline_conflicts",
            "Assess whether a deadline has an exact calendar conflict; date-only deadlines are never treated as exact conflicts.",
            {
                "type": "object",
                "properties": {
                    "deadline": {"type": "string", "minLength": 10, "maxLength": 10},
                    "deadline_time": {"type": "string", "minLength": 5, "maxLength": 8},
                    "duration_minutes": {"type": "integer", "minimum": 1, "maximum": 720},
                    "timezone": {"type": "string", "minLength": 1, "maxLength": 128},
                    "calendar_id": {"type": "string", "maxLength": 512},
                },
                "required": ["deadline", "timezone"],
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
            category="calendar",
            tags=frozenset({"calendar", "deadline", "conflict", "deterministic"}),
        ),
    )

    async def execute(self, context, capability_name, arguments):
        if capability_name != "calendar.assess_deadline_conflicts":
            return CapabilityResult(False, False, {"reason": "unsupported_calendar_semantics_capability"})

        deadline = _parse_date(arguments["deadline"])
        zone = _zone(arguments.get("timezone") or "UTC")
        calendar_id = str(arguments.get("calendar_id") or "primary")
        start_of_day = datetime.combine(deadline, time.min, tzinfo=zone)
        end_of_day = start_of_day + timedelta(days=1)

        connector = await google_connector(context.db, context.tenant_id, CALENDAR)
        token = await access_token(context.db, connector)
        listing = await request_json(
            "GET",
            f"https://www.googleapis.com/calendar/v3/calendars/{quote(calendar_id, safe='')}/events",
            token,
            params={
                "timeMin": start_of_day.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "timeMax": end_of_day.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": 50,
            },
        )
        events = [
            {
                "id": item.get("id"),
                "summary": item.get("summary"),
                "start": item.get("start"),
                "end": item.get("end"),
                "status": item.get("status"),
            }
            for item in (listing.get("items") or [])[:50]
        ]

        deadline_time = str(arguments.get("deadline_time") or "").strip()
        if not deadline_time:
            return CapabilityResult(
                True,
                False,
                {
                    "deadline": deadline.isoformat(),
                    "deadline_time": None,
                    "timezone": zone.key,
                    "events": events,
                    "assessment": "DATE_ONLY_NO_EXACT_CONFLICT",
                    "exact_conflicts": [],
                },
            )

        try:
            parsed_time = time.fromisoformat(deadline_time)
        except ValueError:
            return CapabilityResult(False, False, {"reason": "invalid_deadline_time"})
        target_start = datetime.combine(deadline, parsed_time, tzinfo=zone)
        target_end = target_start + timedelta(minutes=int(arguments.get("duration_minutes", 30)))
        conflicts = []
        for event in events:
            window = _event_window(event, zone)
            if window is None:
                # All-day/date-only entries are context, not an automatic exact-time collision.
                continue
            event_start, event_end = window
            if event_start < target_end and event_end > target_start:
                conflicts.append(event)

        return CapabilityResult(
            True,
            False,
            {
                "deadline": deadline.isoformat(),
                "deadline_time": parsed_time.isoformat(),
                "duration_minutes": int(arguments.get("duration_minutes", 30)),
                "timezone": zone.key,
                "events": events,
                "assessment": "EXACT_CONFLICT" if conflicts else "NO_EXACT_CONFLICT",
                "exact_conflicts": conflicts,
                "target_start": target_start.isoformat(),
                "target_end": target_end.isoformat(),
            },
        )

    async def verify(self, context, capability_name, arguments, result):
        return CapabilityResult(
            bool(result.success and result.evidence.get("assessment")),
            False,
            {
                "assessment": result.evidence.get("assessment"),
                "deterministic": True,
            },
        )
