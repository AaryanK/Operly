"""Operly's transitional wrapper for the DragonZpyder-owned LangGraph harness.

The remote model receives an expiring, signed, read-only capability lease.
Operly retains authority over workspace membership and each tool invocation.
No browser cookies or provider credentials are sent to DragonZpyder.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from packages.kernel.contracts import CapabilityRisk
from packages.security.execution_context import ExecutionContext

_ID = re.compile(r"^[a-z][a-z0-9_.:-]{1,159}$")
_ALLOWED_DZ_HOSTS = {"api.dragonzpyder.xyz"}


class RemoteHarnessUnavailable(RuntimeError):
    pass


def _secret(name: str) -> str:
    secret = os.getenv(name, "")
    if len(secret) < 40:
        raise RemoteHarnessUnavailable(f"{name} is not configured")
    return secret


def _b64(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def issue_readonly_lease(context: ExecutionContext, *, run_id: str, capabilities) -> str:
    """Mint authority for only already-available read-only capabilities."""
    key = _secret("OPERLY_DRAGONZPYDER_DELEGATION_KEY").encode()
    allowed = sorted({spec.id for spec in capabilities
                      if spec.risk is CapabilityRisk.READ_ONLY and _ID.fullmatch(spec.id)})[:20]
    if not context.user_id or (context.is_workspace and not context.workspace_id):
        raise RemoteHarnessUnavailable("A valid human principal is required")
    now = int(time.time())
    claims = {
        "v": 1, "iss": "operly", "aud": "operly-agent-readonly",
        "iat": now, "exp": now + 240, "jti": str(uuid4()),
        "run_id": run_id, "scope_kind": context.scope_kind.value,
        "user_id": context.user_id, "workspace_id": context.workspace_id,
        "conversation_id": context.conversation_id,
        "capability_ids": allowed,
    }
    blob = _b64(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return blob + "." + _b64(hmac.new(key, blob.encode(), hashlib.sha256).digest())


def validate_readonly_lease(value: str) -> dict:
    """Only Operly validates the lease: the remote model is not an authority."""
    try:
        if len(value) > 6000 or value.count(".") != 1:
            raise ValueError()
        blob, mac = value.split(".")
        key = _secret("OPERLY_DRAGONZPYDER_DELEGATION_KEY").encode()
        expected = hmac.new(key, blob.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64(mac)):
            raise ValueError()
        data = json.loads(_unb64(blob))
        now = int(time.time())
        if not isinstance(data, dict) or data.get("v") != 1 or data.get("iss") != "operly" or data.get("aud") != "operly-agent-readonly":
            raise ValueError()
        if not isinstance(data.get("iat"), int) or not isinstance(data.get("exp"), int) or not data["iat"] - 30 <= now <= data["exp"] or data["exp"] - data["iat"] > 240:
            raise ValueError()
        if data.get("scope_kind") not in ("workspace", "personal") or not isinstance(data.get("user_id"), str) or not data["user_id"]:
            raise ValueError()
        if data["scope_kind"] == "workspace" and not data.get("workspace_id"):
            raise ValueError()
        if data["scope_kind"] == "personal" and data.get("workspace_id") is not None:
            raise ValueError()
        ids = data.get("capability_ids")
        if not isinstance(ids, list) or len(ids) > 40 or not all(isinstance(x, str) and _ID.fullmatch(x) for x in ids) or len(ids) != len(set(ids)):
            raise ValueError()
        if not isinstance(data.get("run_id"), str) or not data["run_id"]:
            raise ValueError()
        return data
    except (TypeError, ValueError, KeyError, UnicodeError, base64.binascii.Error) as exc:
        raise PermissionError("Invalid or expired delegated capability lease") from exc


def dragonzpyder_endpoint() -> str:
    url = os.getenv("OPERLY_DRAGONZPYDER_AGENT_URL", "").strip()
    parsed = urlsplit(url)
    private = parsed.scheme == "http" and bool(parsed.hostname) and parsed.hostname.endswith(".railway.internal")
    public = parsed.scheme == "https" and parsed.hostname in _ALLOWED_DZ_HOSTS
    if (not (public or private) or parsed.username or parsed.password or parsed.query or
            parsed.fragment or parsed.path != "/internal/agents/operly/run" or parsed.port not in (None, 80, 443, 8080)):
        raise RemoteHarnessUnavailable("Approved DragonZpyder agent endpoint is not configured")
    return url


@dataclass(frozen=True)
class DelegatedAgentResult:
    message: str
    run_id: str
    dispatch: str = "dragonzpyder_langgraph"
    objective_kind: str = "delegated_goal"
    error_code: str | None = None
    capability_calls: tuple[str, ...] = ()
    approval_id: str | None = None

    def as_dict(self) -> dict:
        return {
            "message": self.message, "run_id": self.run_id,
            "dispatch": self.dispatch, "objective_kind": self.objective_kind,
            "error_code": self.error_code,
            "capability_calls": list(self.capability_calls), "approval_id": None,
            "objective_verified": False,
        }


async def run_dragonzpyder_harness(*, context: ExecutionContext, message: str,
                                 conversation_id: str, run_id: str, capabilities) -> DelegatedAgentResult:
    endpoint = dragonzpyder_endpoint()
    auth = _secret("OPERLY_DRAGONZPYDER_AGENT_TOKEN")
    lease = issue_readonly_lease(context, run_id=run_id, capabilities=capabilities)
    payload = {
        "thread_id": conversation_id, "prompt": message,
        "scope_kind": context.scope_kind.value,
        "user_id": context.user_id,
        "workspace_id": context.workspace_id,
        "lease_token": lease,
    }
    try:
        async with httpx.AsyncClient(timeout=90.0, follow_redirects=False, trust_env=False) as client:
            response = await client.post(endpoint, json=payload, headers={
                "Authorization": "Bearer " + auth, "Accept": "application/json",
            })
        if response.status_code != 200:
            raise RemoteHarnessUnavailable("DragonZpyder agent service rejected the delegated run")
        if len(response.content) > 64_000:
            raise RemoteHarnessUnavailable("DragonZpyder agent response is too large")
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise RemoteHarnessUnavailable("DragonZpyder agent service is unavailable") from exc
    if not isinstance(data, dict) or not isinstance(data.get("message"), str) or not data["message"].strip():
        raise RemoteHarnessUnavailable("Invalid response from DragonZpyder")
    return DelegatedAgentResult(
        message=data["message"][:16000],
        run_id=run_id,
        error_code=("agent_incomplete" if data.get("status") != "completed" else None),
        capability_calls=tuple(str(x) for x in data.get("successful_tools", [])
                               if isinstance(x, str) and x.startswith("operly_"))[:40],
    )
