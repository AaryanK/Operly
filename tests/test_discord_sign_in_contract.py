from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DiscordSignInContractTests(unittest.TestCase):
    def test_oauth_callback_bridges_auth_and_channel_identity(self):
        source = (ROOT / "apps/api/channel_identity_router.py").read_text(encoding="utf-8")
        self.assertIn('@router.get("/discord/sign-in")', source)
        self.assertIn('@router.get("/discord/callback")', source)
        self.assertIn('"scope": "identify email"', source)
        self.assertIn('provider="discord"', source)
        self.assertIn("IdentityService.link_external_identity", source)
        self.assertIn("_create_session(db, request, user.id, None)", source)
        self.assertIn("DISCORD_OAUTH_STATE_COOKIE", source)

    def test_old_discord_pairing_http_paths_are_retired(self):
        source = (ROOT / "apps/api/channel_identity_router.py").read_text(encoding="utf-8")
        self.assertIn("Discord pairing codes are retired. Use Sign in with Discord instead.", source)
        self.assertIn("Discord web-claim links are retired. Use Sign in with Discord instead.", source)

    def test_auth_ui_exposes_discord_sign_in(self):
        source = (ROOT / "apps/web/static/auth-runtime.js").read_text(encoding="utf-8")
        self.assertIn("Sign in with Discord", source)
        self.assertIn("/api/identities/discord/sign-in", source)

    def test_discord_runtime_keeps_compatibility_entrypoint_but_no_pairing(self):
        source = (ROOT / "packages/connectors/discord/secure_runtime.py").read_text(encoding="utf-8")
        self.assertIn("async def create_channel_link", source)
        self.assertIn("DISCORD_SIGN_IN_URL", source)
        self.assertNotIn("IdentityLinkService", source)
        self.assertNotIn("claim_from_channel", source)
        self.assertNotIn("create_from_channel", source)


if __name__ == "__main__":
    unittest.main()
