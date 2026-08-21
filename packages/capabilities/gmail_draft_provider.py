from urllib.parse import quote

from packages.capabilities.contracts import ApprovalPolicy, CapabilityDefinition, CapabilityResult, ExecutionMode
from packages.capabilities.providers import BaseProvider
from packages.connectors.google_provider import (
    GMAIL_MODIFY,
    _email_message,
    _headers,
    _message_bodies,
    _raw_message,
    access_token,
    google_connector,
    request_json,
)


class GmailDraftLifecycleProvider(BaseProvider):
    name = "gmail_drafts"
    capabilities = (
        CapabilityDefinition(
            "gmail.list_drafts",
            "gmail_list_drafts",
            "List recent Gmail drafts in the current workspace connector. Use this to resolve references such as 'that draft' instead of guessing a draft ID.",
            {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 25}}, "additionalProperties": False},
            {"type": "object"},
            risk_level="read_only",
            permissions=("gmail:draft",),
            approval_policy=ApprovalPolicy.AUTO,
            execution_mode=ExecutionMode.EXTERNAL,
            source="external",
            provider="google",
            integration_provider="google",
            credential_scopes=(GMAIL_MODIFY,),
        ),
        CapabilityDefinition(
            "gmail.get_draft",
            "gmail_get_draft",
            "Read one Gmail draft by durable draft ID.",
            {"type": "object", "properties": {"draft_id": {"type": "string"}}, "required": ["draft_id"], "additionalProperties": False},
            {"type": "object"},
            risk_level="read_only",
            permissions=("gmail:draft",),
            approval_policy=ApprovalPolicy.AUTO,
            execution_mode=ExecutionMode.EXTERNAL,
            source="external",
            provider="google",
            integration_provider="google",
            credential_scopes=(GMAIL_MODIFY,),
        ),
        CapabilityDefinition(
            "gmail.update_draft",
            "gmail_update_draft",
            "Update an existing Gmail draft. Omitted fields preserve the current draft values.",
            {
                "type": "object",
                "properties": {
                    "draft_id": {"type": "string"},
                    "to": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
                    "cc": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
                    "bcc": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
                    "subject": {"type": "string"},
                    "text_body": {"type": "string"},
                    "html_body": {"type": "string"},
                },
                "required": ["draft_id"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="low",
            permissions=("gmail:draft",),
            approval_policy=ApprovalPolicy.AUTO,
            execution_mode=ExecutionMode.EXTERNAL,
            source="external",
            provider="google",
            integration_provider="google",
            credential_scopes=(GMAIL_MODIFY,),
            reversible=True,
        ),
        CapabilityDefinition(
            "gmail.send_draft",
            "gmail_send_draft",
            "Send one existing Gmail draft by durable draft ID. Sending always requires Operly approval.",
            {"type": "object", "properties": {"draft_id": {"type": "string"}}, "required": ["draft_id"], "additionalProperties": False},
            {"type": "object"},
            risk_level="high",
            permissions=("messaging:send",),
            approval_policy=ApprovalPolicy.ALWAYS,
            execution_mode=ExecutionMode.EXTERNAL,
            source="external",
            provider="google",
            integration_provider="google",
            credential_scopes=(GMAIL_MODIFY,),
        ),
        CapabilityDefinition(
            "gmail.delete_draft",
            "gmail_delete_draft",
            "Delete one Gmail draft by durable draft ID.",
            {"type": "object", "properties": {"draft_id": {"type": "string"}}, "required": ["draft_id"], "additionalProperties": False},
            {"type": "object"},
            risk_level="medium",
            permissions=("gmail:draft",),
            approval_policy=ApprovalPolicy.ALWAYS,
            execution_mode=ExecutionMode.EXTERNAL,
            source="external",
            provider="google",
            integration_provider="google",
            credential_scopes=(GMAIL_MODIFY,),
            reversible=False,
        ),
    )

    @staticmethod
    def _draft_evidence(body: dict) -> dict:
        message = body.get("message") or {}
        payload = message.get("payload") or {}
        headers = _headers(payload)
        plain, rich = _message_bodies(payload)
        return {
            "draft_id": body.get("id"),
            "message_id": message.get("id"),
            "thread_id": message.get("threadId"),
            "to": headers.get("to", ""),
            "cc": headers.get("cc", ""),
            "bcc": headers.get("bcc", ""),
            "subject": headers.get("subject", ""),
            "text_body": plain,
            "html_body": rich,
            "snippet": message.get("snippet", "")[:1000],
        }

    async def _connector(self, context):
        connector = await google_connector(context.db, context.tenant_id, GMAIL_MODIFY)
        return connector, await access_token(context.db, connector)

    async def _get(self, context, draft_id: str) -> dict:
        _, token = await self._connector(context)
        return await request_json(
            "GET",
            f"https://gmail.googleapis.com/gmail/v1/users/me/drafts/{quote(draft_id, safe='')}",
            token,
            params={"format": "full"},
        )

    async def execute(self, context, capability_name, arguments):
        if capability_name == "gmail.list_drafts":
            _, token = await self._connector(context)
            limit = max(1, min(int(arguments.get("limit", 10)), 25))
            listing = await request_json(
                "GET",
                "https://gmail.googleapis.com/gmail/v1/users/me/drafts",
                token,
                params={"maxResults": limit},
            )
            drafts = []
            for item in (listing.get("drafts") or [])[:limit]:
                detail = await request_json(
                    "GET",
                    f"https://gmail.googleapis.com/gmail/v1/users/me/drafts/{quote(item['id'], safe='')}",
                    token,
                    params={"format": "full"},
                )
                drafts.append(self._draft_evidence(detail))
            return CapabilityResult(True, False, {"drafts": drafts})

        draft_id = str(arguments.get("draft_id") or "").strip()
        if not draft_id:
            return CapabilityResult(False, False, {"reason": "draft_id_required"})

        if capability_name == "gmail.get_draft":
            detail = await self._get(context, draft_id)
            return CapabilityResult(True, False, self._draft_evidence(detail), draft_id)

        if capability_name == "gmail.update_draft":
            current = self._draft_evidence(await self._get(context, draft_id))
            def recipients(value):
                if isinstance(value, list):
                    return value
                return [item.strip() for item in str(value or "").split(",") if item.strip()]
            message = _email_message(
                to=arguments.get("to") or recipients(current.get("to")),
                cc=arguments.get("cc") if "cc" in arguments else recipients(current.get("cc")),
                bcc=arguments.get("bcc") if "bcc" in arguments else recipients(current.get("bcc")),
                subject=arguments.get("subject") if "subject" in arguments else current.get("subject", ""),
                text_body=arguments.get("text_body") if "text_body" in arguments else current.get("text_body", ""),
                html_body=arguments.get("html_body") if "html_body" in arguments else current.get("html_body", ""),
            )
            _, token = await self._connector(context)
            body = await request_json(
                "PUT",
                f"https://gmail.googleapis.com/gmail/v1/users/me/drafts/{quote(draft_id, safe='')}",
                token,
                {"id": draft_id, "message": {"raw": _raw_message(message)}},
            )
            detail = await self._get(context, body.get("id") or draft_id)
            evidence = self._draft_evidence(detail)
            return CapabilityResult(True, True, evidence, evidence.get("draft_id"))

        if capability_name == "gmail.send_draft":
            _, token = await self._connector(context)
            body = await request_json(
                "POST",
                "https://gmail.googleapis.com/gmail/v1/users/me/drafts/send",
                token,
                {"id": draft_id},
            )
            evidence = {
                "draft_id": draft_id,
                "message_id": body.get("id"),
                "thread_id": body.get("threadId"),
                "provider_status": "accepted",
            }
            return CapabilityResult(bool(body.get("id")), True, evidence, body.get("id"))

        if capability_name == "gmail.delete_draft":
            _, token = await self._connector(context)
            await request_json(
                "DELETE",
                f"https://gmail.googleapis.com/gmail/v1/users/me/drafts/{quote(draft_id, safe='')}",
                token,
                expected_statuses=(204,),
            )
            return CapabilityResult(True, True, {"draft_id": draft_id, "deleted": True}, draft_id)

        return CapabilityResult(False, False, {"reason": "unsupported_gmail_draft_capability"})

    async def verify(self, context, capability_name, arguments, result):
        if not result.success:
            return result
        if capability_name == "gmail.delete_draft":
            return CapabilityResult(True, True, {"deleted": True, **result.evidence}, result.external_reference)
        if capability_name == "gmail.send_draft":
            return CapabilityResult(bool(result.evidence.get("message_id")), True, result.evidence, result.external_reference)
        if capability_name in {"gmail.list_drafts", "gmail.get_draft"}:
            return CapabilityResult(True, False, {"observation_available": True, **result.evidence}, result.external_reference)
        return CapabilityResult(bool(result.evidence.get("draft_id")), result.changed, result.evidence, result.external_reference)
