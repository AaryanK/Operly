import base64
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

ITERATIONS = 220_000
TOKEN_MAX_AGE_SECONDS = 60 * 60 * 24 * 7


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        ITERATIONS,
    )
    return (
        f"pbkdf2_sha256${ITERATIONS}$"
        f"{base64.urlsafe_b64encode(salt).decode()}$"
        f"{base64.urlsafe_b64encode(derived).decode()}"
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt_text, hash_text = stored.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False

        salt = base64.urlsafe_b64decode(salt_text.encode())
        expected = base64.urlsafe_b64decode(hash_text.encode())
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            int(iterations),
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def _serializer() -> URLSafeTimedSerializer:
    secret = os.getenv("SESSION_SECRET")
    if not secret:
        raise RuntimeError("SESSION_SECRET is missing")
    return URLSafeTimedSerializer(secret, salt="operly-session-v1")


def create_token(user_id: str, tenant_id: str, role: str) -> str:
    return _serializer().dumps(
        {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "role": role,
        }
    )


def decode_token(token: str) -> dict:
    try:
        return _serializer().loads(
            token,
            max_age=TOKEN_MAX_AGE_SECONDS,
        )
    except SignatureExpired as error:
        raise ValueError("Session expired") from error
    except BadSignature as error:
        raise ValueError("Invalid session") from error
