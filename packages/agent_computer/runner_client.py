from __future__ import annotations

import hashlib
import hmac
import json
import os

import aiohttp

from packages.custom_software.sandbox import SandboxFailure, SandboxUnavailable, validate_runner_url


class AgentComputerRunnerClient:
    """Authenticated client for the shared Railway Sandbox computer endpoint."""

    def __init__(self, *, url: str | None = None, token: str | None = None):
        self.url = (url or os.getenv("OPERLY_SANDBOX_RUNNER_URL", "")).rstrip("/")
        self.token = token or os.getenv("OPERLY_SANDBOX_RUNNER_TOKEN", "")

    async def execute(self, payload: dict) -> dict:
        if not self.url or not self.token:
            raise SandboxUnavailable("External isolated runner is not configured")
        url = validate_runner_url(self.url) + "/v1/computer/execute"
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        signature = hmac.new(self.token.encode(), raw, hashlib.sha256).hexdigest()
        timeout_seconds = max(10, min(int(payload.get("timeoutSeconds") or 120) + 30, 660))
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    url,
                    data=raw,
                    headers={
                        "Authorization": f"Bearer {self.token}",
                        "Content-Type": "application/json",
                        "X-Operly-Signature": signature,
                    },
                ) as response:
                    body = await response.read()
                    supplied = response.headers.get("X-Operly-Signature", "")
                    expected = hmac.new(self.token.encode(), body, hashlib.sha256).hexdigest()
                    if not supplied or not hmac.compare_digest(supplied, expected):
                        raise SandboxFailure("Agent computer runner response signature is invalid")
                    try:
                        parsed = json.loads(body or b"{}")
                    except json.JSONDecodeError as error:
                        raise SandboxFailure("Agent computer runner returned invalid JSON") from error
                    if response.status not in range(200, 300):
                        detail = str(parsed.get("detail") or "runner rejected computer execution")
                        failure = SandboxFailure(detail[:1000])
                        failure.status = response.status
                        failure.response_body = parsed
                        raise failure
                    return parsed
        except SandboxFailure:
            raise
        except (aiohttp.ClientError, TimeoutError) as error:
            raise SandboxFailure("Agent computer runner communication failed") from error
