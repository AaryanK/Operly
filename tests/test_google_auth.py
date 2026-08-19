import os
import time
import unittest
from unittest.mock import patch

from apps.api.google_auth import GoogleAuthenticationError, verify_google_credential


class GoogleCredentialVerificationTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(
            os.environ,
            {"GOOGLE_AUTH_CLIENT_ID": "operly-client.apps.googleusercontent.com"},
        )
        self.environment.start()
        self.claims = {
            "iss": "https://accounts.google.com",
            "aud": "operly-client.apps.googleusercontent.com",
            "sub": "google-subject-123",
            "email": "Owner@Example.com",
            "email_verified": True,
            "name": "Owner",
            "nonce": "browser-nonce",
            "exp": int(time.time()) + 300,
        }

    def tearDown(self):
        self.environment.stop()

    def verify(self, claims=None):
        returned = self.claims if claims is None else claims
        with patch("google.oauth2.id_token.verify_oauth2_token", return_value=returned):
            return verify_google_credential("signed-google-credential", "browser-nonce")

    def assert_rejected(self, claims):
        with self.assertRaises(GoogleAuthenticationError):
            self.verify(claims)

    def test_valid_claims_are_normalized(self):
        identity = self.verify()
        self.assertEqual(identity.subject, "google-subject-123")
        self.assertEqual(identity.email, "owner@example.com")

    def test_wrong_audience_is_rejected(self):
        self.assert_rejected({**self.claims, "aud": "other-client"})

    def test_wrong_issuer_is_rejected(self):
        self.assert_rejected({**self.claims, "iss": "https://attacker.example"})

    def test_unverified_email_and_nonce_mismatch_are_rejected(self):
        self.assert_rejected({**self.claims, "email_verified": False})
        self.assert_rejected({**self.claims, "nonce": "different"})

    def test_expired_claim_is_rejected_even_if_upstream_returns_it(self):
        self.assert_rejected({**self.claims, "exp": int(time.time()) - 1})

    def test_tampered_or_malformed_token_rejection_is_wrapped(self):
        with patch(
            "google.oauth2.id_token.verify_oauth2_token",
            side_effect=ValueError("invalid signature"),
        ):
            with self.assertRaisesRegex(GoogleAuthenticationError, "could not confirm"):
                verify_google_credential("tampered", "browser-nonce")


if __name__ == "__main__":
    unittest.main()
