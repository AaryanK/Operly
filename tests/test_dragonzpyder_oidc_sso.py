from __future__ import annotations

import base64
import json
import time
import unittest
from unittest.mock import patch
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from packages.security.dragonzpyder_oidc import (
    ISSUER, create_pkce, verified_id_token, DragonZpyderIdentityError,
)
from apps.api.dragonzpyder_sso_router import enabled, conf


def encoded(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class DragonZpyderOIDCContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        numbers = cls.private.public_key().public_numbers()
        cls.jwks = {"keys": [{"kid": "v1", "alg": "RS256", "use": "sig", "kty": "RSA",
                               "n": encoded(numbers.n.to_bytes(256, "big")),
                               "e": encoded(numbers.e.to_bytes(3, "big"))}]}

    def token(self, **updates):
        now = int(time.time())
        claims = {"iss": ISSUER, "sub": "dz-human-subject-1",
                  "aud": "operly-web", "nonce": "test-nonce",
                  "iat": now, "exp": now + 300}
        claims.update(updates)
        head = encoded(json.dumps({"alg": "RS256", "kid": "v1"}).encode())
        body = encoded(json.dumps(claims).encode())
        message = (head + "." + body).encode()
        sig = self.private.sign(message, padding.PKCS1v15(), hashes.SHA256())
        return head + "." + body + "." + encoded(sig)

    def test_signed_token_requires_issuer_audience_nonce_and_expiry(self):
        result = verified_id_token(self.token(), self.jwks, audience="operly-web", nonce="test-nonce")
        self.assertEqual(result["sub"], "dz-human-subject-1")
        for changes in ({"iss": "https://evil.example"}, {"aud": "some-other-app"},
                        {"nonce": "wrong"}, {"exp": int(time.time()) - 600}):
            with self.subTest(changes=changes):
                with self.assertRaises(DragonZpyderIdentityError):
                    verified_id_token(self.token(**changes), self.jwks,
                                      audience="operly-web", nonce="test-nonce")

    def test_modified_signature_is_rejected(self):
        token = self.token()
        with self.assertRaises(DragonZpyderIdentityError):
            verified_id_token(token[:-3] + "abc", self.jwks,
                              audience="operly-web", nonce="test-nonce")

    def test_pkce_produces_distinct_challenges(self):
        a, challenge_a = create_pkce()
        b, challenge_b = create_pkce()
        self.assertNotEqual(a, b)
        self.assertNotEqual(challenge_a, challenge_b)
        self.assertTrue(43 <= len(a) <= 128)

    def test_ssso_disabled_without_explicit_flag(self):
        with patch.dict("os.environ", {"DRAGONZPYDER_SSO_ENABLED": "false"}):
            self.assertFalse(enabled())
            with self.assertRaises(DragonZpyderIdentityError):
                conf()

    def test_sso_requires_server_secrets(self):
        with patch.dict("os.environ", {
            "DRAGONZPYDER_SSO_ENABLED": "true",
            "DRAGONZPYDER_SSO_CLIENT_ID": "operly-web",
            "DRAGONZPYDER_SSO_CLIENT_SECRET": "",
            "DRAGONZPYDER_SSO_STATE_SECRET": "too-short",
        }):
            with self.assertRaises(DragonZpyderIdentityError):
                conf()
