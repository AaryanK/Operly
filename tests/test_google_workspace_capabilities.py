import unittest

from apps.api.connectors_router import google_capabilities
from packages.capabilities.agent_harness import PluginAgentHarness, ROLE_AUTHORITY
from packages.connectors.google_provider import (
    CALENDAR,
    CALENDAR_FREEBUSY,
    CALENDAR_LIST_READONLY,
    GMAIL_MODIFY,
    GMAIL_SEND,
    GmailProvider,
    GoogleCalendarProvider,
    _email_message,
    sanitize_html_email,
)


class GoogleWorkspaceCapabilityTests(unittest.TestCase):
    def test_rich_html_email_is_multipart_and_strips_active_content(self):
        rich = """
        <div onclick="steal()"><h2>Quote calculator</h2>
        <p style="font-weight:bold">Static rich content works.</p>
        <script>alert('x')</script>
        <a href="javascript:alert(1)">bad</a>
        <a href="https://operly.example">good</a></div>
        """
        sanitized = sanitize_html_email(rich)
        self.assertIn("<h2>Quote calculator</h2>", sanitized)
        self.assertIn("https://operly.example", sanitized)
        self.assertNotIn("script", sanitized.lower())
        self.assertNotIn("onclick", sanitized.lower())
        self.assertNotIn("javascript:", sanitized.lower())

        message = _email_message(
            to=["client@example.com"],
            subject="A rich email",
            html_body=rich,
        )
        self.assertTrue(message.is_multipart())
        types = [part.get_content_type() for part in message.walk()]
        self.assertIn("text/plain", types)
        self.assertIn("text/html", types)

    def test_google_permission_tiers_expose_only_granted_capabilities(self):
        basic = set(google_capabilities({GMAIL_SEND, CALENDAR}))
        self.assertIn("gmail.send_email", basic)
        self.assertIn("calendar.create_event", basic)
        self.assertIn("calendar.list_events", basic)
        self.assertNotIn("gmail.search", basic)
        self.assertNotIn("gmail.modify_labels", basic)
        self.assertNotIn("calendar.freebusy", basic)

        assistant = set(
            google_capabilities(
                {
                    GMAIL_MODIFY,
                    CALENDAR,
                    CALENDAR_FREEBUSY,
                    CALENDAR_LIST_READONLY,
                }
            )
        )
        self.assertTrue(
            {
                "gmail.send_email",
                "gmail.search",
                "gmail.read_message",
                "gmail.modify_labels",
                "gmail.create_draft",
                "calendar.create_event",
                "calendar.list_events",
                "calendar.update_event",
                "calendar.delete_event",
                "calendar.freebusy",
                "calendar.list_calendars",
            }.issubset(assistant)
        )

    def test_mutations_stay_approval_gated_and_reads_are_auto(self):
        gmail = {item.id: item for item in GmailProvider.capabilities}
        calendar = {item.id: item for item in GoogleCalendarProvider.capabilities}
        self.assertEqual(gmail["gmail.send_email"].approval_policy.value, "always")
        self.assertEqual(gmail["gmail.modify_labels"].approval_policy.value, "always")
        self.assertEqual(gmail["gmail.search"].approval_policy.value, "auto")
        self.assertEqual(calendar["calendar.update_event"].approval_policy.value, "always")
        self.assertEqual(calendar["calendar.delete_event"].approval_policy.value, "always")
        self.assertEqual(calendar["calendar.list_events"].approval_policy.value, "auto")
        self.assertEqual(calendar["calendar.freebusy"].approval_policy.value, "auto")

    def test_roles_separate_private_google_accounts_from_shared_tenant_messages(self):
        harness = PluginAgentHarness()
        employee = ROLE_AUTHORITY["employee"]
        owner = ROLE_AUTHORITY["owner"]
        manager = ROLE_AUTHORITY["manager"]
        bounded_agent = ROLE_AUTHORITY["agent"]

        # Employees can still read shared Operly/Discord message history without
        # inheriting access to a tenant-connected Gmail mailbox or Calendar.
        self.assertIn("messages:read", employee)
        self.assertIn("messaging:read", employee)
        self.assertNotIn("gmail:read", employee)
        self.assertNotIn("gmail:write", employee)
        self.assertNotIn("gmail:draft", employee)
        self.assertNotIn("calendar:read", employee)
        self.assertFalse(harness.capability_authorized("gmail.search", employee))
        self.assertFalse(harness.capability_authorized("gmail.read_message", employee))
        self.assertFalse(harness.capability_authorized("gmail.modify_labels", employee))
        self.assertFalse(harness.capability_authorized("gmail.create_draft", employee))

        for authority in (owner, manager):
            self.assertIn("gmail:read", authority)
            self.assertIn("gmail:write", authority)
            self.assertIn("gmail:draft", authority)
            self.assertIn("calendar:read", authority)
            self.assertIn("calendar:write", authority)
            self.assertTrue(harness.capability_authorized("gmail.search", authority))
            self.assertTrue(harness.capability_authorized("gmail.modify_labels", authority))

        self.assertIn("gmail:read", bounded_agent)
        self.assertIn("gmail:draft", bounded_agent)
        self.assertNotIn("gmail:write", bounded_agent)
        self.assertIn("calendar:read", bounded_agent)
        self.assertNotIn("calendar:write", bounded_agent)

        self.assertNotIn("messaging:send", employee)
        self.assertIn("messaging:send", owner)


if __name__ == "__main__":
    unittest.main()
