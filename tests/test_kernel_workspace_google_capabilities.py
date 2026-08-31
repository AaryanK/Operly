import unittest

from packages.kernel.contracts import CapabilityRisk
from packages.kernel.workspace_google_provider import PROVIDER_ID, workspace_google_capabilities


class WorkspaceGoogleCapabilityTests(unittest.TestCase):
    def setUp(self):
        self.specs = workspace_google_capabilities()
        self.by_id = {spec.id: spec for spec in self.specs}

    def test_google_capabilities_use_one_workspace_provider(self):
        self.assertEqual(len(self.specs), len(self.by_id))
        self.assertGreaterEqual(len(self.specs), 10)
        for spec in self.specs:
            self.assertEqual(spec.provider_id, PROVIDER_ID)
            self.assertEqual(spec.scopes, frozenset({"workspace"}))
            self.assertEqual(spec.resource_scope, "workspace")

    def test_google_reads_are_not_approval_gated(self):
        for capability_id in (
            "google.connection.status",
            "google.gmail.search",
            "google.gmail.read_message",
            "google.calendar.list_calendars",
            "google.calendar.list_events",
            "google.calendar.freebusy",
        ):
            spec = self.by_id[capability_id]
            self.assertEqual(spec.risk, CapabilityRisk.READ_ONLY)
            self.assertFalse(spec.approval_required)

    def test_external_side_effects_are_approval_gated(self):
        for capability_id in (
            "google.gmail.send_email",
            "google.gmail.modify_labels",
            "google.calendar.create_event",
            "google.calendar.update_event",
            "google.calendar.delete_event",
        ):
            self.assertTrue(self.by_id[capability_id].approval_required, capability_id)

    def test_gmail_draft_is_separate_from_send(self):
        draft = self.by_id["google.gmail.create_draft"]
        send = self.by_id["google.gmail.send_email"]
        self.assertEqual(draft.permissions, ("messaging:draft",))
        self.assertEqual(draft.risk, CapabilityRisk.LOW)
        self.assertFalse(draft.approval_required)
        self.assertTrue(draft.reversible)
        self.assertEqual(send.permissions, ("messaging:send",))
        self.assertEqual(send.risk, CapabilityRisk.HIGH)
        self.assertTrue(send.approval_required)

    def test_calendar_delete_is_non_reversible(self):
        spec = self.by_id["google.calendar.delete_event"]
        self.assertEqual(spec.permissions, ("calendar:write",))
        self.assertEqual(spec.risk, CapabilityRisk.HIGH)
        self.assertTrue(spec.approval_required)
        self.assertFalse(spec.reversible)


if __name__ == "__main__":
    unittest.main()
