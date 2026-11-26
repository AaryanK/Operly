"""Staged per-account local credential fence, before global identity cutover.

Existing mapping rows are operator-controlled. Pending, active and disabled
mappings all fence local credential issuance during the staged handoff.
A disabled mapping is not an instruction to restore local authentication.
"""
import os
from sqlalchemy import select

from packages.database.models import AppUser
from packages.database.federated_identity_models import FederatedIdentityMap


def handoff_enabled():
    return os.getenv("OPERLY_IDENTITY_HANDOFF_ENABLED") == "1"


async def local_identity_allowed(db, user_id):
    if not handoff_enabled():
        return True
    # Hold the same user row lock as the operator's handoff transaction.
    # Caller must retain the transaction until credential/session commit.
    # no_autoflush prevents an unapproved credential change being flushed
    # before obtaining the lock.
    with db.no_autoflush:
        user = await db.scalar(select(AppUser.id).where(AppUser.id == user_id).with_for_update())
        if user is None:
            return False
        mapped = await db.scalar(select(FederatedIdentityMap.operly_user_id)
            .where(FederatedIdentityMap.operly_user_id == user_id))
    return mapped is None
