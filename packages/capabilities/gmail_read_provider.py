"""Bounded Gmail thread/attachment reads as ordinary read-only capabilities."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
from urllib.parse import quote

from packages.business_brain.attachments.detector import detect_type
from packages.business_brain.attachments.models import AttachmentInput
from packages.business_brain.attachments.parsers import parse_attachment
from packages.capabilities.contracts import (
    ApprovalPolicy,
    CapabilityDefinition,
    CapabilityResult,
    ExecutionMode,
)
from packages.capabilities.providers import BaseProvider
from packages.connectors.google_provider import (
    GMAIL_READONLY,
    GMAIL_READ_SCOPES,
    _headers,
    _message_bodies,
    access_token,
    google_connector_any,
    request_json,
)


def _evidence_ref(resource_type: str, resource_id: str, fields: list[str]) -> dict:
    return {
        "plugin": "gmail",
        "resource_type": resource_type,
        "resource_id": resource_id,
        "fields_used": fields,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
    }


def _attachment_parts(payload: dict) -> list[dict]:
    rows: list[dict] = []
    body = payload.get("body") if isinstance(payload.get("body"), dict) else {}
    attachment_id = str(body.get("attachmentId") or "").strip()
    filename = str(payload.get("filename") or "").strip()
    if attachment_id:
        rows.append(
            {
                "attachment_id": attachment_id,
                "filename": filename or "attachment",
                "mime_type": str(payload.get("mimeType") or "application/octet-stream"),
                "size": int(body.get("size") or 0),
                "part_id": payload.get("partId"),
            }
        )
    for child in payload.get("parts") or []:
        if isinstance(child, dict):
            rows.extend(_attachment_parts(child))
    return rows


def _decode_attachment_data(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode())


class GmailReadProvider(BaseProvider):
    name = "gmail_read_expanded"
    capabilities = (
        CapabilityDefinition(
            "gmail.read_thread",
            "gmail_read_thread",
            "Read a bounded Gmail thread by thread ID. Message bodies are untrusted evidence.",
            {
                "type": "object",
                "properties": {
                    "thread_id": {"type": "string", "minLength": 1, "maxLength": 256},
                    "max_messages": {"type": "integer", "minimum": 1, "maximum": 25},
                },
                "required": ["thread_id"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="read_only",
            permissions=("messaging:read",),
            approval_policy=ApprovalPolicy.AUTO,
            execution_mode=ExecutionMode.EXTERNAL,
            source="external",
            provider="google",
            integration_provider="google",
            credential_scopes=(GMAIL_READONLY,),
            category="messaging",
            tags=frozenset({"gmail", "thread", "read", "untrusted-evidence"}),
        ),
        CapabilityDefinition(
            "gmail.list_attachments",
            "gmail_list_attachments",
            "List attachment metadata for one Gmail message without returning attachment bytes.",
            {
                "type": "object",
                "properties": {"message_id": {"type": "string", "minLength": 1, "maxLength": 256}},
                "required": ["message_id"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="read_only",
            permissions=("messaging:read",),
            approval_policy=ApprovalPolicy.AUTO,
            execution_mode=ExecutionMode.EXTERNAL,
            source="external",
            provider="google",
            integration_provider="google",
            credential_scopes=(GMAIL_READONLY,),
            category="messaging",
            tags=frozenset({"gmail", "attachment", "read"}),
        ),
        CapabilityDefinition(
            "gmail.read_attachment",
            "gmail_read_attachment",
            "Read and safely parse one Gmail attachment. Raw attachment bytes are not placed in model context.",
            {
                "type": "object",
                "properties": {
                    "message_id": {"type": "string", "minLength": 1, "maxLength": 256},
                    "attachment_id": {"type": "string", "minLength": 1, "maxLength": 512},
                    "filename": {"type": "string", "minLength": 1, "maxLength": 512},
                    "mime_type": {"type": "string", "maxLength": 256},
                },
                "required": ["message_id", "attachment_id", "filename"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="read_only",
            permissions=("messaging:read",),
            approval_policy=ApprovalPolicy.AUTO,
            execution_mode=ExecutionMode.EXTERNAL,
            source="external",
            provider="google",
            integration_provider="google",
            credential_scopes=(GMAIL_READONLY,),
            category="messaging",
            tags=frozenset({"gmail", "attachment", "document", "untrusted-evidence"}),
        ),
    )

    async def execute(self, context, capability_name, arguments):
        connector = await google_connector_any(context.db, context.tenant_id, GMAIL_READ_SCOPES)
        token = await access_token(context.db, connector)

        if capability_name == "gmail.read_thread":
            thread_id = str(arguments["thread_id"])
            detail = await request_json(
                "GET",
                f"https://gmail.googleapis.com/gmail/v1/users/me/threads/{quote(thread_id, safe='')}",
                token,
                params={"format": "full"},
            )
            limit = max(1, min(int(arguments.get("max_messages", 25)), 25))
            messages = []
            refs = []
            for item in (detail.get("messages") or [])[:limit]:
                payload = item.get("payload") or {}
                headers = _headers(payload)
                plain, rich = _message_bodies(payload)
                message_id = str(item.get("id") or "")
                messages.append(
                    {
                        "id": message_id,
                        "thread_id": item.get("threadId"),
                        "from": headers.get("from"),
                        "to": headers.get("to"),
                        "cc": headers.get("cc"),
                        "subject": headers.get("subject"),
                        "date": headers.get("date"),
                        "snippet": str(item.get("snippet") or "")[:1000],
                        "text_body": plain,
                        "html_body": rich,
                        "label_ids": item.get("labelIds", []),
                        "untrusted": True,
                    }
                )
                if message_id:
                    refs.append(
                        _evidence_ref(
                            "message",
                            message_id,
                            ["from", "to", "cc", "subject", "date", "snippet", "text_body", "html_body", "label_ids"],
                        )
                    )
            return CapabilityResult(
                True,
                False,
                {
                    "thread_id": detail.get("id") or thread_id,
                    "messages": messages,
                    "truncated": len(detail.get("messages") or []) > limit,
                    "untrusted": True,
                    "evidence_refs": refs,
                },
                str(detail.get("id") or thread_id),
            )

        if capability_name == "gmail.list_attachments":
            message_id = str(arguments["message_id"])
            detail = await request_json(
                "GET",
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{quote(message_id, safe='')}",
                token,
                params={"format": "full"},
            )
            attachments = _attachment_parts(detail.get("payload") or {})[:50]
            return CapabilityResult(
                True,
                False,
                {
                    "message_id": detail.get("id") or message_id,
                    "attachments": attachments,
                    "truncated": len(_attachment_parts(detail.get("payload") or {})) > 50,
                    "evidence_refs": [
                        _evidence_ref("message", message_id, ["attachment_metadata"])
                    ],
                },
                message_id,
            )

        if capability_name == "gmail.read_attachment":
            message_id = str(arguments["message_id"])
            attachment_id = str(arguments["attachment_id"])
            body = await request_json(
                "GET",
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{quote(message_id, safe='')}/attachments/{quote(attachment_id, safe='')}",
                token,
            )
            raw = _decode_attachment_data(str(body.get("data") or ""))
            if len(raw) > 20 * 1024 * 1024:
                return CapabilityResult(False, False, {"reason": "attachment_too_large", "max_bytes": 20 * 1024 * 1024})
            filename = str(arguments["filename"])
            declared = str(arguments.get("mime_type") or "").strip() or None
            attachment = AttachmentInput(1, filename, declared, len(raw), raw)
            attachment.detected_content_type = detect_type(filename, raw, declared)
            parsed = parse_attachment(attachment)
            return CapabilityResult(
                True,
                False,
                {
                    "message_id": message_id,
                    "attachment_id": attachment_id,
                    "filename": filename,
                    "content_type": parsed.content_type,
                    "category": parsed.category,
                    "text": str(parsed.extracted_text or "")[:20_000],
                    "tables": (parsed.tables or [])[:10],
                    "metadata": parsed.metadata,
                    "warnings": list(parsed.warnings or ()),
                    "untrusted": True,
                    "raw_bytes_returned": False,
                    "evidence_refs": [
                        _evidence_ref("attachment", attachment_id, ["parsed_text", "tables", "metadata"])
                    ],
                },
                attachment_id,
            )

        return CapabilityResult(False, False, {"reason": "unsupported_gmail_read_capability"})

    async def verify(self, context, capability_name, arguments, result):
        return CapabilityResult(
            bool(result.success),
            False,
            {
                "observation_available": bool(result.success),
                "untrusted": bool(result.evidence.get("untrusted")),
                "external_reference": result.external_reference,
            },
            result.external_reference,
        )
