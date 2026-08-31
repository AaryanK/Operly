from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any
from urllib.parse import quote

import httpx


class ComputerRunnerError(RuntimeError):
    pass


class ComputerRunnerClient:
    """Control-plane client for the existing Operly Sandbox Runner.

    The Operly API never executes agent shell/Python/browser code locally. Native
    Computer operations cross the already-deployed Sandbox Runner boundary, which
    allocates one Railway Sandbox VM per Computer session. The API stores only the
    opaque Railway sandbox ID; connector/provider secrets never enter the sandbox.
    """

    def __init__(self) -> None:
        self.base_url = os.getenv("OPERLY_SANDBOX_RUNNER_URL", "").strip().rstrip("/")
        self.token = os.getenv("OPERLY_SANDBOX_RUNNER_TOKEN", "").strip()
        self.timeout_seconds = max(
            5.0,
            min(float(os.getenv("OPERLY_AGENT_COMPUTER_TIMEOUT_SECONDS", "120")), 900.0),
        )

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.token)

    def _signature(self, method: str, path: str, raw: bytes) -> str:
        canonical = method.upper().encode("utf-8") + b"\n" + path.encode("utf-8") + b"\n" + raw
        return hmac.new(self.token.encode("utf-8"), canonical, hashlib.sha256).hexdigest()

    def _verify_response(self, response: httpx.Response) -> None:
        supplied = response.headers.get("x-operly-signature", "").strip()
        expected = hmac.new(self.token.encode("utf-8"), response.content, hashlib.sha256).hexdigest()
        if not supplied or not hmac.compare_digest(supplied, expected):
            raise ComputerRunnerError("Operly Sandbox Runner returned an invalid response signature")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        if not self.configured:
            raise ComputerRunnerError(
                "Operly Sandbox Runner is not configured. Set "
                "OPERLY_SANDBOX_RUNNER_URL and OPERLY_SANDBOX_RUNNER_TOKEN."
            )

        raw = b"" if payload is None else json.dumps(
            payload,
            separators=(",", ":"),
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.token}",
            "X-Operly-Signature": self._signature(method, path, raw),
            "User-Agent": "operly-agent-computer/2",
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"
        timeout = max(1.0, min(timeout_seconds or self.timeout_seconds, 930.0))

        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                response = await client.request(
                    method,
                    f"{self.base_url}{path}",
                    headers=headers,
                    content=raw,
                )
        except httpx.HTTPError as error:
            raise ComputerRunnerError("Operly Sandbox Runner is unavailable") from error

        self._verify_response(response)
        try:
            data = response.json()
        except ValueError as error:
            raise ComputerRunnerError("Operly Sandbox Runner returned invalid JSON") from error
        if not isinstance(data, dict):
            raise ComputerRunnerError("Operly Sandbox Runner returned an invalid response shape")

        if response.status_code >= 400 or data.get("ok") is False:
            message = str(
                data.get("detail")
                or data.get("error")
                or data.get("message")
                or "Sandbox Runner rejected the operation"
            )
            suffix = f" (HTTP {response.status_code})" if response.status_code >= 400 else ""
            raise ComputerRunnerError(message + suffix)
        return data

    async def health(self) -> dict[str, Any]:
        if not self.base_url:
            raise ComputerRunnerError("OPERLY_SANDBOX_RUNNER_URL is not configured")
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                response = await client.get(f"{self.base_url}/health")
        except httpx.HTTPError as error:
            raise ComputerRunnerError("Operly Sandbox Runner is unavailable") from error
        if response.status_code >= 400:
            raise ComputerRunnerError(f"Operly Sandbox Runner health check failed (HTTP {response.status_code})")
        try:
            data = response.json()
        except ValueError as error:
            raise ComputerRunnerError("Operly Sandbox Runner returned invalid health JSON") from error
        if not isinstance(data, dict):
            raise ComputerRunnerError("Operly Sandbox Runner returned an invalid health response")
        return data

    async def start(
        self,
        *,
        computer_session_id: str,
        workspace_id: str,
        principal_id: str,
        profile: str,
        ttl_seconds: int,
        network_policy: str,
    ) -> dict[str, Any]:
        # Legacy callers may still say "full". Agent Computer never joins the
        # Operly private network; normalize it to public-web sandbox networking.
        effective_network_policy = "web" if network_policy == "full" else network_policy
        return await self._request(
            "POST",
            "/v1/computer/sessions",
            payload={
                "client_session_id": computer_session_id,
                "workspace_id": workspace_id,
                "principal_id": principal_id,
                "profile": profile,
                "ttl_seconds": ttl_seconds,
                "network_policy": effective_network_policy,
            },
        )

    async def status(self, runtime_session_id: str) -> dict[str, Any]:
        runtime_id = quote(str(runtime_session_id), safe="")
        return await self._request("GET", f"/v1/computer/sessions/{runtime_id}")

    async def stop(self, runtime_session_id: str) -> dict[str, Any]:
        runtime_id = quote(str(runtime_session_id), safe="")
        return await self._request("DELETE", f"/v1/computer/sessions/{runtime_id}")

    async def tool(
        self,
        runtime_session_id: str,
        tool_id: str,
        arguments: dict[str, Any],
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        runtime_id = quote(str(runtime_session_id), safe="")
        tool = quote(str(tool_id), safe="._-")
        return await self._request(
            "POST",
            f"/v1/computer/sessions/{runtime_id}/tools/{tool}",
            payload={"arguments": arguments},
            timeout_seconds=timeout_seconds,
        )
