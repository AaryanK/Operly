"""Opt-in Operly Personal → DragonZpyder harness delegation.

User identity comes exclusively from an already validated Operly AuthSession.
Only short-lived, capability-limited MCP credentials cross the service boundary;
no browser cookies, OAuth provider secrets, or user tokens are stored in prompts.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from packages.mcp.first_party import issue_personal_delegation
from packages.security.execution_context import ExecutionContext

RUN_PATH = "/internal/agents/operly/personal/run"


class DragonZpyderPersonalUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class PersonalAgentResult:
    message: str
    capability_calls: tuple[str, ...]
    run_id: str
    dispatch: str = "dragonzpyder_personal_mcp"
    objective_kind: str = "personal_goal"
    error_code: str | None = None

    def as_dict(self) -> dict:
        return {
            "message": self.message,
            "run_id": self.run_id,
            "dispatch": self.dispatch,
            "objective_kind": self.objective_kind,
            "error_code": self.error_code,
            "capability_calls": list(self.capability_calls),
            "approval_id": None,
            "objective_verified": False,  # Tool completion is not objective verification.
        }


def _endpoint() -> str:
    raw = os.getenv("OPERLY_DRAGONZPYDER_PERSONAL_RUN_URL", "").strip()
    parsed = urlsplit(raw)
    public = (parsed.scheme == "https" and
              parsed.hostname == "api.dragonzpyder.xyz" and parsed.port in (None, 443))
    private = (parsed.scheme == "http" and parsed.hostname is not None and
               parsed.hostname.endswith(".railway.internal")
               and parsed.port in (None, 8080))
    if (not (public or private) or parsed.path != RUN_PATH or
            parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise DragonZpyderPersonalUnavailable("Approved DragonZpyder endpoint not configured")
    return raw


async def run_personal_harness(
    db: AsyncSession, *, context: ExecutionContext, session_id: str,
    conversation_id: str, run_id: str, message: str,
) -> PersonalAgentResult:
    if not context.is_personal or not context.user_id or not session_id:
        raise DragonZpyderPersonalUnavailable("Live Personal authority is required")
    endpoint = _endpoint()
    token = os.getenv("OPERLY_DRAGONZPYDER_PERSONAL_AGENT_TOKEN", "")
    service_proof = os.getenv("OPERLY_DRAGONZPYDER_MCP_SERVICE_SECRET", "")
    if len(token) < 40 or len(service_proof) < 40:
        raise DragonZpyderPersonalUnavailable("Private delegation secrets are missing")
    base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
    bearer, approved = await issue_personal_delegation(
        db, session_id=session_id, user_id=context.user_id,
        conversation_id=conversation_id, query=message,
        resource=base + "/mcp",
    )
    body = {
        "scope_kind": "personal",
        "user_id": context.user_id,
        "session_id": session_id,
        "conversation_id": conversation_id,
        "prompt": message,
        "mcp_access_token": bearer,
        "approved_tool_names": sorted(approved),
    }
    try:
        async with httpx.AsyncClient(
            timeout=100, follow_redirects=False, trust_env=False,
        ) as http:
            reply = await http.post(
                endpoint, json=body,
                headers={
                    "Authorization": "Bearer " + token,
                    "X-Operly-Agent-Service": service_proof,
                },
            )
        if reply.status_code != 200 or len(reply.content) > 64_000:
            raise DragonZpyderPersonalUnavailable("DragonZpyder rejected Personal execution")
        response = reply.json()
    except (httpx.HTTPError, ValueError) as error:
        raise DragonZpyderPersonalUnavailable("DragonZpyder Personal harness unavailable") from error
    if (not isinstance(response, dict) or
            response.get("status") not in ("completed", "incomplete") or
            not isinstance(response.get("answer"), str)):
        raise DragonZpyderPersonalUnavailable("Invalid Personal harness response")
    names = response.get("successful_tools", [])
    if not isinstance(names, list):
        names = []
    return PersonalAgentResult(
        message=response["answer"][:16000],
        capability_calls=tuple(
            item for item in names if isinstance(item, str) and item.startswith("operly_")
        )[:40],
        run_id=run_id,
        error_code=None if response["status"] == "completed" else "agent_incomplete",
    )
