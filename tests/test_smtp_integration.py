import os
import unittest

from packages.email.service import get_email_service


@unittest.skipUnless(
    os.getenv("OPERLY_SMTP_INTEGRATION") == "1",
    "Set OPERLY_SMTP_INTEGRATION=1 and SMTP_TEST_TO to send a real test email",
)
class SMTPIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_opt_in_delivery(self):
        recipient = os.getenv("SMTP_TEST_TO", "").strip()
        self.assertTrue(recipient, "SMTP_TEST_TO is required for the opt-in SMTP test")
        await get_email_service().send_security_alert(
            to_email=recipient,
            display_name="OPERLY operator",
            summary="This message confirms the explicitly enabled SMTP integration test.",
            app_url=os.getenv("PUBLIC_BASE_URL", "http://localhost:8000"),
        )


if __name__ == "__main__":
    unittest.main()
