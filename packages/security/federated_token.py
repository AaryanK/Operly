"""Strict DragonZpyder access-token validation before Operly local user mapping.

Call using a PUBLIC key selected by a trusted, pinned JWKS client. Never load
the signing PRIVATE key into Operly. Verification is not session authorization:
workspace membership and Kernel policies remain Operly-controlled.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

import jwt

from packages.security.federated_identity import FederationDenied, resolve_federated_user


class FederatedTokenDenied(PermissionError):
    pass


def verify_federated_access_token(
    token: str, *, public_key, issuer: str, audience: str, required_scope: str = "operly:access",
) -> tuple[str, str]:
    origin = urlsplit(issuer)
    if (origin.scheme != "https" or not origin.hostname or origin.username
            or origin.password or origin.path not in ("", "/") or origin.query or origin.fragment):
        raise FederatedTokenDenied("Identity issuer is not a trusted HTTPS origin")
    if (not isinstance(audience, str) or not audience
            or not isinstance(required_scope, str) or not required_scope
            or not isinstance(token, str) or len(token) > 8192 or not token):
        raise FederatedTokenDenied("Invalid identity verifier configuration")
    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256" or header.get("typ") != "at+jwt":
            raise FederatedTokenDenied("Unsupported token kind")
        claims = jwt.decode(
            token, public_key, algorithms=["RS256"], issuer=issuer, audience=audience,
            options={"require": ["iss", "sub", "aud", "iat", "nbf", "exp", "jti"]},
            leeway=15,
        )
    except (jwt.PyJWTError, ValueError, TypeError) as exc:
        raise FederatedTokenDenied("Access token rejected") from exc
    if claims.get("token_use") != "access" or claims.get("typ") != "at+jwt":
        raise FederatedTokenDenied("Not an access token")
    if (not isinstance(claims.get("scope"), str)
            or required_scope not in claims["scope"].split()):
        raise FederatedTokenDenied("Missing resource scope")
    if (not isinstance(claims.get("sub"), str) or not 1 <= len(claims["sub"]) <= 160
            or not isinstance(claims.get("jti"), str) or not 1 <= len(claims["jti"]) <= 128):
        raise FederatedTokenDenied("Invalid principal")
    if (not isinstance(claims.get("iat"), int) or not isinstance(claims.get("exp"), int)
            or not 1 <= claims["exp"] - claims["iat"] <= 300):
        raise FederatedTokenDenied("Invalid access-token lifetime")
    return issuer, claims["sub"]


async def resolve_verified_access_user(
    db, *, token: str, public_key, issuer: str, audience: str,
):
    verified_issuer, verified_subject = verify_federated_access_token(
        token, public_key=public_key, issuer=issuer, audience=audience,
    )
    return await resolve_federated_user(
        db, verified_issuer=verified_issuer, verified_subject=verified_subject,
    )
