from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from typing import Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.artifacts import ArtifactScope, ArtifactService
from packages.connectors.secrets import read_secret
from packages.database.digital_event_models import (
    DigitalWebhookEndpointRecord,
    DigitalWebhookReceiptRecord,
)
from packages.plugins.events import DigitalEventService


@dataclass(frozen=True, slots=True)
class CreatedWebhookEndpoint:
    endpoint_id: str
    endpoint_key: str
    event_type: str


class WebhookVerificationError(PermissionError):
    pass


class DigitalWebhookService:
    MAX_BODY_BYTES = 1024 * 1024

    @staticmethod
    def _hash_key(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    async def create_endpoint(
        self,
        db: AsyncSession,
        *,
        tenant_id: str,
        event_type: str,
        installation_id: str | None = None,
        verification_type: str = "none",
        secret_reference: str | None = None,
        max_body_bytes: int = MAX_BODY_BYTES,
        created_by: str | None = None,
        metadata: Mapping | None = None,
    ) -> CreatedWebhookEndpoint:
        verification = str(verification_type or "none").strip().lower()
        if verification not in {"none", "hmac_sha256"}:
            raise ValueError("Webhook verification_type is unsupported")
        if verification == "hmac_sha256" and not secret_reference:
            raise ValueError("HMAC webhook verification requires a secret reference")
        if secret_reference:
            # Ownership check only; raw value is never returned from this method.
            await read_secret(db, tenant_id, secret_reference)
        event_name = str(event_type or "").strip().lower()
        if not event_name or len(event_name) > 160:
            raise ValueError("Webhook event_type must be between 1 and 160 characters")
        endpoint_key = "whk_" + secrets.token_urlsafe(32)
        row = DigitalWebhookEndpointRecord(
            tenant_id=tenant_id,
            installation_id=installation_id,
            endpoint_key_hash=self._hash_key(endpoint_key),
            event_type=event_name,
            verification_type=verification,
            secret_reference=secret_reference,
            max_body_bytes=max(1024, min(int(max_body_bytes), self.MAX_BODY_BYTES)),
            enabled=True,
            metadata_json=json.dumps(dict(metadata or {}), separators=(",", ":"), sort_keys=True),
            created_by=created_by,
        )
        db.add(row)
        await db.flush()
        return CreatedWebhookEndpoint(
            endpoint_id=row.id,
            endpoint_key=endpoint_key,
            event_type=row.event_type,
        )

    async def receive(
        self,
        db: AsyncSession,
        *,
        endpoint_key: str,
        body: bytes,
        headers: Mapping[str, str],
    ) -> DigitalWebhookReceiptRecord:
        row = await db.scalar(
            select(DigitalWebhookEndpointRecord).where(
                DigitalWebhookEndpointRecord.endpoint_key_hash == self._hash_key(endpoint_key),
                DigitalWebhookEndpointRecord.enabled.is_(True),
            )
        )
        if row is None:
            raise LookupError("Webhook endpoint not found")
        raw = bytes(body)
        if len(raw) > min(row.max_body_bytes, self.MAX_BODY_BYTES):
            raise ValueError("Webhook payload exceeds endpoint policy")

        normalized_headers = {str(k).lower(): str(v) for k, v in headers.items()}
        if row.verification_type == "hmac_sha256":
            secret_payload = await read_secret(db, row.tenant_id, str(row.secret_reference))
            secret = str(secret_payload.get("webhook_secret") or "")
            if not secret:
                raise WebhookVerificationError("Webhook verification secret is unavailable")
            supplied = normalized_headers.get("x-operly-webhook-signature", "").strip()
            if supplied.startswith("sha256="):
                supplied = supplied[7:]
            expected = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
            if not supplied or not hmac.compare_digest(supplied, expected):
                raise WebhookVerificationError("Webhook signature is invalid")

        digest = hashlib.sha256(raw).hexdigest()
        dedupe_key = str(normalized_headers.get("x-operly-event-id") or digest)[:240]
        existing = await db.scalar(
            select(DigitalWebhookReceiptRecord).where(
                DigitalWebhookReceiptRecord.endpoint_id == row.id,
                DigitalWebhookReceiptRecord.dedupe_key == dedupe_key,
            )
        )
        if existing is not None:
            return existing

        artifact = await ArtifactService(db).create_bytes(
            ArtifactScope("workspace", row.tenant_id, tenant_id=row.tenant_id),
            filename=f"webhook-{row.id}-{digest[:12]}.bin",
            content=raw,
            content_type=normalized_headers.get("content-type", "application/octet-stream")[:200],
            source="webhook_ingress",
            created_by=row.created_by,
            metadata={"webhook_endpoint_id": row.id, "event_type": row.event_type},
        )
        safe_headers = {
            key: normalized_headers[key][:1000]
            for key in ("content-type", "user-agent", "x-operly-event-id")
            if key in normalized_headers
        }
        receipt = DigitalWebhookReceiptRecord(
            tenant_id=row.tenant_id,
            endpoint_id=row.id,
            dedupe_key=dedupe_key,
            body_sha256=digest,
            payload_artifact_id=artifact.id,
            headers_json=json.dumps(safe_headers, separators=(",", ":"), sort_keys=True),
            verification_state="verified" if row.verification_type != "none" else "not_required",
            processing_state="accepted",
        )
        db.add(receipt)
        await db.flush()
        safe_projection = {
            "receipt_id": receipt.id,
            "artifact_id": artifact.id,
            "body_sha256": digest,
            "content_type": artifact.content_type,
        }
        event = await DigitalEventService().emit(
            db,
            tenant_id=row.tenant_id,
            event_type=row.event_type,
            source_kind="webhook",
            source_id=row.id,
            subject_type="plugin_installation" if row.installation_id else "workspace",
            subject_id=row.installation_id or row.tenant_id,
            payload=safe_projection,
            trigger_payload=safe_projection,
        )
        receipt.event_id = event.id
        await db.flush()
        return receipt


webhooks = DigitalWebhookService()
