"""Operly's thin business wrapper for DragonZpyder-owned persistent goals.

This is an internal client. It never runs an agent, stores a model checkpoint,
manages cron, or duplicates DragonZpyder's goal persistence. It must only be
called with an ExecutionContext resolved by Operly's auth layer.
"""
from __future__ import annotations

import hmac
from typing import Any

import httpx

from packages.agent_runtime.remote_harness import (
    RemoteHarnessUnavailable, dragonzpyder_endpoint, _secret,
)
from packages.security.execution_context import ExecutionContext


class GoalHubUnavailable(RuntimeError):
    pass


def _goal_hub() -> tuple[str, str]:
    try:
        endpoint = dragonzpyder_endpoint()
        token = _secret("OPERLY_DRAGONZPYDER_AGENT_TOKEN")
    except RemoteHarnessUnavailable as exc:
        raise GoalHubUnavailable("DragonZpyder goal hub is unavailable") from exc
    return endpoint.removesuffix("/internal/agents/operly/run") + "/internal/goals", token


def _principal(context: ExecutionContext) -> dict[str, Any]:
    if not isinstance(context, ExecutionContext) or not context.user_id:
        raise PermissionError("Authenticated user context is required")
    if context.is_workspace:
        if not context.workspace_id:
            raise PermissionError("Authenticated workspace is required")
    elif not context.is_personal or context.workspace_id is not None:
        raise PermissionError("Invalid scope")
    return {
        "kind": context.scope_kind.value,
        "user_id": context.user_id,
        "workspace_id": context.workspace_id,
    }


async def _call(operation: str, payload: dict) -> dict:
    if operation not in ("register", "get", "cancel"):
        raise ValueError("Unsupported goal operation")
    base, token = _goal_hub()
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=False, trust_env=False) as client:
            response = await client.post(
                base + "/" + operation, json=payload,
                headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
            )
        if response.status_code not in (200,) or len(response.content) > 24_000:
            raise GoalHubUnavailable("DragonZpyder goal operation could not be completed")
        data = response.json()
    except (httpx.HTTPError, ValueError, UnicodeError) as exc:
        raise GoalHubUnavailable("DragonZpyder goal hub request failed") from exc
    if (not isinstance(data, dict) or not isinstance(data.get("goal_id"), str)
            or data.get("status") not in
            ("registered", "awaiting_authorization", "paused", "cancelled", "completed")):
        raise GoalHubUnavailable("Unexpected goal hub response")
    return data


async def register_goal(*, context: ExecutionContext, request_id: str,
                        goal: str, schedule: dict | None) -> dict:
    return await _call("register", {
        **_principal(context),
        "request_id": request_id, "goal": goal, "schedule": schedule,
    })


async def read_goal(*, context: ExecutionContext, goal_id: str) -> dict:
    return await _call("get", {**_principal(context), "goal_id": goal_id})


async def cancel_goal(*, context: ExecutionContext, goal_id: str) -> dict:
    return await _call("cancel", {**_principal(context), "goal_id": goal_id})
