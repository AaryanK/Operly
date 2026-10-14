"""Strict OIDC/PCKE validation for the future DragonZpyder identity issuer.

No browser cookies, access tokens, or email matches establish an Operly
identity by themselves. Operly trusts (issuer, subject) only after verification.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import urlparse

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

ISSUER = "https://auth.dragonzpyder.xyz"
AUTHORIZE_URL = ISSUER + "/oauth/authorize"
TOKEN_URL = ISSUER + "/oauth/token"
JWKS_URL = ISSUER + "/.well-known/jwks.json"


class DragonZpyderIdentityError(ValueError):
    pass


def _decode(data: str) -> bytes:
    if not isinstance(data, str) or len(data) > 30000:
        raise DragonZpyderIdentityError("Malformed identity token")
    try:
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    except Exception as exc:
        raise DragonZpyderIdentityError("Malformed identity token") from exc


def create_pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


def validate_issuer_origin(origin: str) -> None:
    parsed = urlparse(origin)
    if parsed.scheme != "https" or parsed.netloc != "auth.dragonzpyder.xyz" or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise DragonZpyderIdentityError("Untrusted identity issuer")


def verified_id_token(token: str, jwks: dict, *, audience: str, nonce: str, now: int | None = None) -> dict:
    """Verify RSA signature and the required OIDC claims. Never identify by email."""
    if not isinstance(token, str) or len(token) > 18000:
        raise DragonZpyderIdentityError("Invalid identity token")
    pieces = token.split(".")
    if len(pieces) != 3:
        raise DragonZpyderIdentityError("Malformed identity token")
    try:
        header = json.loads(_decode(pieces[0]))
        claims = json.loads(_decode(pieces[1]))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise DragonZpyderIdentityError("Invalid token structure") from exc
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise DragonZpyderIdentityError("Invalid token structure")
    kid = header.get("kid")
    if header.get("alg") != "RS256" or not isinstance(kid, str) or not kid or header.get("crit"):
        raise DragonZpyderIdentityError("Unsupported identity signature")
    keys = jwks.get("keys") if isinstance(jwks, dict) else None
    if not isinstance(keys, list) or len(keys) > 20:
        raise DragonZpyderIdentityError("Invalid issuer signing keys")
    matches = [key for key in keys if isinstance(key, dict) and key.get("kid") == kid and key.get("kty") == "RSA"
               and key.get("alg", "RS256") == "RS256" and key.get("use", "sig") == "sig"]
    if len(matches) != 1:
        raise DragonZpyderIdentityError("Unknown issuer signing key")
    key = matches[0]
    try:
        n = int.from_bytes(_decode(key["n"]), "big")
        e = int.from_bytes(_decode(key["e"]), "big")
        if n.bit_length() < 2048 or e != 65537:
            raise DragonZpyderIdentityError("Weak identity signing key")
        public = rsa.RSAPublicNumbers(e, n).public_key()
        public.verify(_decode(pieces[2]), (pieces[0] + "." + pieces[1]).encode("ascii"),
                      padding.PKCS1v15(), hashes.SHA256())
    except (KeyError, ValueError, TypeError, InvalidSignature) as exc:
        raise DragonZpyderIdentityError("Identity token signature is invalid") from exc
    instant = int(time.time()) if now is None else now
    aud = claims.get("aud")
    valid_aud = aud == audience or isinstance(aud, list) and audience in aud
    if isinstance(aud, list) and len(aud) > 1 and claims.get("azp") != audience:
        raise DragonZpyderIdentityError("Invalid authorised party")
    if claims.get("iss") != ISSUER or not valid_aud:
        raise DragonZpyderIdentityError("Identity issuer or audience mismatch")
    subject = claims.get("sub")
    if not isinstance(subject, str) or not (1 <= len(subject) <= 255) or not nonce or not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
        raise DragonZpyderIdentityError("Identity or nonce mismatch")
    # OIDC time checks with a 60-second clock skew; never accept missing expiry.
    exp, iat = claims.get("exp"), claims.get("iat")
    nbf = claims.get("nbf", iat)
    if type(exp) is not int or type(iat) is not int or type(nbf) is not int:
        raise DragonZpyderIdentityError("Missing identity lifetime")
    if exp <= instant - 60 or iat > instant + 60 or nbf > instant + 60 or exp - iat > 3600:
        raise DragonZpyderIdentityError("Identity token expired or invalid")
    return claims
