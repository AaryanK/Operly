import asyncio
import hmac
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.auth_cookies import (
    GOOGLE_NONCE_COOKIE,
    SESSION_MAX_AGE,
    clear_auth_cookies,
    set_google_nonce_cookie,
    set_preauth_csrf_cookie,
    set_session_cookies,
)
from apps.api.dependencies import (
    AccountAuthContext,
    AuthContext,
    get_account_auth_context,
    get_auth_context,
    get_db,
)
from apps.api.google_auth import GoogleAuthenticationError, verify_google_credential
from apps.api.schemas import (
    ChallengeInput,
    ChangePasswordInput,
    ForgotPasswordInput,
    GoogleCredentialInput,
    LoginInput,
    ResendVerificationInput,
    ResetPasswordInput,
    SignupInput,
    WorkspaceSwitchInput,
)
from apps.api.security import (
    EmailAddressError,
    PasswordPolicyError,
    hash_password,
    hash_token,
    normalize_email,
    privacy_hash,
    random_code,
    random_token,
    validate_password,
    verify_and_update_password,
    verify_password,
)
from packages.database.db import SessionFactory
from packages.database.models import (
    AppUser,
    AuthChallenge,
    AuthIdentity,
    AuthRateLimitEvent,
    AuthSession,
    SecurityEvent,
    Tenant,
    TenantMember,
)
from packages.email.service import get_email_service


router = APIRouter(tags=["authentication"])
logger = logging.getLogger(__name__)

VERIFY_MINUTES = 30
RESET_MINUTES = 20
CHALLENGE_MAX_ATTEMPTS = 6
DUMMY_PASSWORD_HASH = hash_password("OPERLY timing defense placeholder")


def auth_error(status_code: int, code: str, message: str, *, retry_after: int | None = None) -> HTTPException:
    headers = {"Cache-Control": "no-store"}
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
        headers=headers,
    )


def _safe_name(value: str) -> str:
    name = " ".join(value.replace("\x00", "").split()).strip()
    if not name:
        raise auth_error(422, "INVALID_NAME", "Enter your name")
    return name[:200]


def _workspace_name(value: str) -> str:
    name = " ".join(str(value or "").replace("\x00", "").split()).strip()
    if not name:
        raise auth_error(422, "INVALID_WORKSPACE_NAME", "Enter a workspace name")
    return name[:200]


def _workspace_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:80]
    return slug or "workspace"


def _normalized_email(value: str) -> str:
    try:
        return normalize_email(value)
    except EmailAddressError as error:
        raise auth_error(422, "INVALID_EMAIL", str(error)) from error


def _existing_account_error(user: AppUser) -> HTTPException:
    if user.email_verified_at is None:
        return auth_error(
            409,
            "ACCOUNT_PENDING_VERIFICATION",
            "This account is waiting for email verification. Request a new code to continue.",
        )
    return auth_error(
        409,
        "ACCOUNT_EXISTS",
        "An account with this email already exists. Sign in or reset your password.",
    )


def _ip_hash(request: Request) -> str:
    value = request.client.host if request.client else "unknown"
    return privacy_hash(value, purpose="ip")


def _user_agent(request: Request) -> str | None:
    value = " ".join(request.headers.get("user-agent", "").replace("\x00", "").split())
    return value[:255] or None


def _audit(
    db: AsyncSession,
    event_type: str,
    outcome: str,
    request: Request,
    *,
    user_id: str | None = None,
    tenant_id: str | None = None,
    metadata: dict | None = None,
) -> None:
    db.add(
        SecurityEvent(
            user_id=user_id,
            tenant_id=tenant_id,
            event_type=event_type,
            outcome=outcome,
            ip_hash=_ip_hash(request),
            metadata_json=json.dumps(metadata or {}, separators=(",", ":"), sort_keys=True),
        )
    )


async def _rate_limit(
    db: AsyncSession,
    endpoint: str,
    request: Request,
    *,
    account: str | None = None,
    combined_limit: int = 8,
    ip_limit: int = 60,
    account_limit: int = 60,
    window_seconds: int = 60,
) -> None:
    now = datetime.utcnow()
    cutoff = now - timedelta(seconds=window_seconds)
    ip = _ip_hash(request)
    signals: list[tuple[str, int]] = [(f"ip:{ip}", ip_limit)]
    if account:
        account_hash = privacy_hash(account, purpose="rate-account")
        signals.extend(
            [
                (f"account:{account_hash}", account_limit),
                (f"combined:{account_hash}:{ip}", combined_limit),
            ]
        )
    hashes = [(privacy_hash(signal, purpose=f"rate:{endpoint}"), limit) for signal, limit in signals]
    db.add_all(
        AuthRateLimitEvent(endpoint=endpoint, key_hash=key_hash, created_at=now)
        for key_hash, _ in hashes
    )
    await db.execute(
        delete(AuthRateLimitEvent).where(
            AuthRateLimitEvent.created_at < now - timedelta(days=1)
        )
    )
    await db.commit()
    exceeded = False
    for key_hash, limit in hashes:
        count = await db.scalar(
            select(func.count(AuthRateLimitEvent.id)).where(
                AuthRateLimitEvent.endpoint == endpoint,
                AuthRateLimitEvent.key_hash == key_hash,
                AuthRateLimitEvent.created_at >= cutoff,
            )
        )
        exceeded = exceeded or int(count or 0) > limit
    if exceeded:
        _audit(db, "rate_limit_triggered", "blocked", request, metadata={"endpoint": endpoint})
        await db.commit()
        raise auth_error(
            429,
            "RATE_LIMITED",
            "Too many attempts. Please wait and try again.",
            retry_after=window_seconds,
        )


async def _first_membership(db: AsyncSession, user_id: str) -> TenantMember | None:
    return await db.scalar(
        select(TenantMember)
        .where(TenantMember.user_id == user_id)
        .order_by(TenantMember.created_at, TenantMember.id)
    )


async def _new_challenge(
    db: AsyncSession,
    user: AppUser,
    purpose: str,
    minutes: int,
) -> tuple[AuthChallenge, str, str]:
    now = datetime.utcnow()
    await db.execute(
        update(AuthChallenge)
        .where(
            AuthChallenge.user_id == user.id,
            AuthChallenge.purpose == purpose,
            AuthChallenge.consumed_at.is_(None),
        )
        .values(consumed_at=now)
    )
    token = random_token()
    code = random_code()
    challenge = AuthChallenge(
        purpose=purpose,
        user_id=user.id,
        target_email=user.email,
        secret_hash=hash_token(token, purpose=f"challenge:{purpose}:link"),
        code_hash=hash_token(code, purpose=f"challenge:{purpose}:code"),
        expires_at=now + timedelta(minutes=minutes),
        max_attempts=CHALLENGE_MAX_ATTEMPTS,
        created_at=now,
    )
    db.add(challenge)
    await db.flush()
    return challenge, token, code


async def _deliver_verification(
    db: AsyncSession,
    challenge: AuthChallenge,
    user: AppUser,
    token: str,
    code: str,
) -> bool:
    base_url = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
    try:
        await get_email_service().send_email_verification(
            to_email=user.email,
            display_name=user.display_name,
            code=code,
            verify_url=f"{base_url}/verify-email#token={token}",
            minutes=VERIFY_MINUTES,
        )
    except Exception as error:
        logger.warning("Verification email was not delivered (provider_error=%s)", type(error).__name__)
        challenge.delivery_status = "failed"
        await db.commit()
        return False
    challenge.delivery_status = "delivered"
    challenge.delivered_at = datetime.utcnow()
    await db.commit()
    return True


async def _deliver_reset_by_id(
    challenge_id: str,
    user_id: str,
    token: str,
    code: str,
) -> None:
    async with SessionFactory() as db:
        challenge = await db.get(AuthChallenge, challenge_id)
        user = await db.get(AppUser, user_id)
        if not challenge or not user or challenge.consumed_at is not None:
            return
        base_url = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
        try:
            await get_email_service().send_password_reset(
                to_email=user.email,
                display_name=user.display_name,
                code=code,
                reset_url=f"{base_url}/reset-password#token={token}",
                minutes=RESET_MINUTES,
            )
        except Exception as error:
            logger.warning("Password reset email was not delivered (provider_error=%s)", type(error).__name__)
            challenge.delivery_status = "failed"
        else:
            challenge.delivery_status = "delivered"
            challenge.delivered_at = datetime.utcnow()
        await db.commit()


async def _send_welcome(db: AsyncSession, user: AppUser, request: Request, tenant_id: str | None) -> None:
    base_url = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
    try:
        await get_email_service().send_welcome(
            to_email=user.email,
            display_name=user.display_name,
            app_url=base_url,
        )
    except Exception as error:
        logger.warning("Welcome email was not delivered (provider_error=%s)", type(error).__name__)
        _audit(db, "transactional_email_failed", "failed", request, user_id=user.id, tenant_id=tenant_id, metadata={"message": "welcome"})
        await db.commit()


async def _send_password_changed(db: AsyncSession, user: AppUser, request: Request, tenant_id: str | None) -> None:
    base_url = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
    try:
        await get_email_service().send_password_changed(
            to_email=user.email,
            display_name=user.display_name,
            app_url=base_url,
        )
    except Exception as error:
        logger.warning("Password-change email was not delivered (provider_error=%s)", type(error).__name__)
        _audit(db, "transactional_email_failed", "failed", request, user_id=user.id, tenant_id=tenant_id, metadata={"message": "password_changed"})
        await db.commit()


async def _create_session(
    db: AsyncSession,
    request: Request,
    user_id: str,
    tenant_id: str | None,
) -> tuple[AuthSession, str, str]:
    now = datetime.utcnow()
    secret = random_token()
    csrf = random_token()
    auth_session = AuthSession(
        token_hash=hash_token(secret, purpose="session"),
        csrf_token_hash=hash_token(csrf, purpose="csrf"),
        user_id=user_id,
        tenant_id=tenant_id,
        created_at=now,
        last_activity_at=now,
        authenticated_at=now,
        expires_at=now + timedelta(seconds=SESSION_MAX_AGE),
        user_agent=_user_agent(request),
        ip_hash=_ip_hash(request),
    )
    db.add(auth_session)
    await db.flush()
    return auth_session, secret, csrf


async def _challenge_from_proof(
    db: AsyncSession,
    payload: ChallengeInput,
    purpose: str,
) -> AuthChallenge | None:
    if payload.token:
        return await db.scalar(
            select(AuthChallenge).where(
                AuthChallenge.purpose == purpose,
                AuthChallenge.secret_hash == hash_token(payload.token, purpose=f"challenge:{purpose}:link"),
            )
        )
    if payload.challenge_id:
        challenge = await db.get(AuthChallenge, payload.challenge_id)
        if challenge and challenge.purpose == purpose:
            return challenge
    if payload.email and payload.code:
        try:
            target_email = normalize_email(payload.email)
        except EmailAddressError:
            return None
        return await db.scalar(
            select(AuthChallenge)
            .where(
                AuthChallenge.purpose == purpose,
                AuthChallenge.target_email == target_email,
                AuthChallenge.consumed_at.is_(None),
            )
            .order_by(AuthChallenge.created_at.desc())
        )
    return None


async def _validate_challenge(
    db: AsyncSession,
    payload: ChallengeInput,
    purpose: str,
    request: Request,
) -> AuthChallenge:
    challenge = await _challenge_from_proof(db, payload, purpose)
    if challenge is None:
        raise auth_error(400, "INVALID_CHALLENGE", "That code or link is not valid")
    now = datetime.utcnow()
    if challenge.consumed_at is not None:
        _audit(db, "challenge_replay", "blocked", request, user_id=challenge.user_id, metadata={"purpose": purpose})
        await db.commit()
        raise auth_error(409, "CHALLENGE_ALREADY_USED", "That code or link has already been used")
    if challenge.expires_at <= now:
        _audit(db, "challenge_expired", "blocked", request, user_id=challenge.user_id, metadata={"purpose": purpose})
        await db.commit()
        raise auth_error(410, "CHALLENGE_EXPIRED", "That code or link has expired")
    if challenge.attempt_count >= challenge.max_attempts:
        raise auth_error(429, "CHALLENGE_LOCKED", "Too many incorrect attempts. Request a new code.")
    if payload.code:
        actual = hash_token(payload.code, purpose=f"challenge:{purpose}:code")
        if not challenge.code_hash or not hmac.compare_digest(actual, challenge.code_hash):
            challenge.attempt_count += 1
            if challenge.attempt_count >= challenge.max_attempts:
                challenge.consumed_at = now
            _audit(db, "challenge_failed", "failed", request, user_id=challenge.user_id, metadata={"purpose": purpose})
            await db.commit()
            raise auth_error(400, "INVALID_CHALLENGE", "That code or link is not valid")
    result = await db.execute(
        update(AuthChallenge)
        .where(AuthChallenge.id == challenge.id, AuthChallenge.consumed_at.is_(None))
        .values(consumed_at=now)
    )
    if result.rowcount != 1:
        await db.rollback()
        raise auth_error(409, "CHALLENGE_ALREADY_USED", "That code or link has already been used")
    return challenge


@router.get("/api/auth/bootstrap")
async def auth_bootstrap(response: Response):
    csrf = random_token()
    nonce = random_token()
    set_preauth_csrf_cookie(response, csrf)
    set_google_nonce_cookie(response, nonce)
    response.headers["Cache-Control"] = "no-store"
    return {
        "csrf_token": csrf,
        "google_nonce": nonce,
        "google_client_id": os.getenv("GOOGLE_AUTH_CLIENT_ID", "").strip() or None,
    }


@router.post("/api/auth/signup", status_code=201)
async def signup(payload: SignupInput, request: Request, db: AsyncSession = Depends(get_db)):
    email = _normalized_email(payload.email)
    display_name = _safe_name(payload.display_name)
    try:
        validate_password(payload.password, email=email)
    except PasswordPolicyError as error:
        raise auth_error(422, "WEAK_PASSWORD", str(error)) from error
    await _rate_limit(db, "signup", request, account=email, combined_limit=4, ip_limit=20, account_limit=12, window_seconds=600)
    existing_user = await db.scalar(select(AppUser).where(AppUser.email == email))
    if existing_user:
        raise _existing_account_error(existing_user)

    user = AppUser(
        email=email,
        display_name=display_name,
        password_hash=hash_password(payload.password),
        active=True,
    )
    db.add(user)
    try:
        await db.flush()
        db.add(AuthIdentity(user_id=user.id, provider="password", provider_subject=email, provider_email=email))
        challenge, token, code = await _new_challenge(db, user, "email_verification", VERIFY_MINUTES)
        _audit(db, "signup_requested", "succeeded", request, user_id=user.id)
        await db.commit()
    except IntegrityError as error:
        await db.rollback()
        existing_user = await db.scalar(select(AppUser).where(AppUser.email == email))
        if existing_user:
            raise _existing_account_error(existing_user) from error
        raise auth_error(409, "ACCOUNT_EXISTS", "An account with this email already exists. Sign in or reset your password.") from error

    if not await _deliver_verification(db, challenge, user, token, code):
        raise auth_error(503, "EMAIL_DELIVERY_FAILED", "Your account was created, but we could not send the verification email. Please try resend.")
    return {
        "ok": True,
        "requires_verification": True,
        "challenge_id": challenge.id,
        "email": email,
    }


@router.post("/api/auth/resend-verification")
async def resend_verification(payload: ResendVerificationInput, request: Request, db: AsyncSession = Depends(get_db)):
    email = _normalized_email(payload.email)
    await _rate_limit(db, "resend_verification", request, account=email, combined_limit=3, ip_limit=20, account_limit=6, window_seconds=600)
    user = await db.scalar(select(AppUser).where(AppUser.email == email))
    if not user or user.email_verified_at is not None or not user.active:
        return {"ok": True, "message": "If verification is still needed, a new message will arrive shortly."}
    challenge, token, code = await _new_challenge(db, user, "email_verification", VERIFY_MINUTES)
    await db.commit()
    if not await _deliver_verification(db, challenge, user, token, code):
        raise auth_error(503, "EMAIL_DELIVERY_FAILED", "We could not send a new verification email. Please try again shortly.")
    return {"ok": True, "challenge_id": challenge.id, "message": "A new verification email is on its way."}


@router.post("/api/auth/verify-email")
async def verify_email(payload: ChallengeInput, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    account_signal = (
        payload.challenge_id
        or (_normalized_email(payload.email) if payload.email else None)
        or privacy_hash(payload.token or "", purpose="verify-token")
    )
    await _rate_limit(db, "verify_email", request, account=account_signal, combined_limit=8, ip_limit=50, account_limit=12, window_seconds=600)
    challenge = await _validate_challenge(db, payload, "email_verification", request)
    user = await db.get(AppUser, challenge.user_id)
    if not user or not user.active:
        await db.rollback()
        raise auth_error(400, "INVALID_CHALLENGE", "That code or link is not valid")
    membership = await _first_membership(db, user.id)
    tenant_id = membership.tenant_id if membership else None
    now = datetime.utcnow()
    user.email_verified_at = user.email_verified_at or now
    user.updated_at = now
    await db.execute(
        update(AuthChallenge)
        .where(
            AuthChallenge.user_id == user.id,
            AuthChallenge.purpose == "email_verification",
            AuthChallenge.consumed_at.is_(None),
        )
        .values(consumed_at=now)
    )
    _, session_secret, csrf_secret = await _create_session(db, request, user.id, tenant_id)
    _audit(db, "email_verified", "succeeded", request, user_id=user.id, tenant_id=tenant_id)
    _audit(db, "signup_completed", "succeeded", request, user_id=user.id, tenant_id=tenant_id)
    await db.commit()
    set_session_cookies(response, session_secret, csrf_secret)
    await _send_welcome(db, user, request, tenant_id)
    return {"ok": True, "next": "/", "scope": "workspace" if tenant_id else "personal"}


@router.post("/api/auth/login")
@router.post("/api/session/login", include_in_schema=False)
async def login(payload: LoginInput, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    email = _normalized_email(payload.email)
    await _rate_limit(db, "login", request, account=email, combined_limit=8, ip_limit=60, account_limit=60)
    user = await db.scalar(select(AppUser).where(AppUser.email == email))
    verification = verify_and_update_password(payload.password, user.password_hash if user else DUMMY_PASSWORD_HASH)
    if user is None or not user.active or not verification.valid:
        _audit(db, "login_failure", "failed", request, user_id=user.id if user else None)
        await db.commit()
        raise auth_error(401, "INVALID_CREDENTIALS", "Invalid email or password")
    if user.email_verified_at is None:
        _audit(db, "login_failure", "blocked", request, user_id=user.id, metadata={"reason": "unverified"})
        await db.commit()
        raise auth_error(403, "EMAIL_NOT_VERIFIED", "Verify your email before signing in")
    membership = await _first_membership(db, user.id)
    tenant_id = membership.tenant_id if membership else None
    if verification.upgraded_hash:
        user.password_hash = verification.upgraded_hash
        user.updated_at = datetime.utcnow()
    _, session_secret, csrf_secret = await _create_session(db, request, user.id, tenant_id)
    _audit(db, "login_success", "succeeded", request, user_id=user.id, tenant_id=tenant_id, metadata={"provider": "password", "scope": "workspace" if tenant_id else "personal"})
    await db.commit()
    set_session_cookies(response, session_secret, csrf_secret)
    return {"ok": True, "scope": "workspace" if tenant_id else "personal"}


@router.post("/api/auth/google")
async def google_login(payload: GoogleCredentialInput, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    await _rate_limit(db, "google", request, combined_limit=30, ip_limit=30)
    nonce = request.cookies.get(GOOGLE_NONCE_COOKIE, "")
    try:
        claims = verify_google_credential(payload.credential, nonce)
    except GoogleAuthenticationError as error:
        _audit(db, "google_authentication_failure", "failed", request)
        await db.commit()
        raise auth_error(401, "GOOGLE_AUTHENTICATION_FAILED", str(error)) from error

    replay_hash = hash_token(payload.credential, purpose="google-replay")
    if await db.scalar(select(AuthChallenge.id).where(AuthChallenge.secret_hash == replay_hash)):
        _audit(db, "google_token_replay", "blocked", request)
        await db.commit()
        raise auth_error(409, "GOOGLE_CREDENTIAL_REPLAYED", "This Google sign-in has already been used")

    identity = await db.scalar(
        select(AuthIdentity).where(
            AuthIdentity.provider == "google",
            AuthIdentity.provider_subject == claims.subject,
        )
    )
    new_account = False
    if identity:
        user = await db.get(AppUser, identity.user_id)
    else:
        user = await db.scalar(select(AppUser).where(AppUser.email == claims.email))
        if user:
            if user.email_verified_at is None:
                _audit(db, "google_account_link_blocked", "blocked", request, user_id=user.id)
                await db.commit()
                raise auth_error(409, "ACCOUNT_LINK_REQUIRES_VERIFICATION", "Verify your OPERLY email before linking Google")
            db.add(
                AuthIdentity(
                    user_id=user.id,
                    provider="google",
                    provider_subject=claims.subject,
                    provider_email=claims.email,
                )
            )
        else:
            new_account = True
            user = AppUser(
                email=claims.email,
                display_name=claims.display_name,
                password_hash=None,
                email_verified_at=datetime.utcnow(),
                active=True,
            )
            db.add(user)
            await db.flush()
            db.add(
                AuthIdentity(
                    user_id=user.id,
                    provider="google",
                    provider_subject=claims.subject,
                    provider_email=claims.email,
                )
            )
    if not user or not user.active:
        raise auth_error(401, "GOOGLE_AUTHENTICATION_FAILED", "Google could not confirm this sign-in")
    membership = await _first_membership(db, user.id)
    tenant_id = membership.tenant_id if membership else None
    db.add(
        AuthChallenge(
            purpose="google_replay",
            user_id=user.id,
            target_email=user.email,
            secret_hash=replay_hash,
            code_hash=None,
            expires_at=datetime.utcfromtimestamp(claims.expires_at),
            consumed_at=datetime.utcnow(),
            attempt_count=0,
            max_attempts=1,
            delivery_status="not_applicable",
        )
    )
    _, session_secret, csrf_secret = await _create_session(db, request, user.id, tenant_id)
    _audit(db, "google_authentication_success", "succeeded", request, user_id=user.id, tenant_id=tenant_id, metadata={"new_account": new_account, "scope": "workspace" if tenant_id else "personal"})
    if new_account:
        _audit(db, "signup_completed", "succeeded", request, user_id=user.id, metadata={"provider": "google", "scope": "personal"})
    try:
        await db.commit()
    except IntegrityError as error:
        await db.rollback()
        raise auth_error(409, "GOOGLE_SIGN_IN_CONFLICT", "This Google sign-in is already being completed. Please try again.") from error
    set_session_cookies(response, session_secret, csrf_secret)
    if new_account:
        await _send_welcome(db, user, request, None)
    return {"ok": True, "new_account": new_account, "next": "/", "scope": "workspace" if tenant_id else "personal"}


@router.post("/api/auth/forgot-password", status_code=202)
async def forgot_password(payload: ForgotPasswordInput, request: Request, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    started = time.monotonic()
    try:
        email = normalize_email(payload.email)
    except EmailAddressError:
        email = "invalid@invalid.example"
    await _rate_limit(db, "forgot_password", request, account=email, combined_limit=4, ip_limit=30, account_limit=30, window_seconds=600)
    user = await db.scalar(select(AppUser).where(AppUser.email == email))
    if user and user.active and user.email_verified_at is not None:
        challenge, token, code = await _new_challenge(db, user, "password_reset", RESET_MINUTES)
        _audit(db, "password_reset_requested", "succeeded", request, user_id=user.id)
        await db.commit()
        background_tasks.add_task(_deliver_reset_by_id, challenge.id, user.id, token, code)
    else:
        verify_password("not-the-password", DUMMY_PASSWORD_HASH)
    elapsed = time.monotonic() - started
    if elapsed < 0.075:
        await asyncio.sleep(0.075 - elapsed)
    return {"ok": True, "message": "If an account matches that email, password reset instructions will arrive shortly."}


@router.post("/api/auth/reset-password")
async def reset_password(payload: ResetPasswordInput, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    account_signal = (
        payload.challenge_id
        or (_normalized_email(payload.email) if payload.email else None)
        or privacy_hash(payload.token or "", purpose="reset-token")
    )
    await _rate_limit(db, "reset_password", request, account=account_signal, combined_limit=8, ip_limit=50, account_limit=12, window_seconds=600)
    challenge = await _validate_challenge(db, payload, "password_reset", request)
    user = await db.get(AppUser, challenge.user_id)
    if not user or not user.active:
        await db.rollback()
        raise auth_error(400, "INVALID_CHALLENGE", "That code or link is not valid")
    try:
        validate_password(payload.password, email=user.email)
    except PasswordPolicyError as error:
        await db.rollback()
        raise auth_error(422, "WEAK_PASSWORD", str(error)) from error
    membership = await _first_membership(db, user.id)
    tenant_id = membership.tenant_id if membership else None
    now = datetime.utcnow()
    user.password_hash = hash_password(payload.password)
    user.updated_at = now
    password_identity = await db.scalar(
        select(AuthIdentity).where(AuthIdentity.user_id == user.id, AuthIdentity.provider == "password")
    )
    if not password_identity:
        db.add(AuthIdentity(user_id=user.id, provider="password", provider_subject=user.email, provider_email=user.email))
    await db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    _, session_secret, csrf_secret = await _create_session(db, request, user.id, tenant_id)
    _audit(db, "password_reset_completed", "succeeded", request, user_id=user.id, tenant_id=tenant_id)
    await db.commit()
    set_session_cookies(response, session_secret, csrf_secret)
    await _send_password_changed(db, user, request, tenant_id)
    return {"ok": True, "scope": "workspace" if tenant_id else "personal"}


@router.post("/api/auth/change-password")
async def change_password(
    payload: ChangePasswordInput,
    request: Request,
    response: Response,
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await _rate_limit(db, "change_password", request, account=auth.user.id, combined_limit=6, ip_limit=30, account_limit=12, window_seconds=600)
    user = await db.get(AppUser, auth.user.id)
    if not user:
        raise auth_error(401, "SESSION_INVALID", "Your session is no longer valid")
    if user.password_hash:
        verification = verify_and_update_password(payload.current_password or "", user.password_hash)
        if not verification.valid:
            _audit(db, "password_change_failed", "failed", request, user_id=user.id, tenant_id=auth.session.tenant_id)
            await db.commit()
            raise auth_error(401, "CURRENT_PASSWORD_INCORRECT", "Current password is incorrect")
    elif auth.session.authenticated_at < datetime.utcnow() - timedelta(minutes=10):
        raise auth_error(401, "RECENT_SIGN_IN_REQUIRED", "Sign in again before setting a password")
    try:
        validate_password(payload.new_password, email=user.email)
    except PasswordPolicyError as error:
        raise auth_error(422, "WEAK_PASSWORD", str(error)) from error
    now = datetime.utcnow()
    user.password_hash = hash_password(payload.new_password)
    user.updated_at = now
    identity = await db.scalar(select(AuthIdentity).where(AuthIdentity.user_id == user.id, AuthIdentity.provider == "password"))
    if not identity:
        db.add(AuthIdentity(user_id=user.id, provider="password", provider_subject=user.email, provider_email=user.email))
    current_tenant_id = auth.session.tenant_id
    if current_tenant_id:
        membership = await db.scalar(
            select(TenantMember).where(
                TenantMember.user_id == user.id,
                TenantMember.tenant_id == current_tenant_id,
            )
        )
        if membership is None:
            current_tenant_id = None
    await db.execute(update(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None)).values(revoked_at=now))
    _, session_secret, csrf_secret = await _create_session(db, request, user.id, current_tenant_id)
    _audit(db, "password_changed", "succeeded", request, user_id=user.id, tenant_id=current_tenant_id)
    await db.commit()
    set_session_cookies(response, session_secret, csrf_secret)
    await _send_password_changed(db, user, request, current_tenant_id)
    return {"ok": True}


@router.get("/api/auth/sessions")
async def sessions(auth: AccountAuthContext = Depends(get_account_auth_context), db: AsyncSession = Depends(get_db)):
    now = datetime.utcnow()
    rows = (
        await db.scalars(
            select(AuthSession)
            .where(
                AuthSession.user_id == auth.user.id,
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > now,
            )
            .order_by(AuthSession.last_activity_at.desc())
        )
    ).all()
    return [
        {
            "id": row.id,
            "current": row.id == auth.session.id,
            "scope": "workspace" if row.tenant_id else "personal",
            "tenant_id": row.tenant_id,
            "created_at": row.created_at,
            "last_activity_at": row.last_activity_at,
            "expires_at": row.expires_at,
            "device": row.user_agent or "Unknown device",
        }
        for row in rows
    ]


@router.post("/api/auth/logout")
@router.post("/api/session/logout", include_in_schema=False)
async def logout(
    response: Response,
    request: Request,
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    auth_session = await db.get(AuthSession, auth.session.id)
    if not auth_session or auth_session.revoked_at is not None:
        raise auth_error(409, "SESSION_ALREADY_REVOKED", "This session is already signed out")
    auth_session.revoked_at = datetime.utcnow()
    _audit(db, "logout", "succeeded", request, user_id=auth.user.id, tenant_id=auth.session.tenant_id)
    await db.commit()
    clear_auth_cookies(response)
    return {"ok": True}


@router.post("/api/auth/logout-all")
async def logout_all(
    response: Response,
    request: Request,
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    now = datetime.utcnow()
    result = await db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == auth.user.id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    _audit(db, "logout_all", "succeeded", request, user_id=auth.user.id, tenant_id=auth.session.tenant_id, metadata={"revoked_count": result.rowcount})
    await db.commit()
    clear_auth_cookies(response)
    return {"ok": True, "revoked": result.rowcount}


@router.get("/api/auth/workspaces")
@router.get("/api/session/workspaces", include_in_schema=False)
async def workspaces(
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            select(Tenant, TenantMember.role)
            .join(TenantMember, TenantMember.tenant_id == Tenant.id)
            .where(TenantMember.user_id == auth.user.id)
            .order_by(Tenant.name)
        )
    ).all()
    return [
        {
            "id": tenant.id,
            "name": tenant.name,
            "role": role,
            "current": tenant.id == auth.session.tenant_id,
        }
        for tenant, role in rows
    ]


@router.post("/api/auth/workspaces", status_code=201)
async def create_workspace(
    payload: dict,
    request: Request,
    response: Response,
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    name = _workspace_name(str(payload.get("name") or ""))
    base_slug = _workspace_slug(name)
    slug = base_slug
    suffix = 2
    while await db.scalar(select(Tenant.id).where(Tenant.slug == slug)):
        slug = f"{base_slug[:72]}-{suffix}"
        suffix += 1
    tenant = Tenant(name=name, slug=slug)
    db.add(tenant)
    await db.flush()
    db.add(TenantMember(tenant_id=tenant.id, user_id=auth.user.id, role="owner"))
    current = await db.get(AuthSession, auth.session.id)
    if current:
        current.revoked_at = datetime.utcnow()
    _, session_secret, csrf_secret = await _create_session(db, request, auth.user.id, tenant.id)
    _audit(db, "workspace_created", "succeeded", request, user_id=auth.user.id, tenant_id=tenant.id)
    await db.commit()
    set_session_cookies(response, session_secret, csrf_secret)
    return {"ok": True, "workspace": {"id": tenant.id, "name": tenant.name, "slug": tenant.slug, "role": "owner"}}


@router.post("/api/auth/personal-scope")
async def switch_to_personal_scope(
    request: Request,
    response: Response,
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    current = await db.get(AuthSession, auth.session.id)
    if current:
        current.revoked_at = datetime.utcnow()
    _, session_secret, csrf_secret = await _create_session(db, request, auth.user.id, None)
    _audit(db, "personal_scope_selected", "succeeded", request, user_id=auth.user.id)
    await db.commit()
    set_session_cookies(response, session_secret, csrf_secret)
    return {"ok": True, "scope": "personal"}


@router.post("/api/auth/switch-workspace")
@router.post("/api/session/switch-workspace", include_in_schema=False)
async def switch_workspace(
    payload: WorkspaceSwitchInput,
    request: Request,
    response: Response,
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    membership = await db.scalar(
        select(TenantMember).where(
            TenantMember.user_id == auth.user.id,
            TenantMember.tenant_id == payload.tenant_id,
        )
    )
    tenant = await db.get(Tenant, payload.tenant_id) if membership else None
    if not membership or not tenant or not auth.user.active:
        raise auth_error(404, "WORKSPACE_NOT_FOUND", "Workspace not found")
    current = await db.get(AuthSession, auth.session.id)
    if current:
        current.revoked_at = datetime.utcnow()
    _, session_secret, csrf_secret = await _create_session(db, request, auth.user.id, tenant.id)
    _audit(db, "workspace_switched", "succeeded", request, user_id=auth.user.id, tenant_id=tenant.id)
    await db.commit()
    set_session_cookies(response, session_secret, csrf_secret)
    return {"ok": True, "workspace": {"id": tenant.id, "name": tenant.name}}
