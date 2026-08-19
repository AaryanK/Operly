import os
import unittest
from unittest.mock import patch

from packages.capabilities.time_context import (
    normalize_calendar_arguments,
    normalize_datetime,
    resolve_timezone,
    user_time_context,
)


class CalendarTimeContextTests(unittest.TestCase):
    def test_naive_calendar_times_use_browser_timezone_before_approval(self):
        arguments = normalize_calendar_arguments(
            "calendar.create_event",
            {
                "summary": "Operly Test",
                "start": "2026-08-20T15:00:00",
                "end": "2026-08-20T16:00:00",
            },
            requested_timezone="America/Chicago",
        )
        self.assertEqual(arguments["time_zone"], "America/Chicago")
        self.assertEqual(arguments["start"], "2026-08-20T15:00:00-05:00")
        self.assertEqual(arguments["end"], "2026-08-20T16:00:00-05:00")

    def test_existing_offset_is_preserved(self):
        value = normalize_datetime("2026-08-20T15:00:00-05:00", "America/New_York")
        self.assertEqual(value, "2026-08-20T15:00:00-05:00")

    def test_zulu_time_stays_utc(self):
        value = normalize_datetime("2026-08-20T20:00:00Z", "America/Chicago")
        self.assertEqual(value, "2026-08-20T20:00:00+00:00")

    def test_invalid_browser_timezone_uses_configured_fallback(self):
        with patch.dict(os.environ, {"DEFAULT_TIMEZONE": "America/Chicago"}):
            self.assertEqual(resolve_timezone("Not/AZone"), "America/Chicago")
            context = user_time_context("Not/AZone")
            self.assertEqual(context["user_timezone"], "America/Chicago")
            self.assertRegex(context["user_local_now"], r"-05:00$|-06:00$")

    def test_non_calendar_arguments_are_unchanged(self):
        original = {"query": "Acme"}
        normalized = normalize_calendar_arguments(
            "gmail.search",
            original,
            requested_timezone="America/Chicago",
        )
        self.assertEqual(normalized, original)


if __name__ == "__main__":
    unittest.main()
