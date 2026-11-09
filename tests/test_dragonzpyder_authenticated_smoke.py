"""Authenticated integration smoke for the *real* Operly Kernel capability broker.

Uses a persisted, synthetic Personal account in an isolated SQLite database,
real HTTP/ASGI routing, signed short-lived leases, the real capability registry,
the real Kernel executor, and the actual native runtime-status provider.
No model calls, fake Kernel provider or external user credentials are involved.
"""
from __future__ import annotations

import os
import unittest
from uuid import uuid4
from unittest.mock import patch

import httpx
from fastapi import FastAPI

from apps.api.dragonzpyder_broker_router import router as broker_router
from packages.agent_runtime.remote_harness import issue_readonly_lease
from packages.database.db import SessionFactory, init_db
from packages.database.models import AppUser, Tenant, TenantMember
from packages.kernel.bootstrap import build_kernel_runtime
from packages.security.execution_context import ExecutionContext, ScopeKind
from packages.security.surfaces import SurfaceKind


class AuthenticatedDelegationSmoke(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.flags = patch.dict(
            os.environ, {
                "OPERLY_ENV": "development",
                "OPERLY_DRAGONZPYDER_AGENT_BROKER_ENABLED": "1",
                "OPERLY_DRAGONZPYDER_BROKER_TOKEN": "broker-smoke-" + "b" * 48,
                "OPERLY_DRAGONZPYDER_DELEGATION_KEY": "lease-smoke-" + "l" * 48,
            },
        )
        self.flags.start()
        self.addCleanup(self.flags.stop)
        await init_db()
        self.user_id = str(uuid4())
        async with SessionFactory() as db:
            db.add(AppUser(
                id=self.user_id,
                email="agent-smoke-" + self.user_id + "@example.invalid",
                display_name="Agent smoke test",
                active=True,
            ))
            await db.commit()
        context = ExecutionContext(
            workspace_id=None,
            user_id=self.user_id,
            membership_id=None,
            role="personal_owner",
            permissions=frozenset({"workspace:read", "tasks:read", "tasks:write"}),
            channel="web",
            surface=SurfaceKind.PERSONAL_PRIVATE,
            scope_kind=ScopeKind.PERSONAL,
            principal_id="user:" + self.user_id,
        )
        self.lease = issue_readonly_lease(
            context,
            run_id="authenticated-smoke-" + self.user_id,
            capabilities=[build_kernel_runtime().registry.get("system.runtime.status")],
        )
        self.headers = {
            "Authorization": "Bearer " + os.environ["OPERLY_DRAGONZPYDER_BROKER_TOKEN"],
            "X-Operly-Agent-Lease": self.lease,
        }
        app = FastAPI()
        app.include_router(broker_router)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://operly.internal.test",
            timeout=15,
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_authenticated_discover_execute_and_mutation_denied(self):
        anonymous = await self.client.post(
            "/api/internal/agent-tools/search",
            json={"query": "runtime status"},
        )
        self.assertEqual(anonymous.status_code, 401)

        discovered = await self.client.post(
            "/api/internal/agent-tools/search",
            headers=self.headers,
            json={"query": "runtime status"},
        )
        self.assertEqual(discovered.status_code, 200, discovered.text[:300])
        self.assertEqual(discovered.json()["source"], "operly")
        self.assertEqual(discovered.json()["scope_kind"], "personal")
        self.assertIsNone(discovered.json()["workspace_context"])
        visible = {tool["id"] for tool in discovered.json()["tools"]}
        self.assertIn("system.runtime.status", visible)
        self.assertNotIn("tasks.create", visible)

        executed = await self.client.post(
            "/api/internal/agent-tools/execute",
            headers=self.headers,
            json={
                "capability_id": "system.runtime.status",
                "arguments": {},
                "request_id": "auth-smoke-read-" + self.user_id,
            },
        )
        self.assertEqual(executed.status_code, 200, executed.text[:300])
        response = executed.json()
        self.assertTrue(response["ok"], response)
        self.assertEqual(response["capability_id"], "system.runtime.status")
        self.assertTrue(response["result"]["kernel_runtime_enabled"])
        self.assertEqual(response["result"]["scope_kind"], "personal")

        mutation = await self.client.post(
            "/api/internal/agent-tools/execute",
            headers=self.headers,
            json={
                "capability_id": "tasks.create",
                "arguments": {"title": "MUST NOT BE CREATED"},
                "request_id": "auth-smoke-denied-" + self.user_id,
            },
        )
        self.assertEqual(mutation.status_code, 403, mutation.text[:300])

    async def test_live_identity_recheck_after_revocation(self):
        before = await self.client.post(
            "/api/internal/agent-tools/search",
            headers=self.headers,
            json={"query": "runtime status"},
        )
        self.assertEqual(before.status_code, 200)
        async with SessionFactory() as db:
            user = await db.get(AppUser, self.user_id)
            user.active = False
            await db.commit()
        revoked = await self.client.post(
            "/api/internal/agent-tools/execute",
            headers=self.headers,
            json={
                "capability_id": "system.runtime.status",
                "arguments": {},
                "request_id": "auth-smoke-revoked-" + self.user_id,
            },
        )
        self.assertEqual(revoked.status_code, 403)
        no_context = await self.client.post(
            "/api/internal/agent-tools/search",
            headers=self.headers,
            json={"query": ""},
        )
        self.assertEqual(no_context.status_code, 403)

    async def test_workspace_scope_rechecks_live_membership(self):
        workspace_id = str(uuid4())
        member_id = str(uuid4())
        async with SessionFactory() as db:
            db.add(Tenant(id=workspace_id, name="Delegation smoke business"))
            db.add(TenantMember(
                id=member_id, tenant_id=workspace_id, user_id=self.user_id,
                role="owner",
            ))
            await db.commit()
        context = ExecutionContext(
            workspace_id=workspace_id,
            user_id=self.user_id,
            membership_id=member_id,
            role="owner",
            permissions=frozenset({"workspace:read"}),
            channel="web",
            surface=SurfaceKind.WORKSPACE_PRIVATE,
            scope_kind=ScopeKind.WORKSPACE,
            principal_id="user:" + self.user_id,
        )
        lease = issue_readonly_lease(
            context, run_id="workspace-smoke-" + workspace_id,
            capabilities=[build_kernel_runtime().registry.get("system.runtime.status")],
        )
        headers = dict(self.headers, **{"X-Operly-Agent-Lease": lease})
        context_reply = await self.client.post(
            "/api/internal/agent-tools/search",
            headers=headers,
            json={"query": ""},
        )
        self.assertEqual(context_reply.status_code, 200)
        info = context_reply.json()
        self.assertEqual(info["scope_kind"], "workspace")
        self.assertEqual(info["source"], "operly")
        self.assertEqual(info["workspace_context"]["name"], "Delegation smoke business")
        self.assertEqual(info["workspace_context"]["role"], "owner")
        self.assertEqual(info["workspace_context"]["timezone"], "UTC")
        response = await self.client.post(
            "/api/internal/agent-tools/execute",
            headers=headers,
            json={
                "capability_id": "system.runtime.status", "arguments": {},
                "request_id": "workspace-read-" + workspace_id,
            },
        )
        self.assertEqual(response.status_code, 200, response.text[:300])
        self.assertTrue(response.json()["ok"], response.text[:300])
        self.assertEqual(response.json()["result"]["scope_kind"], "workspace")
        self.assertEqual(response.json()["result"]["workspace_mode"], "full")

        async with SessionFactory() as db:
            membership = await db.get(TenantMember, member_id)
            await db.delete(membership)
            await db.commit()
        revoked = await self.client.post(
            "/api/internal/agent-tools/execute",
            headers=headers,
            json={
                "capability_id": "system.runtime.status", "arguments": {},
                "request_id": "workspace-revoked-" + workspace_id,
            },
        )
        self.assertEqual(revoked.status_code, 403)



if __name__ == "__main__":
    unittest.main()
