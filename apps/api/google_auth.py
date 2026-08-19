import os
import hmac
import time
from dataclasses import dataclass
from typing import Any

from apps.api.security import normalize_email


class GoogleAuthenticationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class GoogleIdentityClaims:
    subject: str
    email: str
    display_name: str
    expires_at: int
    raw: dict[str, Any]


def verify_google_credential(credential: str, expected_nonce: str) -> GoogleIdentityClaims:
    client_id = os.getenv("GOOGLE_AUTH_CLIENT_ID", "").strip()
    if not client_id:
        raise GoogleAuthenticationError("Google sign-in is not configured")
    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token

        claims = id_token.verify_oauth2_token(
            credential,
            google_requests.Request(),
            client_id,
        )
    except Exception as error:
        raise GoogleAuthenticationError("Google could not confirm this sign-in") from error

    if claims.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}:
        raise GoogleAuthenticationError("Google could not confirm this sign-in")
    if claims.get("aud") != client_id:
        raise GoogleAuthenticationError("Google could not confirm this sign-in")
    if claims.get("email_verified") not in {True, "true"}:
        raise GoogleAuthenticationError("Google has not verified this email")
    returned_nonce = str(claims.get("nonce", ""))
    if not expected_nonce or not hmac.compare_digest(returned_nonce, expected_nonce):
        raise GoogleAuthenticationError("Google sign-in confirmation expired")
    subject = str(claims.get("sub", ""))
    if not subject or len(subject) > 255:
        raise GoogleAuthenticationError("Google could not confirm this sign-in")
    try:
        email = normalize_email(str(claims.get("email", "")))
    except ValueError as error:
        raise GoogleAuthenticationError("Google returned an unsupported email") from error
    display_name = str(claims.get("name") or email.split("@", 1)[0]).strip()[:200]
    if not display_name:
        display_name = "Owner"
    try:
        expires_at = int(claims["exp"])
    except (KeyError, TypeError, ValueError) as error:
        raise GoogleAuthenticationError("Google could not confirm this sign-in") from error
    if expires_at <= int(time.time()):
        raise GoogleAuthenticationError("Google sign-in confirmation expired")
    return GoogleIdentityClaims(subject, email, display_name, expires_at, dict(claims))
