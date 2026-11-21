"""Identity mapping after an independently verified OIDC access token.

This resolver never accepts user-supplied email, bearer strings, sessions or
workspace IDs. Call it only with a cryptographically verified issuer + subject.
It does not authorize workspace access; existing membership checks still apply.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.federated_identity_models import FederatedIdentityMap
from packages.database.models import AppUser


class FederationDenied(PermissionError):
    pass


async def resolve_federated_user(
    db: AsyncSession, *, verified_issuer: str, verified_subject: str,
) -> AppUser:
    if (not isinstance(verified_issuer, str) or not verified_issuer.startswith("https://")
            or len(verified_issuer) > 255
            or not isinstance(verified_subject, str)
            or not 1 <= len(verified_subject) <= 160):
        raise FederationDenied("Invalid verified principal")
    row = await db.scalar(select(FederatedIdentityMap).where(
        FederatedIdentityMap.issuer == verified_issuer,
        FederatedIdentityMap.subject == verified_subject,
        FederatedIdentityMap.status == "active",
    ))
    if row is None:
        raise FederationDenied("Federated user is not activated")
    user = await db.get(AppUser, row.operly_user_id)
    if user is None or not user.active:
        raise FederationDenied("Federated user unavailable")
    return user
