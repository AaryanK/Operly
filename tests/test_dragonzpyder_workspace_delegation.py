"""Fail-closed Workspace MCP delegation; no credential or real provider calls."""
import asyncio
import os
import time
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from packages.mcp.first_party_workspace import (
    WorkspaceDelegationDenied, _current_scope,
    validate_workspace_delegation, WORKSPACE_CLIENT_ID,
)


@pytest.mark.asyncio
async def test_exact_scope_and_live_session_required():
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
    async def getter(cls, id):
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
    }):
        output = await validate_workspace_delegation(
            db, claims=values, resource=values["resource"], service_proof=secret,
        )
        assert output == (values["user_id"], values["tenant_id"], frozenset({"workflow.list"}),
                          values["conversation_id"])
        for mutated in (
            {"scope_kind": "personal"},
            {"tenant_id": None},
            {"exp": int(time.time()) + 1000},
            {"scopes": ["workflow.list", "workflow.list"]},
            {"resource": "https://attacker.example/mcp"},
        ):
            with pytest.raises(WorkspaceDelegationDenied):
                await validate_workspace_delegation(
                    db, claims={**values, **mutated}, resource=values["resource"],
                    service_proof=secret,
                )
        with pytest.raises(WorkspaceDelegationDenied):
            await validate_workspace_delegation(
                db, claims=values, resource=values["resource"],
                service_proof="wrong" * 15,
            )
        db.scalar.return_value = None
        with pytest.raises(WorkspaceDelegationDenied):
            await validate_workspace_delegation(
                db, claims=values, resource=values["resource"], service_proof=secret,
            )


@pytest.mark.asyncio
async def test_no_service_proof_even_with_valid_token():
    from packages.mcp.first_party_workspace import _require_service_proof
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(WorkspaceDelegationDenied):
            _require_service_proof(None)
