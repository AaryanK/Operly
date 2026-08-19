import os
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


CALENDAR_TIME_CAPABILITIES = {
    "calendar.create_event",
    "calendar.update_event",
    "calendar.list_events",
    "calendar.freebusy",
}


def resolve_timezone(requested: str | None = None) -> str:
    """Resolve a valid IANA timezone without trusting the model to choose one."""
    candidates = [
        str(requested or "").strip(),
        os.getenv("DEFAULT_TIMEZONE", "").strip(),
        "UTC",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            ZoneInfo(candidate)
            return candidate
        except ZoneInfoNotFoundError:
            continue
    return "UTC"


def user_time_context(requested: str | None = None) -> dict[str, str]:
    timezone_name = resolve_timezone(requested)
    now = datetime.now(ZoneInfo(timezone_name))
    return {
        "user_timezone": timezone_name,
        "user_local_now": now.isoformat(timespec="seconds"),
        "user_local_date": now.date().isoformat(),
    }


def normalize_datetime(value: Any, timezone_name: str) -> Any:
    """Attach the resolved user timezone to naive ISO datetimes.

    Offset-aware model output is preserved. Non-ISO values are left untouched so
    normal schema/provider validation can reject them with a useful error.
    """
    if not isinstance(value, str) or not value.strip():
        return value
    source = value.strip()
    parse_value = source[:-1] + "+00:00" if source.endswith("Z") else source
    try:
        parsed = datetime.fromisoformat(parse_value)
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
    return parsed.isoformat()


def normalize_calendar_arguments(
    capability_name: str,
    arguments: dict[str, Any],
    *,
    requested_timezone: str | None,
) -> dict[str, Any]:
    """Freeze user-local calendar times before an approval is created.

    Approval execution happens later and runtime-only channel metadata is not
    persisted with the action. Normalizing here means the exact offset-aware
    values the user approves are the values sent to Google.
    """
    result = dict(arguments)
    if capability_name not in CALENDAR_TIME_CAPABILITIES:
        return result

    timezone_name = resolve_timezone(requested_timezone)
    for key in ("start", "end", "time_min", "time_max"):
        if key in result:
            result[key] = normalize_datetime(result[key], timezone_name)

    if capability_name in {"calendar.create_event", "calendar.update_event"}:
        result.setdefault("time_zone", timezone_name)

    return result
