"""Create a local-only owner account for acceptance testing.

Credentials are accepted from environment variables and are never printed. The
command refuses to run when the public URL is HTTPS or the environment is marked
as production.
"""
import asyncio
import os

from sqlalchemy import select

from apps.api.security import hash_password
from packages.database.db import session_scope
from packages.database.models import AppUser, Tenant, TenantMember


def ensure_development() -> None:
    environment = os.getenv("OPERLY_ENV", "development").strip().lower()
    public_url = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").strip().lower()
    if environment in {"production", "prod"} or public_url.startswith("https://"):
        raise RuntimeError("Development account creation is disabled in production")


async def main() -> None:
    ensure_development()
    email = os.environ["OPERLY_DEV_EMAIL"].strip().lower()
    password = os.environ["OPERLY_DEV_PASSWORD"]
    tenant_name = os.getenv("OPERLY_DEV_WORKSPACE", "Dashboard Studio Acceptance").strip()
    if len(password) < 12:
        raise RuntimeError("OPERLY_DEV_PASSWORD must contain at least 12 characters")

    async with session_scope() as db:
        user = await db.scalar(select(AppUser).where(AppUser.email == email))
        if user is None:
            user = AppUser(email=email, display_name="Acceptance Owner", password_hash=hash_password(password))
            db.add(user)
            await db.flush()
        else:
            user.password_hash = hash_password(password)
            user.active = True

        membership = await db.scalar(select(TenantMember).where(TenantMember.user_id == user.id))
        if membership is None:
            tenant = Tenant(name=tenant_name, slug=f"dev-{user.id[:8]}")
            db.add(tenant)
            await db.flush()
            db.add(TenantMember(tenant_id=tenant.id, user_id=user.id, role="owner"))

    print(f"Development account ready for {email}; password was not printed.")


if __name__ == "__main__":
    asyncio.run(main())
