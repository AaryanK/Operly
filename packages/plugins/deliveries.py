from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping
from urllib.parse import urlsplit

import httpx
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.connectors.secrets import read_secret
from packages.database.digital_event_models import DigitalEventDeliveryRecord
from packages.database.plugin_platform_models import (
    DigitalEventOutboxRecord,
    DigitalEventSubscriptionRecord,
)
from packages.security.pinned_https import (
    PublicHostPolicyError,
    PublicHostResolutionError,
    host_header,
    pinned_https_url,
    resolve_public_addresses,
    sni_extensions,
)


class EventDeliveryError(RuntimeError):
    def __init__(self, message: str, *, permanent: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent


@dataclass(frozen=True, slots=True)
class DeliveryEvidence:
    status_code: int
    response_digest: str
    response_bytes: int
    target_kind: str


def _json_object(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _safe_target_url(value: str) -> tuple[str, str, int]:
    raw = str(value or "").strip()
    if len(raw) > 2048:
        raise EventDeliveryError("Webhook target URL is too long", permanent=True)
    parsed = urlsplit(raw)
    if parsed.scheme.lower() != "https":
        raise EventDeliveryError("Webhook event targets must use HTTPS", permanent=True)
    if parsed.username or parsed.password:
        raise EventDeliveryError("Webhook target credentials may not appear in the URL", permanent=True)
    if parsed.fragment:
        raise EventDeliveryError("Webhook target URL may not contain a fragment", permanent=True)
    host = str(parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise EventDeliveryError("Webhook target hostname is required", permanent=True)
    if host in {"localhost", "localhost.localdomain"} or host.endswith(
        (".localhost", ".local", ".internal", ".home.arpa")
    ):
        raise EventDeliveryError("Webhook target may not use a local hostname", permanent=True)
    try:
        port = int(parsed.port or 443)
    except ValueError as error:
        raise EventDeliveryError("Webhook target port is invalid", permanent=True) from error
    if not 1 <= port <= 65535:
        raise EventDeliveryError("Webhook target port is invalid", permanent=True)
    return raw, host, port


class DigitalEventDeliveryService:
    """Durable delivery lifecycle for already-fanned-out digital events."""

    async def lease_batch(
        self,
        db: AsyncSession,
        *,
        worker_id: str,
        limit: int = 50,
        lease_seconds: int = 120,
    ) -> list[DigitalEventDeliveryRecord]:
        now = datetime.utcnow()
        rows = list(
            (
                await db.scalars(
                    select(DigitalEventDeliveryRecord)
                    .where(
                        DigitalEventDeliveryRecord.available_at <= now,
                        or_(
                            DigitalEventDeliveryRecord.status.in_(["pending", "retry"]),
                            and_(
                                DigitalEventDeliveryRecord.status == "delivering",
                                DigitalEventDeliveryRecord.lease_expires_at.is_not(None),
                                DigitalEventDeliveryRecord.lease_expires_at < now,
                            ),
                        ),
                    )
                    .order_by(
                        DigitalEventDeliveryRecord.available_at.asc(),
                        DigitalEventDeliveryRecord.created_at.asc(),
                    )
                    .limit(max(1, min(int(limit), 200)))
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
        lease_until = now + timedelta(seconds=max(30, min(int(lease_seconds), 900)))
        for row in rows:
            row.status = "delivering"
            row.locked_by = worker_id
            row.lease_expires_at = lease_until
            row.attempts += 1
        await db.flush()
        return rows

    async def complete(
        self,
        db: AsyncSession,
        *,
        delivery_id: str,
        worker_id: str,
        evidence: DeliveryEvidence,
    ) -> None:
        row = await db.get(DigitalEventDeliveryRecord, delivery_id)
        if row is None or row.status != "delivering" or row.locked_by != worker_id:
            raise PermissionError("Event delivery lease is not owned by this worker")
        row.status = "delivered"
        row.response_status = evidence.status_code
        row.response_digest = evidence.response_digest
        row.last_error = None
        row.locked_by = None
        row.lease_expires_at = None
        row.delivered_at = datetime.utcnow()
        await db.flush()

    async def fail(
        self,
        db: AsyncSession,
        *,
        delivery_id: str,
        worker_id: str,
        error: str,
        retry_after_seconds: int = 60,
        max_attempts: int = 8,
        permanent: bool = False,
        response_status: int | None = None,
    ) -> None:
        row = await db.get(DigitalEventDeliveryRecord, delivery_id)
        if row is None or row.status != "delivering" or row.locked_by != worker_id:
            raise PermissionError("Event delivery lease is not owned by this worker")
        row.last_error = str(error or "")[:4000]
        row.response_status = response_status
        row.locked_by = None
        row.lease_expires_at = None
        if permanent or row.attempts >= max(1, min(int(max_attempts), 50)):
            row.status = "dead_letter"
        else:
            row.status = "retry"
            row.available_at = datetime.utcnow() + timedelta(
                seconds=max(1, min(int(retry_after_seconds), 86400))
            )
        await db.flush()

    async def deliver(
        self,
        db: AsyncSession,
        *,
        delivery: DigitalEventDeliveryRecord,
    ) -> DeliveryEvidence:
        subscription = await db.get(
            DigitalEventSubscriptionRecord, delivery.subscription_id
        )
        event = await db.get(DigitalEventOutboxRecord, delivery.event_id)
        if subscription is None or event is None:
            raise EventDeliveryError("Event delivery source no longer exists", permanent=True)
        if subscription.tenant_id != delivery.tenant_id or event.tenant_id != delivery.tenant_id:
            raise EventDeliveryError("Event delivery scope mismatch", permanent=True)
        if not subscription.enabled:
            raise EventDeliveryError("Event subscription is disabled", permanent=True)
        if subscription.target_kind != "webhook":
            raise EventDeliveryError(
                f"Unsupported event delivery target kind: {subscription.target_kind}",
                permanent=True,
            )

        target, host, port = _safe_target_url(subscription.target_reference)
        try:
            resolved_addresses = await resolve_public_addresses(host, port)
        except PublicHostPolicyError as error:
            raise EventDeliveryError(str(error), permanent=True) from error
        except PublicHostResolutionError as error:
            raise EventDeliveryError(str(error)) from error
        pinned_address = resolved_addresses[0]
        parsed_target = urlsplit(target)
        pinned_target = pinned_https_url(
            pinned_address,
            port=port,
            path=parsed_target.path or "/",
            query=parsed_target.query,
        )

        policy = _json_object(subscription.delivery_policy_json)
        timeout_seconds = max(1.0, min(float(policy.get("timeout_seconds", 20)), 60.0))
        max_response_bytes = max(
            1024, min(int(policy.get("max_response_bytes", 64 * 1024)), 1024 * 1024)
        )

        try:
            payload = json.loads(event.payload_json or "{}")
        except json.JSONDecodeError as error:
            raise EventDeliveryError("Stored event payload is invalid", permanent=True) from error
        body = json.dumps(
            {
                "id": event.id,
                "type": event.event_type,
                "source": {"kind": event.source_kind, "id": event.source_id},
                "subject": {"type": event.subject_type, "id": event.subject_id},
                "created_at": event.created_at.isoformat() if event.created_at else None,
                "payload": payload,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if len(body) > 1024 * 1024:
            raise EventDeliveryError("Event delivery payload exceeds 1 MiB", permanent=True)

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Operly-Event-Delivery/1",
            "X-Operly-Event-ID": event.id,
            "X-Operly-Event-Type": event.event_type,
            "Host": host_header(host, port),
        }
        if subscription.secret_reference:
            secret_payload = await read_secret(
                db, delivery.tenant_id, subscription.secret_reference
            )
            secret = str(secret_payload.get("webhook_secret") or "")
            if not secret and isinstance(secret_payload.get("value"), Mapping):
                nested = secret_payload["value"]
                secret = str(nested.get("webhook_secret") or nested.get("secret") or "")
            if not secret:
                raise EventDeliveryError(
                    "Event delivery signing secret is unavailable", permanent=True
                )
            signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
            headers["X-Operly-Signature"] = f"sha256={signature}"

        digest = hashlib.sha256()
        response_bytes = 0
        try:
            async with httpx.AsyncClient(
                timeout=timeout_seconds,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                async with client.stream(
                    "POST",
                    pinned_target,
                    headers=headers,
                    content=body,
                    extensions=sni_extensions(host),
                ) as response:
                    async for chunk in response.aiter_bytes():
                        response_bytes += len(chunk)
                        if response_bytes > max_response_bytes:
                            raise EventDeliveryError(
                                "Webhook response exceeds configured evidence limit",
                                permanent=True,
                            )
                        digest.update(chunk)
                    status = int(response.status_code)
        except httpx.TimeoutException as error:
            raise EventDeliveryError("Webhook delivery timed out") from error
        except httpx.HTTPError as error:
            raise EventDeliveryError("Webhook delivery network request failed") from error

        if 200 <= status < 300:
            return DeliveryEvidence(
                status_code=status,
                response_digest=digest.hexdigest(),
                response_bytes=response_bytes,
                target_kind="webhook",
            )
        if status in {408, 425, 429} or 500 <= status < 600:
            raise EventDeliveryError(f"Webhook target returned retryable HTTP {status}")
        raise EventDeliveryError(
            f"Webhook target rejected delivery with HTTP {status}", permanent=True
        )


digital_event_deliveries = DigitalEventDeliveryService()

__all__ = [
    "DeliveryEvidence",
    "DigitalEventDeliveryService",
    "EventDeliveryError",
    "digital_event_deliveries",
]
