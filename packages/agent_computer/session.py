from __future__ import annotations

from datetime import datetime
from typing import Any

from packages.agent_computer.runner_client import AgentComputerRunnerClient
from packages.custom_software.sandbox import SandboxFailure, SandboxUnavailable
from packages.database.artifact_models import AgentRunRecord
from packages.database.db import session_scope


def _scope_matches(row: AgentRunRecord, metadata: dict[str, Any]) -> bool:
    tenant_id = str(metadata.get("tenant_id") or metadata.get("workspace_id") or "").strip()
    user_id = str(metadata.get("user_id") or metadata.get("actor_id") or "").strip()
    personal = str(metadata.get("surface") or "").startswith("personal") or bool(metadata.get("personal_scope"))
    if row.scope_kind == "workspace":
        return bool(tenant_id and row.tenant_id == tenant_id and row.owner_user_id is None)
    if row.scope_kind == "personal":
        return bool(user_id and row.owner_user_id == user_id and row.tenant_id is None and personal)
    return False


async def release_run_computer_session(
    runtime_run_id: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Best-effort idempotent cleanup for a completed AgentRun computer.

    The database handle is cleared even when Railway has already expired the sandbox.
    A cleanup outage must not turn a provider-verified user objective into a failed
    run; Railway idle expiry remains the secondary cleanup boundary.
    """
    run_id = str(runtime_run_id or "").strip()
    if not run_id:
        return {"released": False, "reason": "run_id_missing"}
    async with session_scope() as db:
        row = await db.get(AgentRunRecord, run_id)
        if row is None or not row.computer_session_id:
            return {"released": False, "reason": "session_missing"}
        if metadata and not _scope_matches(row, dict(metadata)):
            return {"released": False, "reason": "scope_mismatch"}
        sandbox_id = str(row.computer_session_id)
        receipt: dict[str, Any] = {}
        try:
            receipt = await AgentComputerRunnerClient().destroy(sandbox_id)
        except (SandboxFailure, SandboxUnavailable):
            receipt = {"ok": False, "destroyed": False, "cleanup_deferred_to_idle_expiry": True}
        row.computer_session_id = None
        row.computer_session_updated_at = datetime.utcnow()
        await db.commit()
        return {
            "released": True,
            "destroyed": bool(receipt.get("destroyed")),
            "expired": bool(receipt.get("expired")),
            "cleanup_deferred_to_idle_expiry": bool(receipt.get("cleanup_deferred_to_idle_expiry")),
        }
