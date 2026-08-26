import asyncio
import unittest
from types import SimpleNamespace

from packages.capabilities.discovery_provider import CapabilityDiscoveryProvider
from packages.capabilities.registry import CapabilityRegistry
from packages.channels.presentation import connector_tool_context, connector_tools, format_for_channel


class ConnectorPresentationToolTests(unittest.TestCase):
    def test_discord_declares_native_attachment_contract(self):
        tools = connector_tools("discord")
        self.assertEqual(tools.text_dialect, "discord_markdown")
        self.assertEqual(tools.attachment_strategy, "native_attachment")
        self.assertTrue(tools.supports_native_files)
        self.assertEqual(tools.max_text_chars, 2000)

    def test_email_declares_html_mime_contract(self):
        tools = connector_tools("email")
        self.assertEqual(tools.text_dialect, "html")
        self.assertEqual(tools.attachment_strategy, "mime_attachment")
        self.assertTrue(tools.supports_html)

    def test_contract_is_small_serializable_tool_context(self):
        payload = connector_tool_context("slack")
        self.assertEqual(payload["provider"], "slack")
        self.assertEqual(payload["attachment_strategy"], "native_file_upload")
        self.assertNotIn("token", payload)
        self.assertNotIn("permissions", payload)

    def test_chat_surfaces_normalize_markdown_tables(self):
        source = "| Item | Value |\n| --- | --- |\n| A | 1 |"
        rendered = format_for_channel(source, "discord")
        self.assertNotIn("| --- |", rendered)
        self.assertIn("**A**", rendered)
        self.assertIn("**Value:** 1", rendered)

    def test_connector_presentation_is_a_discoverable_read_only_tool(self):
        provider = CapabilityDiscoveryProvider(CapabilityRegistry())
        context = SimpleNamespace(
            tenant_id="personal:user-1",
            invocation={
                "channel": "discord",
                "surface": "discord_dm",
                "authority": [],
                "metadata": {"origin_provider": "discord"},
            },
        )
        result = asyncio.run(provider.execute(context, "connector.presentation", {}))
        self.assertTrue(result.success)
        presentation = result.evidence["presentation"]
        self.assertEqual(presentation["provider"], "discord")
        self.assertEqual(presentation["attachment_strategy"], "native_attachment")
        self.assertFalse(result.evidence["authorization_granted"])


if __name__ == "__main__":
    unittest.main()
