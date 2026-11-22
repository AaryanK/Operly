"""Workspace-only governed handoff to the DragonZpyder Deep Agents API.

Operly verifies live session, scope, current membership and executable MCP
capabilities before signing a short-lived delegation. DragonZpyder never
receives a browser cookie, provider secret, or Operly role from the model.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from packages.mcp.first_party_workspace import (
    WorkspaceDelegationDenied, issue_workspace_delegation,
)
from packages.security.execution_context import ExecutionContext

RUN_PATH = "/internal/agents/operly/workspace/run"


class DragonZpyderWorkspaceUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class WorkspaceAgentResult:
    message: str
    run_id: str
    capability_calls: tuple[str, ...]
    dispatch: str = "dragonzpyder_workspace_mcp"
    objective_kind: str = "workspace_goal"
    error_code: str | None = None
    execution_receipts: dict | None = None

    def as_dict(self):
        return {
            "message": self.message,
            "run_id": self.run_id,
            "dispatch": self.dispatch,
            "objective_kind": self.objective_kind,
            "error_code": self.error_code,
            "capability_calls": list(self.capability_calls),
            "execution_receipts": self.execution_receipts or {},
            "approval_id": None,
            "objective_verified": False,
        }


def _endpoint() -> str:
    url = os.getenv("OPERLY_DRAGONZPYDER_WORKSPACE_RUN_URL", "").strip()
    p = urlsplit(url)
    trusted_public = p.scheme == "https" and p.hostname == "api.dragonzpyder.xyz" and p.port in (None, 443)
    trusted_private = (p.scheme == "http" and p.hostname is not None and
                       p.hostname.endswith(".railway.internal") and p.port in (None, 8080))
    if (not (trusted_public or trusted_private) or p.path != RUN_PATH
            or p.username or p.password or p.query or p.fragment):
        raise DragonZpyderWorkspaceUnavailable("Trusted Workspace harness endpoint unavailable")
    return url


async def run_workspace_harness(
    db: AsyncSession, *, context: ExecutionContext, session_id: str,
    conversation_id: str, run_id: str, message: str,
) -> WorkspaceAgentResult:
    if (not context.is_workspace or not context.workspace_id or not context.user_id
            or not session_id or context.is_guest_workspace):
        raise DragonZpyderWorkspaceUnavailable("Full Workspace membership required")
    url = _endpoint()
    service = os.getenv("OPERLY_DRAGONZPYDER_PERSONAL_AGENT_TOKEN", "").strip()
    proof = os.getenv("OPERLY_DRAGONZPYDER_MCP_SERVICE_SECRET", "").strip()
    if len(service) < 40 or len(proof) < 40:
        raise DragonZpyderWorkspaceUnavailable("Workspace service identity not configured")
    base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
    bearer, approved = await issue_workspace_delegation(
        db, session_id=session_id, user_id=context.user_id,
        workspace_id=context.workspace_id, conversation_id=conversation_id,
        resource=base + "/mcp", query=message,
    )
    payload = {
        "scope_kind": "workspace",
        "user_id": context.user_id,
        "workspace_id": context.workspace_id,
        "session_id": session_id,
        "conversation_id": conversation_id,
        "prompt": message,
        "mcp_access_token": bearer,
        "approved_tool_names": sorted(approved),
    }
    try:
        async with httpx.AsyncClient(timeout=100, follow_redirects=False, trust_env=False) as client:
            reply = await client.post(url, json=payload, headers={
                "Authorization": "Bearer " + service,
                "X-Operly-Agent-Service": proof,
            })
        if reply.status_code != 200 or len(reply.content) > 64_000:
            raise DragonZpyderWorkspaceUnavailable("Workspace harness rejected execution")
        response = reply.json()
    except (httpx.HTTPError, ValueError, TypeError) as error:
        raise DragonZpyderWorkspaceUnavailable("Workspace harness unavailable") from error
    if (not isinstance(response, dict) or
            response.get("status") not in ("completed", "incomplete") or
            not isinstance(response.get("answer"), str) or
            not isinstance(response.get("thread_checkpointed"), bool) or
            not response["thread_checkpointed"]):
        raise DragonZpyderWorkspaceUnavailable("Workspace harness execution unverified")
    from packages.agent_runtime.dragonzpyder_personal import sanitize_execution_receipts
    receipts = sanitize_execution_receipts(response)
    successful = response.get("successful_tools", [])
    if not isinstance(successful, list):
        successful = []
    names = tuple(name for name in successful
                  if isinstance(name, str) and re.fullmatch(r"operly_[A-Za-z0-9_-]{1,79}", name))[:40]
    return WorkspaceAgentResult(
        message=response["answer"][:16000],
        run_id=run_id,
        capability_calls=names,
        error_code=None if response["status"] == "completed" else "agent_incomplete",
        execution_receipts=receipts,
    )
