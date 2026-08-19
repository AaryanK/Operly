import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.security import hash_token, random_token
from packages.channels.identity import IdentityService
from packages.database.channel_models import IdentityLinkChallenge


LINK_TTL_MINUTES = 10


@dataclass(slots=True)
class PairingChallenge:
    challenge_id: str
    provider: str
    mode: str
    token: str | None = None
    code: str | None = None
    expires_at: datetime | None = None


class IdentityLinkService:
    @staticmethod
    def _token_hash(secret: str) -> str:
        return hash_token(secret, purpose="external-identity-link")

    @staticmethod
    def _code_hash(code: str) -> str:
        return hash_token(code.upper(), purpose="external-identity-code")

    @classmethod
    async def create_from_operly(
        cls,
        db: AsyncSession,
        *,
        user_id: str,
        provider: str,
    ) -> PairingChallenge:
        token = random_token()
        code = ""
        for _ in range(10):
            candidate = secrets.token_hex(4).upper()
            existing = await db.scalar(
                select(IdentityLinkChallenge.id).where(
                    IdentityLinkChallenge.code_hash == cls._code_hash(candidate),
                    IdentityLinkChallenge.consumed_at.is_(None),
                    IdentityLinkChallenge.expires_at > datetime.utcnow(),
                )
            )
            if not existing:
                code = candidate
                break
        if not code:
            raise RuntimeError("Could not allocate a unique identity pairing code")
        expires = datetime.utcnow() + timedelta(minutes=LINK_TTL_MINUTES)
        row = IdentityLinkChallenge(
            provider=provider,
            mode="from_operly",
            user_id=user_id,
            secret_hash=cls._token_hash(token),
            code_hash=cls._code_hash(code),
            expires_at=expires,
        )
        db.add(row)
        await db.flush()
        return PairingChallenge(
            challenge_id=row.id,
            provider=provider,
            mode=row.mode,
            code=code,
            expires_at=expires,
        )

    @classmethod
    async def create_from_channel(
        cls,
        db: AsyncSession,
        *,
        provider: str,
        external_user_id: str,
        display_name: str | None = None,
    ) -> PairingChallenge:
        token = random_token()
        expires = datetime.utcnow() + timedelta(minutes=LINK_TTL_MINUTES)
        row = IdentityLinkChallenge(
            provider=provider,
            mode="from_channel",
            external_user_id=str(external_user_id),
            display_name=display_name,
            secret_hash=cls._token_hash(token),
            expires_at=expires,
        )
        db.add(row)
        await db.flush()
        return PairingChallenge(
            challenge_id=row.id,
            provider=provider,
            mode=row.mode,
            token=token,
            expires_at=expires,
        )

    @classmethod
    async def _challenge_by_token(
        cls,
        db: AsyncSession,
        *,
        provider: str,
        token: str,
        mode: str,
    ) -> IdentityLinkChallenge | None:
        return await db.scalar(
            select(IdentityLinkChallenge).where(
                IdentityLinkChallenge.provider == provider,
                IdentityLinkChallenge.mode == mode,
                IdentityLinkChallenge.secret_hash == cls._token_hash(token),
                IdentityLinkChallenge.consumed_at.is_(None),
                IdentityLinkChallenge.expires_at > datetime.utcnow(),
            )
        )

    @classmethod
    async def inspect_channel_token(
        cls,
        db: AsyncSession,
        *,
        provider: str,
        token: str,
    ) -> dict | None:
        row = await cls._challenge_by_token(
            db,
            provider=provider,
            token=token,
            mode="from_channel",
        )
        if not row:
            return None
        return {
            "challenge_id": row.id,
            "provider": row.provider,
            "display_name": row.display_name,
            "expires_at": row.expires_at,
        }

    @classmethod
    async def claim_from_web(
        cls,
        db: AsyncSession,
        *,
        user_id: str,
        provider: str,
        token: str,
    ):
        row = await cls._challenge_by_token(
            db,
            provider=provider,
            token=token,
            mode="from_channel",
        )
        if not row or not row.external_user_id:
            raise ValueError("Identity link is invalid or expired")
        identity = await IdentityService.link_external_identity(
            db,
            user_id=user_id,
            provider=provider,
            external_user_id=row.external_user_id,
            display_name=row.display_name,
            metadata={"linked_via": "channel_to_operly"},
        )
        row.user_id = user_id
        row.consumed_at = datetime.utcnow()
        await db.flush()
        return identity

    @classmethod
    async def claim_from_channel(
        cls,
        db: AsyncSession,
        *,
        provider: str,
        external_user_id: str,
        code: str,
        display_name: str | None = None,
    ):
        row = await db.scalar(
            select(IdentityLinkChallenge).where(
                IdentityLinkChallenge.provider == provider,
                IdentityLinkChallenge.mode == "from_operly",
                IdentityLinkChallenge.code_hash == cls._code_hash(code),
                IdentityLinkChallenge.consumed_at.is_(None),
                IdentityLinkChallenge.expires_at > datetime.utcnow(),
            )
        )
        if not row or not row.user_id:
            raise ValueError("Pairing code is invalid or expired")
        identity = await IdentityService.link_external_identity(
            db,
            user_id=row.user_id,
            provider=provider,
            external_user_id=str(external_user_id),
            display_name=display_name,
            metadata={"linked_via": "operly_to_channel"},
        )
        row.external_user_id = str(external_user_id)
        row.display_name = display_name
        row.consumed_at = datetime.utcnow()
        await db.flush()
        return identity
