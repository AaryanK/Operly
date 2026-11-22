"""Workspace AI delegation to DragonZpyder's single Deep Agents harness.

Operly remains the Workspace authority. The current bridge grants ZERO
business tools; only a separately reviewed user/workspace OAuth MCP grant
may enable them. No browser cookies, model-selected IDs or local LLM fallback.
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from packages.agent_runtime.dragonzpyder_personal import (
    DragonZpyderPersonalUnavailable, PersonalAgentResult,
    sanitize_execution_receipts,
)

PATH = "/internal/agents/operly/workspace/run"


def _endpoint() -> str:
    url = os.getenv("OPERLY_DRAGONZPYDER_WORKSPACE_RUN_URL", "").strip()
    parsed = urlsplit(url)
    public = parsed.scheme == "https" and parsed.hostname == "api.dragonzpyder.xyz" and parsed.port in (None, 443)
    private = parsed.scheme == "http" and bool(parsed.hostname) and parsed.hostname.endswith(".railway.internal") and parsed.port in (None, 8080)
    if (not (public or private) or parsed.path != PATH
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise DragonZpyderPersonalUnavailable("Approved DragonZpyder Workspace endpoint required")
    return url


async def run_workspace_harness(*, context, conversation_id: str,
                                run_id: str, message: str) -> PersonalAgentResult:
    if (not context.is_workspace or not context.workspace_id or not context.user_id
            or context.principal_id != f"user:{context.user_id}"
            or not message.strip()):
        raise DragonZpyderPersonalUnavailable(
            "Active member Workspace authority required")
    try:
        UUID(str(context.user_id))
        UUID(str(context.workspace_id))
        UUID(str(conversation_id))
    except (ValueError, AttributeError):
        raise DragonZpyderPersonalUnavailable("Canonical Workspace identity required") from None
    token = os.getenv("OPERLY_DRAGONZPYDER_WORKSPACE_AGENT_TOKEN", "")
    proof = os.getenv("OPERLY_DRAGONZPYDER_WORKSPACE_SERVICE_SECRET", "")
    if len(token) < 40 or len(proof) < 40 or token == proof:
        raise DragonZpyderPersonalUnavailable("Workspace delegation secrets unavailable")

    payload = {
        "scope_kind": "workspace",
        "user_id": str(context.user_id),
        "workspace_id": str(context.workspace_id),
        "conversation_id": str(conversation_id),
        "principal_id": str(context.principal_id),
        "prompt": message,
    }
    try:
        async with httpx.AsyncClient(timeout=100, follow_redirects=False, trust_env=False) as http:
            reply = await http.post(_endpoint(), json=payload, headers={
                "Authorization": "Bearer " + token,
                "X-Operly-Workspace-Service": proof,
            })
        if reply.status_code != 200 or len(reply.content) > 64000:
            raise DragonZpyderPersonalUnavailable("Workspace harness unavailable")
        response = reply.json()
    except (httpx.HTTPError, ValueError) as error:
        raise DragonZpyderPersonalUnavailable("Workspace harness unavailable") from error
    if (not isinstance(response, dict) or response.get("status") not in ("completed", "incomplete")
            or not isinstance(response.get("answer"), str)
            or response.get("business_tools_enabled") is not False
            or response.get("thread_checkpointed") is not True):
        raise DragonZpyderPersonalUnavailable("Invalid Workspace harness response")
    return PersonalAgentResult(
        message=response["answer"][:16000],
        capability_calls=(),
        run_id=run_id,
        dispatch="dragonzpyder_workspace_harness",
        objective_kind="workspace_goal",
        error_code=None if response["status"] == "completed" else "agent_incomplete",
        execution_receipts=sanitize_execution_receipts(response),
    )
