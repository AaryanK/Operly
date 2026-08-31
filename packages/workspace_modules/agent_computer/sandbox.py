from __future__ import annotations

import os
from typing import Any

import httpx


class ComputerRunnerError(RuntimeError):
    pass


class ComputerRunnerClient:
    """Client for the isolated Agent Computer runtime service.

    The Operly API never executes user/agent shell commands itself. All native
    computer operations cross this explicit runner boundary. A production runner
    is expected to isolate every session in its own container/microVM and enforce
    the requested network/resource policy.
    """

    def __init__(self) -> None:
        self.base_url = (
            os.getenv("OPERLY_AGENT_COMPUTER_RUNNER_URL", "").strip().rstrip("/")
            or os.getenv("OPERLY_SANDBOX_RUNNER_URL", "").strip().rstrip("/")
        )
        self.token = (
            os.getenv("OPERLY_AGENT_COMPUTER_RUNNER_TOKEN", "").strip()
            or os.getenv("OPERLY_SANDBOX_RUNNER_TOKEN", "").strip()
        )
        self.timeout_seconds = max(
            5.0,
            min(float(os.getenv("OPERLY_AGENT_COMPUTER_TIMEOUT_SECONDS", "120")), 900.0),
        )

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.token)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "User-Agent": "operly-agent-computer/1",
        }

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
                "Agent Computer runner is not configured. Set OPERLY_AGENT_COMPUTER_RUNNER_URL and OPERLY_AGENT_COMPUTER_RUNNER_TOKEN."
            )
        timeout = max(1.0, min(timeout_seconds or self.timeout_seconds, 900.0))
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                response = await client.request(
                    method,
                    f"{self.base_url}{path}",
                    headers=self._headers(),
                    json=payload,
                )
        except httpx.HTTPError as error:
            raise ComputerRunnerError("Agent Computer runner is unavailable") from error
        if response.status_code >= 400:
            message = "Agent Computer runner rejected the operation"
            try:
                body = response.json()
                if isinstance(body, dict):
                    message = str(body.get("detail") or body.get("message") or message)
            except ValueError:
                pass
            raise ComputerRunnerError(f"{message} (HTTP {response.status_code})")
        try:
            data = response.json()
        except ValueError as error:
            raise ComputerRunnerError("Agent Computer runner returned invalid JSON") from error
        if not isinstance(data, dict):
            raise ComputerRunnerError("Agent Computer runner returned an invalid response shape")
        return data

    async def health(self) -> dict[str, Any]:
        return await self._request("GET", "/v1/health", timeout_seconds=10)

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
        return await self._request(
            "POST",
            "/v1/sessions",
            payload={
                "client_session_id": computer_session_id,
                "workspace_id": workspace_id,
                "principal_id": principal_id,
                "profile": profile,
                "ttl_seconds": ttl_seconds,
                "network_policy": network_policy,
            },
        )

    async def status(self, runtime_session_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/v1/sessions/{runtime_session_id}")

    async def stop(self, runtime_session_id: str) -> dict[str, Any]:
        return await self._request("DELETE", f"/v1/sessions/{runtime_session_id}")

    async def tool(
        self,
        runtime_session_id: str,
        tool_id: str,
        arguments: dict[str, Any],
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/v1/sessions/{runtime_session_id}/tools/{tool_id}",
            payload={"arguments": arguments},
            timeout_seconds=timeout_seconds,
        )
