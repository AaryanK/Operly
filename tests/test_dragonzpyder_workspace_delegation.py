"""No-network, live-session Workspace MCP credential rejection tests."""
import os
import time
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from packages.mcp.first_party_workspace import (
    WORKSPACE_CLIENT_ID, WorkspaceDelegationDenied, validate_workspace_delegation,
)


class WorkspaceDelegationTests(unittest.IsolatedAsyncioTestCase):
    async def test_scoped_credentials_reject_impersonation_and_revocation(self):
        values = {
            "client_id": WORKSPACE_CLIENT_ID,
            "scope_kind": "workspace",
            "user_id": "user-00000001",
            "tenant_id": "space-00000001",
            "session_id": "session-00000001",
            "conversation_id": "conversation-00001",
            "resource": "https://operly.dragonzpyder.xyz/mcp",
            "scopes": ["workflow.list"],
            "iat": int(time.time()),
            "exp": int(time.time()) + 120,
        }
        secret = "s" * 48
        async def getter(cls, ident):
            if cls.__name__ == "AuthSession":
                return SimpleNamespace(
                    user_id=values["user_id"], tenant_id=values["tenant_id"],
                    revoked_at=None, expires_at=datetime.utcnow() + timedelta(minutes=5),
                )
            return SimpleNamespace(active=True)
        db = SimpleNamespace(get=getter, scalar=AsyncMock(return_value=SimpleNamespace()))
        with patch.dict(os.environ, {
            "OPERLY_DRAGONZPYDER_MCP_SERVICE_SECRET": secret,
            "OPERLY_DRAGONZPYDER_WORKSPACE_MCP_ENABLED": "1",
        }, clear=True):
            actual = await validate_workspace_delegation(
                db, claims=values, resource=values["resource"], service_proof=secret,
            )
            self.assertEqual(actual, (values["user_id"], values["tenant_id"],
                                      frozenset({"workflow.list"}), values["conversation_id"]))
            for mutation in [
                {"scope_kind": "personal"}, {"tenant_id": None},
                {"exp": int(time.time()) + 1000},
                {"scopes": ["workflow.list", "workflow.list"]},
                {"resource": "https://attacker.example/mcp"},
            ]:
                with self.subTest(mutation=mutation), self.assertRaises(WorkspaceDelegationDenied):
                    await validate_workspace_delegation(
                        db, claims={**values, **mutation}, resource=values["resource"],
                        service_proof=secret,
                    )
            with self.assertRaises(WorkspaceDelegationDenied):
                await validate_workspace_delegation(
                    db, claims=values, resource=values["resource"],
                    service_proof="wrong" * 15,
                )
            db.scalar.return_value = None
            with self.assertRaises(WorkspaceDelegationDenied):
                await validate_workspace_delegation(
                    db, claims=values, resource=values["resource"],
                    service_proof=secret,
                )

    async def test_disabled_service_rejects_valid_credentials(self):
        from packages.mcp.first_party_workspace import _require_service_proof
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(WorkspaceDelegationDenied):
                _require_service_proof("a" * 48)


if __name__ == "__main__":
    unittest.main()
