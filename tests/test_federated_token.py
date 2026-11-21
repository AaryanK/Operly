"""Offline validation of synthetic JWTs; no production account or network."""
import sys
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from packages.security.federated_token import FederatedTokenDenied, verify_federated_access_token


@pytest.fixture
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=3072)


def signed(key, *, kind="access", scope="operly:access", audience="operly-api",
           issuer="https://api.dragonzpyder.xyz", lifetime=300, typ="at+jwt"):
    import time
    now = int(time.time())
    return jwt.encode(
        {"iss": issuer, "sub": "person-1", "aud": audience, "iat": now,
         "nbf": now, "exp": now + lifetime, "jti": "sample-jti",
         "token_use": kind, "typ": typ, "scope": scope},
        key, algorithm="RS256", headers={"typ": typ, "kid": "synthetic"},
    )


def check(token, key):
    return verify_federated_access_token(
        token, public_key=key.public_key(), issuer="https://api.dragonzpyder.xyz",
        audience="operly-api",
    )


def test_only_correctly_scoped_signed_access_token_is_valid(key):
    assert check(signed(key), key) == ("https://api.dragonzpyder.xyz", "person-1")


@pytest.mark.parametrize("overrides", [
    {"kind": "id", "typ": "JWT"},
    {"scope": "dragonzpyder:personal"},
    {"audience": "different-product"},
    {"issuer": "https://attacker.example"},
    {"lifetime": 301},
])
def test_rejects_wrong_token_kind_scope_audience_issuer_or_lifetime(key, overrides):
    with pytest.raises(FederatedTokenDenied):
        check(signed(key, **overrides), key)


def test_rejects_wrong_signing_key(key):
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    with pytest.raises(FederatedTokenDenied):
        check(signed(attacker), key)


def test_no_access_to_untrusted_issuer_or_unsigned_content(key):
    with pytest.raises(FederatedTokenDenied):
        verify_federated_access_token(
            signed(key), public_key=key.public_key(),
            issuer="http://api.dragonzpyder.xyz", audience="operly-api",
        )
    with pytest.raises(FederatedTokenDenied):
        check("not-a-jwt", key)
