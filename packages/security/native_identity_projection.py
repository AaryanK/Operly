"""Provision only a new native DragonZpyder identity after verified OIDC login.

The issuer is authoritative. No mapping by email, no local credential, and
no membership/workspace creation. Collisions fail closed, including an
existing disabled or pending mapping.
"""
import os
from datetime import datetime,timezone
from uuid import UUID

from sqlalchemy import select,or_
from sqlalchemy.exc import IntegrityError
from packages.database.models import AppUser
from packages.database.federated_identity_models import FederatedIdentityMap
from packages.security.identity_handoff import handoff_enabled
from packages.security.federated_identity import FederationDenied,resolve_federated_user
from packages.security.federated_session_status import ISSUER


async def resolve_or_provision_native(db,*,verified_issuer,verified_subject):
    try:
        return await resolve_federated_user(db,verified_issuer=verified_issuer,
                                            verified_subject=verified_subject)
    except FederationDenied:
        pass
    if (not handoff_enabled() or os.getenv("OPERLY_NATIVE_PROJECTION_ENABLED")!="1"
            or os.getenv("OPERLY_FEDERATED_STATUS_ENABLED")!="1"
            or verified_issuer!=ISSUER):
        raise FederationDenied("Federated identity unavailable")
    try:
        if str(UUID(verified_subject))!=verified_subject:
            raise ValueError()
    except (ValueError,TypeError,AttributeError):
        raise FederationDenied("Invalid native subject") from None
    secret=os.getenv("OPERLY_FEDERATED_STATUS_SECRET","")
    if len(secret)<43:
        raise FederationDenied("Projection unavailable")
    # Never auto-revive an existing mapping, even if the issuer is active.
    existing=await db.scalar(select(FederatedIdentityMap).where(or_(
        FederatedIdentityMap.operly_user_id==verified_subject,
        (FederatedIdentityMap.issuer==verified_issuer)&(FederatedIdentityMap.subject==verified_subject))))
    if existing is not None or await db.get(AppUser,verified_subject) is not None:
        raise FederationDenied("Existing identity requires review")
    import httpx
    try:
        async with httpx.AsyncClient(timeout=4,follow_redirects=False,trust_env=False) as client:
            response=await client.post(ISSUER+"/identity/v1/relying-party/native-projection",
                json={"subject":verified_subject},headers={"authorization":"Bearer "+secret})
        if response.status_code!=200 or len(response.content)>4096:
            raise ValueError()
        data=response.json()
        if not isinstance(data,dict) or set(data)!={"subject","email","display_name","email_verified_at"}:
            raise ValueError()
        if data["subject"]!=verified_subject:
            raise ValueError()
        email=data["email"]; name=data["display_name"]
        if not isinstance(email,str) or not 3<=len(email)<=320 or "@" not in email:
            raise ValueError()
        if not isinstance(name,str) or not 1<=len(name)<=200:
            raise ValueError()
        verified_at=datetime.fromisoformat(data["email_verified_at"])
        if verified_at.tzinfo is None or verified_at>datetime.now(timezone.utc):
            raise ValueError()
        verified_at=verified_at.astimezone(timezone.utc).replace(tzinfo=None)
    except (httpx.HTTPError,ValueError,TypeError,AttributeError,OverflowError):
        raise FederationDenied("Projection unavailable") from None
    if await db.scalar(select(AppUser.id).where(AppUser.email==email)) is not None:
        raise FederationDenied("Existing account requires review")
    try:
        async with db.begin_nested():
            user=AppUser(id=verified_subject,email=email,display_name=name,
                         email_verified_at=verified_at,password_hash=None,active=True)
            db.add(user)
            await db.flush()
            db.add(FederatedIdentityMap(issuer=verified_issuer,subject=verified_subject,
                operly_user_id=verified_subject,status="active",activated_at=datetime.utcnow()))
            await db.flush()
        return user
    except IntegrityError:
        raise FederationDenied("Projection conflict; retry sign-in") from None
