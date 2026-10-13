"""Admin SSE transport safety tests that do not require a running PostgreSQL."""

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from apps.api.admin_events import _sse, admin_event_stream


class FakeListener:
    def __init__(self):
        self.callback = None
        self.closed = False

    async def add_listener(self, channel, callback):
        self.callback = callback
        self.channel = channel

    async def remove_listener(self, channel, callback):
        assert callback == self.callback
        assert channel == self.channel

    def is_closed(self):
        return self.closed

    async def close(self):
        self.closed = True


class LiveRequest:
    async def is_disconnected(self):
        return False


class AdminEventStreamTests(unittest.IsolatedAsyncioTestCase):
    def test_sse_frames_only_contain_topic_payload(self):
        self.assertEqual(
            _sse("invalidate", {"scope": "ai-usage"}),
            'event: invalidate\\ndata: {"scope":"ai-usage"}\\n\\n'.replace("\\n", "\n"),
        )

    async def test_sqlite_fails_closed_instead_of_fake_live_updates(self):
        with patch("apps.api.admin_events.DATABASE_URL", "sqlite+aiosqlite:///./test.db"):
            with self.assertRaises(HTTPException) as raised:
                await admin_event_stream(LiveRequest(), session_id="id", admin_email="admin@example.com")
        self.assertEqual(raised.exception.status_code, 503)

    async def test_postgres_notification_reaches_sse_and_listener_is_cleaned_up(self):
        listener = FakeListener()
        with (
            patch("apps.api.admin_events._postgres_dsn", return_value="postgresql://example/test"),
            patch("apps.api.admin_events.asyncpg.connect", new=AsyncMock(return_value=listener)),
        ):
            response = await admin_event_stream(
                LiveRequest(), session_id="id", admin_email="admin@example.com"
            )
            self.assertEqual(response.media_type, "text/event-stream")
            iterator = response.body_iterator
            hello = await anext(iterator)
            self.assertIn("event: ready", hello)
            self.assertEqual(listener.channel, "operly_admin_invalidation")

            listener.callback(None, 0, listener.channel, "ai-usage")
            frame = await anext(iterator)
            self.assertIn('data: {"scope":"ai-usage"}', frame)
            self.assertNotIn("admin@example.com", frame)

            await iterator.aclose()
            self.assertTrue(listener.closed)

    async def test_invalid_topics_are_not_forwarded(self):
        listener = FakeListener()
        with (
            patch("apps.api.admin_events._postgres_dsn", return_value="postgresql://example/test"),
            patch("apps.api.admin_events.asyncpg.connect", new=AsyncMock(return_value=listener)),
            patch("apps.api.admin_events.HEARTBEAT_SECONDS", 0.01),
            patch("apps.api.admin_events._session_is_admin", new=AsyncMock(return_value=True)),
        ):
            response = await admin_event_stream(
                LiveRequest(), session_id="id", admin_email="admin@example.com"
            )
            iterator = response.body_iterator
            await anext(iterator)
            listener.callback(None, 0, listener.channel, "email=secret@example.com")
            frame = await anext(iterator)
            self.assertEqual(frame, ": keep-alive\n\n")
            await iterator.aclose()
            self.assertTrue(listener.closed)

    async def test_revocation_ends_authenticated_stream(self):
        listener = FakeListener()
        with (
            patch("apps.api.admin_events._postgres_dsn", return_value="postgresql://example/test"),
            patch("apps.api.admin_events.asyncpg.connect", new=AsyncMock(return_value=listener)),
            patch("apps.api.admin_events.HEARTBEAT_SECONDS", 0.01),
            patch("apps.api.admin_events._session_is_admin", new=AsyncMock(return_value=False)),
        ):
            response = await admin_event_stream(
                LiveRequest(), session_id="revoked", admin_email="admin@example.com"
            )
            iterator = response.body_iterator
            await anext(iterator)
            self.assertEqual(await anext(iterator), _sse("auth-expired"))
            with self.assertRaises(StopAsyncIteration):
                await anext(iterator)
            self.assertTrue(listener.closed)


if __name__ == "__main__":
    unittest.main()
