import unittest

from packages.kernel.contracts import CapabilityRisk
from packages.personal_modules.connectors import (
    GOOGLE_ASSISTANT_SCOPES,
    GOOGLE_BASIC_SCOPES,
)
from packages.personal_modules.google_provider import (
    CALENDAR,
    CALENDAR_FREEBUSY,
    CALENDAR_LIST_READONLY,
    GMAIL_READONLY,
    PROVIDER_ID,
    PersonalGoogleProvider,
    personal_google_capabilities,
    supported_capability_ids,
)
from packages.personal_modules.router import _tool_summary
from packages.personal_modules.runtime import build_personal_runtime
from packages.security.execution_context import ExecutionContext, ScopeKind
from packages.security.surfaces import SurfaceKind
from packages.workspace_modules.integrations.google import (
    WorkspaceGoogleProvider,
    workspace_google_capabilities,
)


class PersonalGoogleCapabilityTests(unittest.TestCase):
    def setUp(self):
        self.specs = personal_google_capabilities()
        self.by_id = {spec.id: spec for spec in self.specs}

    def test_personal_google_contracts_are_account_scoped(self):
        self.assertEqual(len(self.specs), len(self.by_id))
        self.assertGreaterEqual(len(self.specs), 12)
        for spec in self.specs:
            self.assertEqual(spec.provider_id, PROVIDER_ID)
            self.assertEqual(spec.scopes, frozenset({"personal"}))
            self.assertEqual(spec.resource_scope, "personal")
            self.assertIn("personal", spec.tags)

    def test_reads_are_not_approval_gated(self):
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

    def test_draft_is_separate_from_send(self):
        draft = self.by_id["google.gmail.create_draft"]
        send = self.by_id["google.gmail.send_email"]
        self.assertEqual(draft.risk, CapabilityRisk.LOW)
        self.assertFalse(draft.approval_required)
        self.assertTrue(draft.reversible)
        self.assertEqual(send.risk, CapabilityRisk.HIGH)
        self.assertTrue(send.approval_required)
        self.assertFalse(send.reversible)

    def test_basic_oauth_tier_is_read_only(self):
        scopes = set(GOOGLE_BASIC_SCOPES)
        self.assertIn(GMAIL_READONLY, scopes)
        self.assertIn(CALENDAR_FREEBUSY, scopes)
        self.assertIn(CALENDAR_LIST_READONLY, scopes)
        self.assertNotIn(CALENDAR, scopes)
        capabilities = set(supported_capability_ids(scopes))
        self.assertIn("google.gmail.search", capabilities)
        self.assertIn("google.gmail.read_message", capabilities)
        self.assertIn("google.calendar.list_calendars", capabilities)
        self.assertIn("google.calendar.freebusy", capabilities)
        self.assertNotIn("google.gmail.send_email", capabilities)
        self.assertNotIn("google.calendar.create_event", capabilities)
        self.assertNotIn("google.calendar.update_event", capabilities)
        self.assertNotIn("google.calendar.delete_event", capabilities)

    def test_assistant_oauth_tier_enables_calendar_mutation(self):
        scopes = set(GOOGLE_ASSISTANT_SCOPES)
        self.assertIn(CALENDAR, scopes)
        capabilities = set(supported_capability_ids(scopes))
        self.assertIn("google.calendar.list_events", capabilities)
        self.assertIn("google.calendar.create_event", capabilities)
        self.assertIn("google.calendar.update_event", capabilities)
        self.assertIn("google.calendar.delete_event", capabilities)

    def test_discovery_summary_is_schema_light(self):
        summary = _tool_summary(self.by_id["google.gmail.search"])
        self.assertEqual(summary["id"], "google.gmail.search")
        self.assertIn("contract_endpoint", summary)
        self.assertNotIn("input_schema", summary)
        self.assertNotIn("output_schema", summary)

    def test_same_semantic_id_has_distinct_scope_contracts(self):
        personal = build_personal_runtime().registry.get("google.gmail.search")
        workspace = {spec.id: spec for spec in workspace_google_capabilities()}[
            "google.gmail.search"
        ]
        self.assertEqual(personal.scopes, frozenset({"personal"}))
        self.assertEqual(personal.resource_scope, "personal")
        self.assertEqual(workspace.scopes, frozenset({"workspace"}))
        self.assertEqual(workspace.resource_scope, "workspace")
        self.assertNotEqual(personal.provider_id, workspace.provider_id)

    def test_workspace_contracts_stay_workspace_scoped(self):
        for spec in workspace_google_capabilities():
            self.assertEqual(spec.scopes, frozenset({"workspace"}))
            self.assertEqual(spec.resource_scope, "workspace")


class GoogleProviderScopeIsolationTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def personal_context() -> ExecutionContext:
        return ExecutionContext(
            workspace_id=None,
            user_id="user-1",
            membership_id=None,
            role="personal_owner",
            permissions=frozenset(),
            channel="web",
            surface=SurfaceKind.PERSONAL_PRIVATE,
            scope_kind=ScopeKind.PERSONAL,
            principal_id="user:user-1",
            workspace_mode="personal",
        )

    @staticmethod
    def workspace_context() -> ExecutionContext:
        return ExecutionContext(
            workspace_id="workspace-1",
            user_id="user-1",
            membership_id="member-1",
            role="owner",
            permissions=frozenset(),
            channel="web",
            surface=SurfaceKind.WORKSPACE_PRIVATE,
            scope_kind=ScopeKind.WORKSPACE,
            principal_id="user:user-1",
            workspace_mode="full",
        )

    async def test_personal_provider_rejects_workspace_authority_before_db_access(self):
        spec = {item.id: item for item in personal_google_capabilities()}[
            "google.gmail.search"
        ]
        with self.assertRaises(PermissionError):
            await PersonalGoogleProvider().execute(
                None,
                context=self.workspace_context(),
                capability=spec,
                arguments={"query": "newer_than:1d"},
                minimum_context={},
            )

    async def test_workspace_provider_rejects_personal_authority_before_db_access(self):
        spec = {item.id: item for item in workspace_google_capabilities()}[
            "google.gmail.search"
        ]
        with self.assertRaises(PermissionError):
            await WorkspaceGoogleProvider().execute(
                None,
                context=self.personal_context(),
                capability=spec,
                arguments={"query": "newer_than:1d"},
                minimum_context={},
            )


if __name__ == "__main__":
    unittest.main()
