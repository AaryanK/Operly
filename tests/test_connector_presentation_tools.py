import unittest

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


if __name__ == "__main__":
    unittest.main()
