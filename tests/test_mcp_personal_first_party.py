"""First-party Personal MCP identity and governed registry regression tests."""
from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import httpx
from fastapi import FastAPI

from apps.api.mcp_router import router
from packages.database.db import SessionFactory, init_db
from packages.database.models import AppUser, AuthSession, Task
from packages.mcp.first_party import (
    PersonalDelegationDenied,
    issue_personal_delegation,
)

ORIGIN = "https://operly-personal-mcp.test"


class PersonalMcpSessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.flags = patch.dict(os.environ, {
            "OPERLY_ENV": "development",
            "PUBLIC_BASE_URL": ORIGIN,
            "OPERLY_MCP_TOKEN_SECRET": "ci-personal-mcp-signing-fixture-" + "z" * 48,  # pragma: allowlist secret
            "OPERLY_DRAGONZPYDER_PERSONAL_MCP_ENABLED": "1",
            "OPERLY_DRAGONZPYDER_PERSONAL_MCP_MUTATIONS_ENABLED": "0",
            "OPERLY_DRAGONZPYDER_MCP_SERVICE_SECRET": "ci-first-party-private-proof-" + "z" * 48,  # pragma: allowlist secret
        })
        self.flags.start()
        self.addCleanup(self.flags.stop)
        await init_db()
        self.user_id, self.other_id = str(uuid4()), str(uuid4())
        self.session_id = str(uuid4())
        async with SessionFactory() as db:
            db.add(AppUser(
                id=self.user_id,
                email="mcp-personal-" + self.user_id + "@example.invalid",
                display_name="Test personal A", active=True,
            ))
            db.add(AppUser(
                id=self.other_id,
                email="mcp-personal-" + self.other_id + "@example.invalid",
                display_name="Test personal B", active=True,
            ))
            db.add(AuthSession(
                id=self.session_id,
                token_hash=uuid4().hex + uuid4().hex,
                csrf_token_hash=uuid4().hex + uuid4().hex,
                user_id=self.user_id, tenant_id=None,
                expires_at=datetime.utcnow() + timedelta(hours=4),
                revoked_at=None,
            ))
            self.own_task_id, self.other_task_id = str(uuid4()), str(uuid4())
            db.add(Task(
                id=self.own_task_id, owner_user_id=self.user_id,
                tenant_id=None, title="Owner A private test task", status="open",
            ))
            db.add(Task(
                id=self.other_task_id, owner_user_id=self.other_id,
                tenant_id=None, title="Owner B private test task", status="open",
            ))
            await db.commit()
            self.token, approved = await issue_personal_delegation(
                db, user_id=self.user_id, session_id=self.session_id,
                conversation_id=str(uuid4()), query="list my tasks",
                resource=ORIGIN + "/mcp",
            )
            self.approved = approved
        self.app = FastAPI()
        self.app.include_router(router)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url=ORIGIN,
        )
        self.addAsyncCleanup(self.client.aclose)

    async def _mcp(self, method, *, proof=True, params=None):
        headers = {
            "Authorization": "Bearer " + self.token,
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-06-18",
        }
        if proof:
            headers["X-Operly-Agent-Service"] = os.environ[
                "OPERLY_DRAGONZPYDER_MCP_SERVICE_SECRET"
            ]
        payload = {"jsonrpc": "2.0", "id": 17, "method": method}
        if params is not None:
            payload["params"] = params
        return await self.client.post("/mcp", json=payload, headers=headers)

    async def test_session_grants_exact_personal_read_tools_not_workspace_or_writes(self):
        self.assertIn("tasks.list", self.approved)
        self.assertNotIn("tasks.create", self.approved)
        response = await self._mcp("tools/list")
        self.assertEqual(response.status_code, 200, response.text[:400])
        names = {x["name"] for x in response.json()["result"]["tools"]}
        self.assertIn("tasks.list", names)
        self.assertNotIn("tasks.create", names)
        self.assertNotIn("workspace.describe", names)
        self.assertTrue(names.issubset(self.approved))

    async def test_actual_kernel_tasks_list_only_returns_signed_in_users_items(self):
        result = await self._mcp("tools/call", params={
            "name": "tasks.list", "arguments": {"limit": 100},
        })
        self.assertEqual(result.status_code, 200, result.text[:600])
        call = result.json()["result"]
        self.assertFalse(call["isError"], call)
        ids = {task["id"] for task in call["structuredContent"]["tasks"]}
        self.assertIn(self.own_task_id, ids)
        self.assertNotIn(self.other_task_id, ids)

        # No MCP prompt can elevate itself to a write or Workspace operation.
        blocked = await self._mcp("tools/call", params={
            "name": "tasks.create",
            "arguments": {"title": "Forbidden write"},
        })
        self.assertEqual(blocked.status_code, 200)
        self.assertTrue(blocked.json()["result"]["isError"])

    async def test_service_proof_required_even_with_valid_bearer(self):
        response = await self._mcp("tools/list", proof=False)
        self.assertEqual(response.status_code, 401)

    async def test_session_user_mismatch_cannot_mint_other_users_authority(self):
        async with SessionFactory() as db:
            with self.assertRaises(PersonalDelegationDenied):
                await issue_personal_delegation(
                    db, user_id=self.other_id, session_id=self.session_id,
                    conversation_id=str(uuid4()), query="my tasks",
                    resource=ORIGIN + "/mcp",
                )

    async def test_logout_invalidates_existing_personal_mcp_token(self):
        before = await self._mcp("tools/list")
        self.assertEqual(before.status_code, 200)
        async with SessionFactory() as db:
            row = await db.get(AuthSession, self.session_id)
            row.revoked_at = datetime.utcnow()
            await db.commit()
        after = await self._mcp("tools/list")
        self.assertEqual(after.status_code, 401)


if __name__ == "__main__":
    unittest.main()
