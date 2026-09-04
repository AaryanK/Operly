"""Bound anonymous request bodies and apply a cross-replica abuse backstop."""
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from starlette.responses import JSONResponse

from apps.api.security import privacy_hash
from packages.database.db import SessionFactory
from packages.database.models import AuthRateLimitEvent


class _PublicBodyTooLarge(Exception):
    pass


class PublicEndpointSafetyMiddleware:
    """Protect anonymous ingress without trusting Content-Length.

    Body size is enforced as ASGI request chunks are consumed, so chunked uploads
    cannot force the application to buffer an arbitrarily large body first. Rate
    events are persisted in the shared database, which keeps the backstop effective
    across process restarts and horizontally scaled API replicas.
    """

    def __init__(self, app, max_body_bytes=1_048_576, requests_per_minute=30):
        self.app = app
        self.max_body_bytes = int(max_body_bytes)
        self.limit = int(requests_per_minute)

    @staticmethod
    def _is_public_endpoint(path: str) -> bool:
        return (
            path.startswith("/api/public/")
            or path == "/api/workspace-invitations/inspect"
            or path == "/api/workspace-os/invitation/inspect"
        )

    @staticmethod
    def _rate_bucket(path: str) -> str:
        # Endpoint keys are bearer-like secrets. Do not let an attacker evade the
        # IP limit merely by probing a different random webhook key each request.
        if path.startswith("/api/public/webhooks/"):
            return "/api/public/webhooks/*"
        return path[:500]

    @staticmethod
    def _client_ip(scope) -> str:
        client = scope.get("client")
        if isinstance(client, (tuple, list)) and client:
            return str(client[0] or "unknown")[:255]
        return "unknown"

    async def _rate_limited(self, scope, path: str) -> bool:
        now = datetime.utcnow()
        cutoff = now - timedelta(seconds=60)
        bucket = self._rate_bucket(path)
        endpoint = f"public:{bucket}"
        key_hash = privacy_hash(self._client_ip(scope), purpose=f"public-rate:{bucket}")
        async with SessionFactory() as db:
            db.add(
                AuthRateLimitEvent(
                    endpoint=endpoint,
                    key_hash=key_hash,
                    created_at=now,
                )
            )
            await db.execute(
                delete(AuthRateLimitEvent).where(
                    AuthRateLimitEvent.created_at < now - timedelta(days=1)
                )
            )
            await db.commit()
            count = await db.scalar(
                select(func.count(AuthRateLimitEvent.id)).where(
                    AuthRateLimitEvent.endpoint == endpoint,
                    AuthRateLimitEvent.key_hash == key_hash,
                    AuthRateLimitEvent.created_at >= cutoff,
                )
            )
        return int(count or 0) > self.limit

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path") or "")
        if not self._is_public_endpoint(path):
            await self.app(scope, receive, send)
            return

        headers = {
            bytes(name).lower(): bytes(value)
            for name, value in scope.get("headers", [])
        }
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                declared = int(content_length.decode("ascii"))
            except (UnicodeDecodeError, ValueError):
                response = JSONResponse({"detail": "Invalid Content-Length"}, 400)
                await response(scope, receive, send)
                return
            if declared < 0 or declared > self.max_body_bytes:
                response = JSONResponse({"detail": "Request body is too large"}, 413)
                await response(scope, receive, send)
                return

        if await self._rate_limited(scope, path):
            response = JSONResponse(
                {"detail": "Too many anonymous requests"},
                429,
                headers={"Retry-After": "60"},
            )
            await response(scope, receive, send)
            return

        consumed = 0
        response_started = False

        async def limited_receive():
            nonlocal consumed
            message = await receive()
            if message.get("type") == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > self.max_body_bytes:
                    raise _PublicBodyTooLarge
            return message

        async def tracked_send(message):
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except _PublicBodyTooLarge:
            if response_started:
                # A compliant body-consuming endpoint parses the request before
                # starting its response. If a future endpoint violates that ordering,
                # abort instead of attempting to send a second HTTP response.
                raise
            response = JSONResponse({"detail": "Request body is too large"}, 413)
            await response(scope, receive, send)
