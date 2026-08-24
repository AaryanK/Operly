"""Credential helpers for generated-app identities.

This module intentionally does not reuse Operly account sessions or token hashes.
Generated-app sessions are application-plane credentials with their own secret.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets

from cryptography.exceptions import InvalidKey
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

PASSWORD_MIN_BYTES = 12
PASSWORD_MAX_BYTES = 1024
_EMAIL_LOCAL = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+$")


class IdentityCredentialError(ValueError):
    pass


def normalize_email(value: str) -> str:
    if not isinstance(value, str):
        raise IdentityCredentialError("Enter a valid email address")
    email = value.strip()
    if not email or len(email) > 320 or "\x00" in email or email.count("@") != 1:
        raise IdentityCredentialError("Enter a valid email address")
    local, domain = email.rsplit("@", 1)
    if not local or len(local) > 64 or local.startswith(".") or local.endswith(".") or ".." in local:
        raise IdentityCredentialError("Enter a valid email address")
    if not _EMAIL_LOCAL.fullmatch(local):
        raise IdentityCredentialError("Enter a valid email address")
    try:
        domain = domain.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as error:
        raise IdentityCredentialError("Enter a valid email address") from error
    labels = domain.split(".")
    if (
        not domain
        or len(domain) > 253
        or "." not in domain
        or any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-") for label in labels)
        or any(not re.fullmatch(r"[a-z0-9-]+", label) for label in labels)
    ):
        raise IdentityCredentialError("Enter a valid email address")
    return f"{local.lower()}@{domain}"


def validate_password(password: str, *, email: str | None = None) -> None:
    if not isinstance(password, str) or "\x00" in password:
        raise IdentityCredentialError("Choose a valid password")
    size = len(password.encode("utf-8"))
    if size < PASSWORD_MIN_BYTES:
        raise IdentityCredentialError("Use at least 12 characters")
    if size > PASSWORD_MAX_BYTES:
        raise IdentityCredentialError("Password is too long")
    if len(set(password)) < 4:
        raise IdentityCredentialError("Choose a less predictable password")
    if email:
        local = email.split("@", 1)[0].casefold()
        if len(local) >= 4 and local in password.casefold():
            raise IdentityCredentialError("Password should not contain your email name")


def _argon2(salt: bytes) -> Argon2id:
    return Argon2id(salt=salt, length=32, iterations=2, lanes=1, memory_cost=19 * 1024)


def hash_password(password: str) -> str:
    return _argon2(secrets.token_bytes(16)).derive_phc_encoded(password.encode("utf-8"))


def verify_password(password: str, encoded: str) -> bool:
    if not encoded or len(password.encode("utf-8")) > PASSWORD_MAX_BYTES:
        return False
    try:
        Argon2id.verify_phc_encoded(password.encode("utf-8"), encoded)
        return True
    except (InvalidKey, ValueError):
        return False


def identity_secret(explicit: str | None = None) -> bytes:
    value = explicit if explicit is not None else os.getenv("OPERLY_APP_IDENTITY_SECRET", "").strip()
    environment = os.getenv("OPERLY_ENV", os.getenv("APP_ENV", "development")).lower()
    if not value and environment not in {"production", "prod"}:
        value = os.getenv("OPERLY_RUNTIME_BINDING_SECRET", "").strip()
    if len(value.encode("utf-8")) < 32:
        raise RuntimeError("OPERLY_APP_IDENTITY_SECRET must contain at least 32 bytes")
    return value.encode("utf-8")


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str, *, purpose: str, secret: str | None = None) -> str:
    return hmac.new(
        identity_secret(secret),
        f"{purpose}\x00{token}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


__all__ = [
    "IdentityCredentialError",
    "normalize_email",
    "validate_password",
    "hash_password",
    "verify_password",
    "new_token",
    "hash_token",
]
