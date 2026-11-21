"""Browser continuation of a Personal MCP approval (synthetic test identity)."""
from __future__ import annotations
import os
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, func

from apps.api.kernel_router import (
    execute_approved_personal_workspace_provision,
    personal_approval_decision,
    ApprovalDecisionInput,
)
from apps.api.dependencies import AccountAuthContext
from packages.database.db import SessionFactory, init_db
from packages.database.models import AppUser, AuthSession, Tenant, TenantMember
from packages.mcp.gateway import McpGateway, McpRequestContext
from packages.security.execution_context import ScopeKind


class DragonZpyderApprovedWorkspaceChatTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        flags = patch.dict(os.environ, {
            "OPERLY_ENV": "development",
            "OPERLY_DRAGONZPYDER_PERSONAL_MCP_ENABLED": "1",
            "OPERLY_PERSONAL_MCP_WORKSPACE_CREATE_ENABLED": "1",
        })
        flags.start()
        self.addCleanup(flags.stop)
        await init_db()
        self.user_id, self.other_user_id = str(uuid4()), str(uuid4())
        self.session_id, self.conversation_id = str(uuid4()), str(uuid4())
        async with SessionFactory() as db:
            for uid in (self.user_id, self.other_user_id):
                db.add(AppUser(id=uid, email=f"workspace-chat-{uid}@example.invalid",
                               display_name="Synthetic test user", active=True))
            db.add(AuthSession(
                id=self.session_id, user_id=self.user_id, tenant_id=None,
                token_hash=uuid4().hex + uuid4().hex,
                csrf_token_hash=uuid4().hex + uuid4().hex,
                expires_at=datetime.utcnow() + timedelta(hours=2),
                revoked_at=None,
            ))
            await db.commit()
        self.gateway = McpGateway()
        self.request = McpRequestContext(
            tenant_id=None, user_id=self.user_id, client_id="dragonzpyder_first_party",
            token_scopes=frozenset({"workspace.create", "workspace.list_owned"}),
            scope_kind=ScopeKind.PERSONAL, enforce_exposure=False,
            conversation_id=self.conversation_id,
        )

    async def test_chat_mcp_approval_browser_resume_and_readback(self):
        name = "DragonZpyder Chat Approved Workspace " + self.user_id[:8]
        arguments = {"name": name, "timezone": "UTC"}
        async with SessionFactory() as db:
            pending = await self.gateway.call_tool(
                db, self.request, tool_id="workspace.create",
                arguments=arguments, request_id="chat-test:" + str(uuid4()),
            )
            self.assertEqual(pending["status"], "waiting_for_approval", pending)
            approval = pending["approval_id"]
            self.assertIsNotNone(approval)
            owner = await db.get(AppUser, self.user_id)
            owner_session = await db.get(AuthSession, self.session_id)
            account = AccountAuthContext(user=owner, session=owner_session)
            other = AccountAuthContext(
                user=await db.get(AppUser, self.other_user_id),
                session=owner_session,
            )
            with self.assertRaises(HTTPException):
                await execute_approved_personal_workspace_provision(
                    approval, account=other, db=db,
                )
            with self.assertRaises(HTTPException):
                await execute_approved_personal_workspace_provision(
                    approval, account=account, db=db,
                )
            decided = await personal_approval_decision(
                approval, ApprovalDecisionInput(approved=True),
                account=account, db=db,
            )
            self.assertEqual(decided["status"], "approved")
            outcome = await execute_approved_personal_workspace_provision(
                approval, account=account, db=db,
            )
            self.assertEqual(outcome["status"], "completed")
            self.assertTrue(outcome["workspace_ownership_verified"])
            new_id = outcome["result"]["id"]
            self.assertEqual(outcome["result"]["name"], name)
            with self.assertRaises(HTTPException):
                await execute_approved_personal_workspace_provision(
                    approval, account=account, db=db,
                )
        # Independent DB session, no model-provided completion assertions.
        async with SessionFactory() as fresh:
            count = await fresh.scalar(select(func.count(Tenant.id)).where(Tenant.id == new_id))
            self.assertEqual(count, 1)
            membership = await fresh.scalar(select(TenantMember).where(
                TenantMember.tenant_id == new_id))
            self.assertEqual(membership.user_id, self.user_id)
            self.assertEqual(membership.role, "owner")

    async def test_disabled_feature_cannot_execute_already_approved_action(self):
        async with SessionFactory() as db:
            owner = await db.get(AppUser, self.user_id)
            session = await db.get(AuthSession, self.session_id)
            account = AccountAuthContext(user=owner, session=session)
            with patch.dict(os.environ, {"OPERLY_PERSONAL_MCP_WORKSPACE_CREATE_ENABLED": "0"}):
                with self.assertRaises(HTTPException) as denied:
                    await execute_approved_personal_workspace_provision(
                        str(uuid4()), account=account, db=db,
                    )
                self.assertEqual(denied.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
