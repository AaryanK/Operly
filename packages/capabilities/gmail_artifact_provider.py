from __future__ import annotations

from email.message import EmailMessage
from typing import Any

from packages.artifacts.service import ArtifactService, artifact_scope_from_context
from packages.capabilities.contracts import ApprovalPolicy, CapabilityDefinition, CapabilityResult, ExecutionMode
from packages.capabilities.providers import BaseProvider
from packages.connectors.google_provider import (
    GMAIL_MODIFY,
    _email_message,
    _raw_message,
    request_json,
)
from packages.connectors.google_scope import (
    google_access_token_for_context,
    google_connector_for_context,
)


_MAX_ATTACHMENTS = 20
_MAX_ATTACHMENT_TOTAL_BYTES = 25 * 1024 * 1024


def _mime_parts(content_type: str | None) -> tuple[str, str]:
    value = str(content_type or "application/octet-stream").split(";", 1)[0].strip().lower()
    if "/" not in value:
        return "application", "octet-stream"
    main, sub = value.split("/", 1)
    if not main or not sub:
        return "application", "octet-stream"
    return main[:80], sub[:120]


class GmailArtifactProvider(BaseProvider):
    """Gmail draft operations that consume durable artifact IDs, never raw model bytes."""

    name = "gmail_artifacts"
    capabilities = (
        CapabilityDefinition(
            "gmail.create_draft_with_artifacts",
            "gmail_create_draft_with_artifacts",
            (
                "Create a Gmail draft and attach durable Operly artifacts from the current execution scope. "
                "Use artifact IDs returned by files.process/files.batch_process; the model never needs attachment bytes."
            ),
            {
                "type": "object",
                "properties": {
                    "to": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 20},
                    "cc": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
                    "bcc": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
                    "subject": {"type": "string", "maxLength": 998},
                    "text_body": {"type": "string", "maxLength": 50000},
                    "html_body": {"type": "string", "maxLength": 100000},
                    "artifact_ids": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": _MAX_ATTACHMENTS,
                        "items": {"type": "string", "minLength": 1, "maxLength": 36},
                    },
                },
                "required": ["to", "subject", "artifact_ids"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="low",
            permissions=("gmail:draft", "files:process"),
            approval_policy=ApprovalPolicy.AUTO,
            execution_mode=ExecutionMode.EXTERNAL,
            source="external",
            provider="google",
            integration_provider="google",
            credential_scopes=(GMAIL_MODIFY,),
            reversible=True,
            category="messaging",
            display_name="Draft Gmail with artifacts",
            tags=frozenset({"gmail", "draft", "attachments", "artifacts", "files"}),
            semantic_operations=frozenset(
                {
                    "draft email with attachment",
                    "attach generated pdf",
                    "attach artifact to gmail draft",
                    "draft email with report",
                }
            ),
        ),
    )

    async def _message(self, context, arguments: dict[str, Any]) -> tuple[EmailMessage, list[dict[str, Any]]]:
        artifact_ids = [str(item) for item in arguments.get("artifact_ids") or []]
        if len(artifact_ids) > _MAX_ATTACHMENTS:
            raise ValueError(f"Maximum {_MAX_ATTACHMENTS} email attachments")
        service = ArtifactService(context.db)
        scope = artifact_scope_from_context(context)
        rows = await service.get_many(scope, artifact_ids, max_items=_MAX_ATTACHMENTS)
        total = sum(int(row.size_bytes or 0) for row in rows)
        if total > _MAX_ATTACHMENT_TOTAL_BYTES:
            raise ValueError("Combined attachment size exceeds Gmail draft safety limit")

        message = _email_message(
            to=arguments["to"],
            cc=arguments.get("cc"),
            bcc=arguments.get("bcc"),
            subject=arguments["subject"],
            text_body=arguments.get("text_body") or "",
            html_body=arguments.get("html_body") or "",
            message_id=(f"<{context.execution_id}@operly.local>" if context.execution_id else None),
        )
        attached = []
        for row in rows:
            raw = await service.read_bytes(scope, row.id)
            maintype, subtype = _mime_parts(row.content_type)
            message.add_attachment(raw, maintype=maintype, subtype=subtype, filename=row.filename)
            attached.append(
                {
                    "artifact_id": row.id,
                    "filename": row.filename,
                    "content_type": row.content_type,
                    "size_bytes": row.size_bytes,
                    "sha256": row.sha256,
                }
            )
        return message, attached

    async def execute(self, context, capability_name: str, arguments: dict[str, Any]) -> CapabilityResult:
        if capability_name != "gmail.create_draft_with_artifacts":
            return CapabilityResult(False, False, {"reason": "unsupported_gmail_artifact_capability"})
        try:
            message, attached = await self._message(context, arguments)
            connector = await google_connector_for_context(context, GMAIL_MODIFY)
            token = await google_access_token_for_context(context, connector)
            body = await request_json(
                "POST",
                "https://gmail.googleapis.com/gmail/v1/users/me/drafts",
                token,
                {"message": {"raw": _raw_message(message)}},
            )
            evidence = {
                "draft_id": body.get("id"),
                "message_id": (body.get("message") or {}).get("id"),
                "recipients": list(arguments["to"]),
                "subject": str(arguments["subject"]),
                "attachment_artifact_ids": [item["artifact_id"] for item in attached],
                "attachments": attached,
                "attachment_count": len(attached),
                "rich_html": bool(arguments.get("html_body")),
                "delivery_status": "draft",
            }
            return CapabilityResult(bool(body.get("id")), True, evidence, body.get("id"))
        except (LookupError, ValueError, RuntimeError) as error:
            return CapabilityResult(False, False, {"reason": "gmail_artifact_draft_failed", "message": str(error)[:1000]})

    async def verify(self, context, capability_name: str, arguments: dict[str, Any], result: CapabilityResult) -> CapabilityResult:
        del context, arguments
        if capability_name != "gmail.create_draft_with_artifacts":
            return CapabilityResult(False, result.changed, {"reason": "unsupported_gmail_artifact_capability"})
        valid = bool(
            result.success
            and result.evidence.get("draft_id")
            and result.evidence.get("attachment_count") == len(result.evidence.get("attachment_artifact_ids") or [])
        )
        return CapabilityResult(
            valid,
            result.changed,
            {
                "draft_id": result.evidence.get("draft_id"),
                "attachment_artifact_ids": result.evidence.get("attachment_artifact_ids") or [],
                "attachment_count": result.evidence.get("attachment_count", 0),
                "draft_persisted_by_provider": valid,
            },
            result.external_reference,
        )
