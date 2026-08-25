from __future__ import annotations

import hashlib
import hmac
import json
import os

import aiohttp

from packages.runtime_plugins.sandbox import SandboxFailure, SandboxUnavailable, validate_runner_url


class AgentComputerRunnerClient:
    """Authenticated client for the shared Railway Sandbox computer endpoint."""

    def __init__(self, *, url: str | None = None, token: str | None = None):
        self.url = (url or os.getenv("OPERLY_SANDBOX_RUNNER_URL", "")).rstrip("/")
        self.token = token or os.getenv("OPERLY_SANDBOX_RUNNER_TOKEN", "")

    def _endpoint(self, path: str) -> str:
        if not self.url or not self.token:
            raise SandboxUnavailable("External isolated runner is not configured")
        return validate_runner_url(self.url) + path

    async def _post(self, path: str, payload: dict, *, timeout_seconds: int) -> dict:
        url = self._endpoint(path)
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        signature = hmac.new(self.token.encode(), raw, hashlib.sha256).hexdigest()
        timeout = aiohttp.ClientTimeout(total=max(10, min(int(timeout_seconds), 690)))
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

    async def execute(self, payload: dict) -> dict:
        timeout_seconds = max(10, min(int(payload.get("timeoutSeconds") or 120) + 45, 675))
        return await self._post("/v1/computer/execute", payload, timeout_seconds=timeout_seconds)

    async def destroy(self, sandbox_id: str) -> dict:
        clean = str(sandbox_id or "").strip()
        if not clean:
            return {"ok": True, "destroyed": False, "reason": "no_session"}
        return await self._post(
            "/v1/computer/destroy",
            {"sandboxId": clean},
            timeout_seconds=45,
        )
