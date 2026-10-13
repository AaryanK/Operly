"""Authenticated, one-way admin invalidations with PostgreSQL LISTEN/NOTIFY.

The stream sends topic names only; all data remains behind the existing admin
GET endpoints. A dedicated asyncpg connection receives notifications from API
and worker transactions, including writes made by other processes/replicas.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import suppress
from datetime import datetime

import asyncpg
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from packages.database.db import DATABASE_URL, SessionFactory
from packages.database.models import AppUser, AuthSession

logger = logging.getLogger(__name__)

CHANNEL = "operly_admin_invalidation"
TOPICS = frozenset({"overview", "ai-usage", "users", "workspaces", "all"})
HEARTBEAT_SECONDS = 15
OVERVIEW_ROLLING_SECONDS = 60
MAX_QUEUE_SIZE = 64


def _postgres_dsn() -> str | None:
    if DATABASE_URL.startswith("postgresql+asyncpg://"):
        return "postgresql://" + DATABASE_URL.split("://", 1)[1]
    return None


def _sse(event: str, payload: dict | None = None) -> str:
    return f"event: {event}\ndata: {json.dumps(payload or {}, separators=(',', ':'))}\n\n"


async def _session_is_admin(session_id: str, admin_email: str) -> bool:
    async with SessionFactory() as db:
        session = await db.scalar(
            select(AuthSession.id)
            .join(AppUser, AppUser.id == AuthSession.user_id)
            .where(
                AuthSession.id == session_id,
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > datetime.utcnow(),
                AppUser.active.is_(True),
                func.lower(AppUser.email) == admin_email,
            )
        )
        return session is not None


async def admin_event_stream(
    request: Request, *, session_id: str, admin_email: str
) -> StreamingResponse:
    dsn = _postgres_dsn()
    if not dsn:
        # SQLite development mode has no cross-process notification bus.
        # Do not advertise a connected realtime transport that cannot notify.
        raise HTTPException(status_code=503, detail="Live admin events require PostgreSQL")

    try:
        listener = await asyncpg.connect(dsn, timeout=8)
    except (OSError, asyncpg.PostgresError, asyncio.TimeoutError):
        logger.exception("Could not connect to the admin notification bus")
        raise HTTPException(status_code=503, detail="Live admin events temporarily unavailable")

    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)

    def on_notification(_connection, _pid: int, _channel: str, payload: str) -> None:
        if payload not in TOPICS:
            return
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            # A bounded queue may drop redundant invalidations during bursts.
            # Resync the active view rather than retaining stale state.
            with suppress(asyncio.QueueEmpty):
                queue.get_nowait()
            with suppress(asyncio.QueueFull):
                queue.put_nowait("all")
                # Resynchronize whichever tab is active after an overflow.

    try:
        await listener.add_listener(CHANNEL, on_notification)
    except Exception:
        await listener.close()
        logger.exception("Could not subscribe to admin notifications")
        raise HTTPException(status_code=503, detail="Live admin events temporarily unavailable")

    async def events():
        checked_at = time.monotonic()
        overview_at = checked_at
        try:
            # Client reloads the current tab at connection establishment / reconnect.
            yield "retry: 2000\n\n" + _sse("ready")
            while not await request.is_disconnected():
                if listener.is_closed():
                    return  # EventSource reconnects and re-synchronizes.
                try:
                    topic = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    topic = None

                now = time.monotonic()
                if now - checked_at >= HEARTBEAT_SECONDS:
                    if not await _session_is_admin(session_id, admin_email):
                        yield _sse("auth-expired")
                        return
                    checked_at = now

                if topic in TOPICS:
                    yield _sse("invalidate", {"scope": topic})
                else:
                    yield ": keep-alive\n\n"

                # 'Active now' and rolling daily windows can age without writes.
                if now - overview_at >= OVERVIEW_ROLLING_SECONDS:
                    yield _sse("invalidate", {"scope": "overview"})
                    overview_at = now
        except (ConnectionError, OSError, asyncpg.PostgresError):
            logger.warning("Admin notification connection ended", exc_info=True)
        finally:
            with suppress(Exception):
                await listener.remove_listener(CHANNEL, on_notification)
            with suppress(Exception):
                await listener.close()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "X-Content-Type-Options": "nosniff",
        },
    )
