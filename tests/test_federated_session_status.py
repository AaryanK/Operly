"""No external calls; verify the relying-party session revalidation contract."""
from types import SimpleNamespace
from datetime import datetime,timezone
import httpx
import pytest
from packages.security import federated_session_status as mod

@pytest.mark.asyncio
async def test_revalidate_mapped_session(monkeypatch):
    monkeypatch.setenv("OPERLY_IDENTITY_HANDOFF_ENABLED","1")
    monkeypatch.setenv("OPERLY_FEDERATED_STATUS_ENABLED","1")
    monkeypatch.setenv("OPERLY_FEDERATED_STATUS_SECRET","synthetic-test-only-"+"x"*43)
    monkeypatch.delenv("OPERLY_IDENTITY_OWNER",raising=False)
    row=SimpleNamespace(status="active",issuer=mod.ISSUER,subject="synthetic-user")
    state={"active":True,"revoked_at":None}
    calls=[]
    class DB:
        async def scalar(self,q): return row
    class Client:
        def __init__(self,**kwargs):
            assert kwargs["follow_redirects"] is False
            assert kwargs["trust_env"] is False
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def post(self,url,**kwargs):
            calls.append(url)
            assert kwargs["json"]=={"subject":"synthetic-user"}
            if state.get("unavailable"): raise httpx.ConnectError("synthetic outage")
            return httpx.Response(200,json=state)
    monkeypatch.setattr(mod.httpx,"AsyncClient",Client)
    async def check():
        return await mod.federated_session_status(DB(),user_id="synthetic-user",
            authenticated_at=datetime(2026,1,1,tzinfo=timezone.utc))
    assert await check()
    state["revoked_at"]="2026-01-02T00:00:00+00:00"
    assert not await check()
    state["revoked_at"]="2025-12-31T00:00:00+00:00"
    assert await check()
    state["active"]=False
    assert not await check()
    state["unavailable"]=True
    assert not await check()
    row.status="pending"
    count=len(calls)
    assert not await check()
    assert len(calls)==count
    row.status="disabled"
    assert not await check()
    monkeypatch.delenv("OPERLY_IDENTITY_HANDOFF_ENABLED")
    assert await check()
