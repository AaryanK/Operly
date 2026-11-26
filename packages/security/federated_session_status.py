"""Fail-closed relying-party session status checks, no credential sharing with browsers.

Only the configured DragonZpyder issuer is contacted. A browser-session
revocation at the issuer conservatively invalidates older Operly sessions
for the same subject. No status cache hides a revocation on this path.
"""
import os
from datetime import datetime,timezone
from sqlalchemy import select

from packages.database.federated_identity_models import FederatedIdentityMap
from packages.security.identity_handoff import handoff_enabled

ISSUER="https://api.dragonzpyder.xyz"


async def federated_session_status(db, *, user_id, authenticated_at):
    if not handoff_enabled() and os.getenv("OPERLY_IDENTITY_OWNER")!="dragonzpyder":
        return True
    row=await db.scalar(select(FederatedIdentityMap).where(FederatedIdentityMap.operly_user_id==user_id))
    if row is None:
        return os.getenv("OPERLY_IDENTITY_OWNER")!="dragonzpyder"
    if row.status!="active" or row.issuer!=ISSUER or row.subject!=user_id:
        return False
    if os.getenv("OPERLY_FEDERATED_STATUS_ENABLED")!="1":
        return False
    secret=os.getenv("OPERLY_FEDERATED_STATUS_SECRET","")
    if len(secret)<43:
        return False
    import httpx
    try:
        async with httpx.AsyncClient(timeout=4,follow_redirects=False,trust_env=False) as client:
            response=await client.post(ISSUER+"/identity/v1/relying-party/status",
                json={"subject":row.subject},headers={"authorization":"Bearer "+secret})
        if response.status_code!=200 or len(response.content)>1024:
            return False
        body=response.json()
        if not isinstance(body,dict) or set(body)!={"active","revoked_at"} or body["active"] is not True:
            return False
        if body["revoked_at"] is None:
            return True
        revoked=datetime.fromisoformat(body["revoked_at"])
        if revoked.tzinfo is None:
            return False
        authenticated=authenticated_at if authenticated_at.tzinfo else authenticated_at.replace(tzinfo=timezone.utc)
        return authenticated>revoked
    except (httpx.HTTPError,ValueError,TypeError,AttributeError,OverflowError):
        return False
