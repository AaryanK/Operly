"""Operly workflow adapter: use DragonZpyder as workflow language owner.

Only a service-authenticated request; no user cookies, workspace database
connection, OAuth tokens or Personal credentials leave Operly. This adapter
is for read-only previews until durable cross-service job authorization is live.
"""
from __future__ import annotations

import os

import httpx

from packages.agent_runtime.remote_harness import (
    RemoteHarnessUnavailable, _secret, dragonzpyder_endpoint,
)


class RemoteWorkflowUnavailable(RuntimeError):
    pass


async def preview_remote_schedule(*, schedule: dict | None, count: int = 5) -> dict:
    # Uses the same approved origin validation as the agent handoff.
    try:
        agent_endpoint = dragonzpyder_endpoint()
        url = agent_endpoint.removesuffix("/internal/agents/operly/run") + "/internal/workflows/preview"
        token = _secret("OPERLY_DRAGONZPYDER_AGENT_TOKEN")
    except RemoteHarnessUnavailable as error:
        raise RemoteWorkflowUnavailable("DragonZpyder workflow service not configured") from error

    if not isinstance(count, int) or not 1 <= count <= 10:
        raise ValueError("Requested schedule preview count is outside policy")
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False, trust_env=False) as client:
            response = await client.post(
                url,
                headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
                json={"schedule": schedule, "count": count},
            )
        if response.status_code != 200 or len(response.content) > 32_000:
            raise RemoteWorkflowUnavailable("DragonZpyder workflow preview unavailable")
        body = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise RemoteWorkflowUnavailable("DragonZpyder workflow preview unavailable") from error

    if (not isinstance(body, dict) or body.get("owner") != "dragonzpyder"
            or not isinstance(body.get("occurrences"), list)
            or not all(isinstance(x, str) for x in body["occurrences"])
            or len(body["occurrences"]) > count
            or (body.get("schedule") is not None and not isinstance(body.get("schedule"), dict))
            or body.get("execution_started") is not False):
        raise RemoteWorkflowUnavailable("Invalid DragonZpyder workflow preview")
    return body
