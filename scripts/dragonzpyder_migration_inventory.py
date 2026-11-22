"""Read-only Operly-to-DragonZpyder cutover inventory: no secrets, IDs or PII.

Runs only on the operator's selected Operly database. This tool does NOT
activate accounts, change workflows, transfer credentials, or call DragonZpyder.
Use read-only database credentials and capture a database backup separately.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from packages.database.db import SessionFactory

# Deliberately fixed reviewed identifiers; never accept SQL table names as CLI input.
TABLES = {
    "accounts": "app_users",
    "login_provider_links": "auth_identities",
    "federated_subject_maps": "federated_identity_maps",
    "workspace_memberships": "tenant_members",
    "personal_connectors": "account_connectors",
    "encrypted_connector_secrets": "account_connector_secrets",
    "personal_memory_rows": "memories",
    "agent_conversations": "agent_chat_conversations",
    "agent_messages": "agent_chat_messages",
    "stored_artifacts": "operly_artifacts",
    "workflow_definitions": "workflow_definitions",
    "workflow_versions": "workflow_versions",
    "workflow_schedules": "workflow_schedules",
    "workflow_event_triggers": "workflow_event_triggers",
    "workflow_runs": "workflow_runs",
    "workflow_step_attempts": "workflow_step_attempts",
    "legacy_scheduled_jobs": "scheduled_jobs",
}


async def snapshot_counts(session: AsyncSession) -> dict:
    """Capture aggregate cardinalities only; no user rows or credentials."""
    counts = {}
    for category, table in TABLES.items():
        count = await session.scalar(text(f"SELECT COUNT(*) FROM {table}"))
        if type(count) is not int or count < 0:
            raise RuntimeError(f"Migration inventory failed for {category}")
        counts[category] = count
    active_accounts = await session.scalar(
        text("SELECT COUNT(*) FROM app_users WHERE active = TRUE")
    )
    if type(active_accounts) is not int or not 0 <= active_accounts <= counts["accounts"]:
        raise RuntimeError("Active account inventory mismatch")
    status_counts = {}
    for status, number in (
        await session.execute(text(
            "SELECT status, COUNT(*) FROM workflow_runs GROUP BY status ORDER BY status"
        ))
    ).all():
        if not isinstance(status, str) or type(number) is not int or number < 0:
            raise RuntimeError("Workflow run inventory invalid")
        status_counts[status] = number
    if sum(status_counts.values()) != counts["workflow_runs"]:
        raise RuntimeError("Workflow run reconciliation failed")
    return {
        "format": "dragonzpyder-cutover-inventory/v1",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "operly",
        "counts": counts,
        "active_accounts": active_accounts,
        "workflow_run_status_counts": status_counts,
        "contains_personal_records": False,
        "backups_verified": False,
        "source_to_destination_reconciliation_verified": False,
        "production_activation_authorized": False,
    }


async def main() -> None:
    async with SessionFactory() as db:
        value = await snapshot_counts(db)
    print(json.dumps(value, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    asyncio.run(main())
