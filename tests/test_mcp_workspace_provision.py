"""End-to-end MCP -> Kernel approval -> workspace provisioning -> readback.

Uses disposable test database and a synthesized logged-in session, not a
production user. The test never bypasses the exact-invocation approval path.
"""
from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import select, func

from packages.database.db import SessionFactory, init_db
from packages.database.models import AppUser, AuthSession, Tenant, TenantMember
from packages.mcp.first_party import issue_personal_delegation
from packages.mcp.gateway import McpGateway, McpRequestContext
from packages.kernel.approvals import decide_approval
from packages.security.execution_context import ScopeKind, resolve_personal_execution_context
from packages.security.surfaces import SurfaceKind


class WorkspaceProvisionMcpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.flags = patch.dict(os.environ, {
            "OPERLY_ENV": "development",
            "PUBLIC_BASE_URL": "https://operly-workspace-provision.test",
            "OPERLY_MCP_TOKEN_SECRET": "ci-workspace-provision-fixture-" + "q" * 48,  # pragma: allowlist secret
            "OPERLY_DRAGONZPYDER_PERSONAL_MCP_ENABLED": "1",
            "OPERLY_DRAGONZPYDER_PERSONAL_MCP_MUTATIONS_ENABLED": "0",
            "OPERLY_PERSONAL_MCP_WORKSPACE_CREATE_ENABLED": "1",
            "OPERLY_DRAGONZPYDER_MCP_SERVICE_SECRET": "ci-provision-service-proof-" + "q" * 48,  # pragma: allowlist secret
        })
        self.flags.start()
        self.addCleanup(self.flags.stop)
        await init_db()
        self.user_id = str(uuid4())
        self.other_user_id = str(uuid4())
        self.session_id = str(uuid4())
        self.conversation_id = str(uuid4())
        async with SessionFactory() as db:
            for uid in (self.user_id, self.other_user_id):
                db.add(AppUser(id=uid, email=f"provision-{uid}@example.invalid",
                               display_name="Test", active=True))
            db.add(AuthSession(
                id=self.session_id, user_id=self.user_id, tenant_id=None,
                token_hash=uuid4().hex + uuid4().hex,
                csrf_token_hash=uuid4().hex + uuid4().hex,
                expires_at=datetime.utcnow() + timedelta(hours=2), revoked_at=None,
            ))
            await db.commit()
        self.gateway = McpGateway()
        self.request = McpRequestContext(
            tenant_id=None, user_id=self.user_id,
            client_id="dragonzpyder_first_party",
            token_scopes=frozenset({"workspace.create", "workspace.list_owned"}),
            conversation_id=self.conversation_id, enforce_exposure=False,
            scope_kind=ScopeKind.PERSONAL,
        )

    async def test_approval_then_create_and_list_owner_and_exact_replay(self):
        from packages.mcp.first_party import validate_personal_delegation
        from packages.mcp.oauth import decode_access_token
        async with SessionFactory() as db:
            token, approved = await issue_personal_delegation(
                db, session_id=self.session_id, user_id=self.user_id,
                conversation_id=self.conversation_id,
                resource="https://operly-workspace-provision.test/mcp",
                query="create workspace",
            )
            self.assertIn("workspace.create", approved)
            self.assertNotIn("tasks.create", approved)
            claims = decode_access_token(token)
            user, scopes, conv = await validate_personal_delegation(
                db, claims=claims,
                resource="https://operly-workspace-provision.test/mcp",
                service_proof=os.environ["OPERLY_DRAGONZPYDER_MCP_SERVICE_SECRET"],
            )
            self.assertEqual(user, self.user_id)
            self.assertEqual(conv, self.conversation_id)
            self.request = McpRequestContext(
                tenant_id=None, user_id=user,
                client_id="dragonzpyder_first_party",
                token_scopes=scopes, conversation_id=conv,
                enforce_exposure=False, scope_kind=ScopeKind.PERSONAL,
            )
            visible = {s["name"] for s in await self.gateway.list_tools(db, self.request)}
            self.assertIn("workspace.create", visible)
            stable = "ci-provision:" + str(uuid4())
            arguments = {"name": "AARYA MCP Test " + self.user_id[:8], "timezone": "UTC"}
            first = await self.gateway.call_tool(
                db, self.request, tool_id="workspace.create",
                arguments=arguments, request_id=stable,
            )
            self.assertEqual(first["status"], "waiting_for_approval", first)
            approval_id = first["approval_id"]
            self.assertIsNotNone(approval_id)
            self.assertIsNone(await db.scalar(select(Tenant.id).where(Tenant.name == arguments["name"])))
            context = await resolve_personal_execution_context(
                db, user_id=self.user_id, channel="web",
                surface=SurfaceKind.PERSONAL_PRIVATE,
                conversation_id=self.conversation_id,
            )
            await decide_approval(
                db, context=context, approval_id=approval_id, approved=True,
                decided_by_user_id=self.user_id,
            )
            await db.commit()
            second = await self.gateway.call_tool(
                db, self.request, tool_id="workspace.create",
                arguments=arguments, request_id=stable, approval_id=approval_id,
            )
            self.assertTrue(second["ok"], second)
            workspace_id = second["result"]["id"]
            member = await db.scalar(select(TenantMember).where(
                TenantMember.tenant_id == workspace_id))
            self.assertEqual(member.user_id, self.user_id)
            self.assertEqual(member.role, "owner")
            replay = await self.gateway.call_tool(
                db, self.request, tool_id="workspace.create",
                arguments=arguments, request_id=stable, approval_id=approval_id,
            )
            self.assertTrue(replay["ok"], replay)
            self.assertEqual(replay["result"]["id"], workspace_id)
            self.assertEqual(await db.scalar(select(func.count(Tenant.id)).where(
                Tenant.name == arguments["name"])), 1)
            # A fresh read is authoritative; output text alone is not completion.
            result = await self.gateway.call_tool(
                db, self.request, tool_id="workspace.list_owned", arguments={},
            )
            self.assertIn(workspace_id, {v["id"] for v in result["result"]["workspaces"]})
            # No automatic tenant-scoped execution permission follows provisioning.
            self.assertNotIn("workspace.describe", visible)

            # Separate Workspace authority: existing Workspace OS POST-to-MCP
            # record capability. Membership and permissions are re-resolved.
            workspace_scope = McpRequestContext(
                tenant_id=workspace_id, user_id=self.user_id,
                client_id="dragonzpyder", grant_id="isolated-test-grant",
                token_scopes=frozenset({"workspace:*"}),
                conversation_id=self.conversation_id,
                enforce_exposure=False, scope_kind=ScopeKind.WORKSPACE,
            )
            # The default MCP list is bounded; exact-name tool resolution must
            # still independently authorize an already-known capability. A
            # separate paginated-discovery feature is required for broad catalogs.
            verified_context, verified_spec = await self.gateway._available_spec(
                db, workspace_scope, "workspace_os.crm.contacts.create"
            )
            self.assertEqual(verified_spec.id, "workspace_os.crm.contacts.create")
            self.assertEqual(verified_context.workspace_id, workspace_id)
            contact = await self.gateway.call_tool(
                db, workspace_scope, tool_id="workspace_os.crm.contacts.create",
                arguments={"name": "MCP Example Contact " + self.user_id[:8]},
                request_id="ci-contact:" + str(uuid4()),
            )
            self.assertTrue(contact["ok"], contact)
            contact_id = contact["result"]["id"]
            listed_contacts = await self.gateway.call_tool(
                db, workspace_scope, tool_id="workspace_os.crm.contacts.list",
                arguments={"limit": 100},
            )
            self.assertTrue(listed_contacts["ok"], listed_contacts)
            self.assertIn(contact_id, {i["id"] for i in listed_contacts["result"]["items"]})
            # Prevent forged principal from reading this workspace even if it
            # supplies a valid workspace ID and a blanket token-scopes hint.
            forged = McpRequestContext(
                tenant_id=workspace_id, user_id=self.other_user_id,
                client_id="dragonzpyder", token_scopes=frozenset({"workspace:*"}),
                enforce_exposure=False, scope_kind=ScopeKind.WORKSPACE,
            )
            with self.assertRaises((PermissionError, LookupError, RuntimeError)):
                await self.gateway.list_tools(db, forged)

    async def test_feature_disabled_does_not_expose_creation(self):
        with patch.dict(os.environ, {"OPERLY_PERSONAL_MCP_WORKSPACE_CREATE_ENABLED": "0"}):
            async with SessionFactory() as db:
                self.assertNotIn("workspace.create", {
                    t["name"] for t in await self.gateway.list_tools(db, self.request)})
                self.assertIn("workspace.list_owned", {
                    t["name"] for t in await self.gateway.list_tools(db, self.request)})


if __name__ == "__main__":
    unittest.main()


def test_first_party_mcp_idempotency_namespaces_are_conversation_bound():
    from packages.kernel.idempotency import _ingress_namespace, IdempotencyConflict
    from packages.security.execution_context import ExecutionContext
    base = dict(
        workspace_id=None, user_id="user-1", membership_id=None,
        role="personal_owner", permissions=frozenset({"workspace:create"}),
        channel="mcp", scope_kind=ScopeKind.PERSONAL,
        metadata={"ingress": "operly_mcp", "mcp_client_id": "dragonzpyder_first_party",
                  "mcp_grant_id": ""},
    )
    first = ExecutionContext(**base, conversation_id="conversation-a")
    second = ExecutionContext(**base, conversation_id="conversation-b")
    assert _ingress_namespace(first) != _ingress_namespace(second)
    with unittest.TestCase().assertRaises(IdempotencyConflict):
        _ingress_namespace(ExecutionContext(**base, conversation_id=None))
    forged = ExecutionContext(
        **{**base, "metadata": {"ingress": "operly_mcp",
                                "mcp_client_id": "unapproved_external_client",
                                "mcp_grant_id": ""}},
        conversation_id="conversation-a",
    )
    with unittest.TestCase().assertRaises(IdempotencyConflict):
        _ingress_namespace(forged)
