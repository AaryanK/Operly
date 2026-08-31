import os
import unittest

from packages.kernel.contracts import CapabilityRisk, CapabilitySpec
from packages.mcp.gateway import (
    agent_description,
    narrow_scope_rules,
    scope_allows,
    scope_rule_covers,
    tool_definition,
)
from packages.mcp.oauth import decode_access_token, issue_access_token, pkce_s256


class McpGatewayContractTests(unittest.TestCase):
    def spec(
        self,
        capability_id: str,
        *,
        permission: str = "computer:execute",
        risk: CapabilityRisk = CapabilityRisk.LOW,
        approval: bool = False,
        reversible: bool = False,
    ) -> CapabilitySpec:
        return CapabilitySpec(
            id=capability_id,
            version="1.0.0",
            display_name="Example tool",
            description="Perform the requested governed Workspace operation.",
            provider_id="operly.test",
            scopes=frozenset({"workspace"}),
            input_schema={"type": "object", "properties": {"value": {"type": "string"}}},
            output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
            permissions=(permission,),
            risk=risk,
            approval_required=approval,
            resource_scope="workspace",
            reversible=reversible,
            tags=frozenset({"test"}),
        )

    def test_workspace_wildcard_does_not_mutate_spec_authority(self):
        spec = self.spec("computer.python.exec")
        self.assertTrue(scope_allows(spec, {"workspace:*"}))
        self.assertTrue(scope_allows(spec, {"computer.*"}))
        self.assertTrue(scope_allows(spec, {"computer:execute"}))
        self.assertFalse(scope_allows(spec, {"workflow.*"}))

    def test_scope_narrowing_never_expands_client_grant(self):
        self.assertTrue(scope_rule_covers("workspace:*", "computer.*"))
        self.assertTrue(scope_rule_covers("computer.*", "computer.python.exec"))
        self.assertFalse(scope_rule_covers("computer.*", "workflow.run.start"))
        narrowed = narrow_scope_rules(
            {"computer.python.exec", "workflow.run.start"},
            {"computer.*"},
        )
        self.assertEqual(narrowed, frozenset({"computer.python.exec"}))

    def test_agent_description_teaches_computer_and_approval_behavior(self):
        spec = self.spec("computer.browser.click", risk=CapabilityRisk.MEDIUM, approval=True)
        description = agent_description(spec)
        self.assertIn("computer_session_id", description)
        self.assertIn("human approval", description.lower())
        self.assertIn("do not retry blindly", description.lower())

    def test_workflow_description_explains_durable_orchestration(self):
        description = agent_description(self.spec("workflow.create", permission="workflows:write"))
        self.assertIn("durable", description.lower())
        self.assertIn("schedule", description.lower())

    def test_mcp_definition_preserves_canonical_json_schemas(self):
        spec = self.spec("computer.python.exec", risk=CapabilityRisk.READ_ONLY)
        tool = tool_definition(spec)
        self.assertEqual(tool["name"], spec.id)
        self.assertEqual(tool["inputSchema"], dict(spec.input_schema))
        self.assertEqual(tool["outputSchema"], dict(spec.output_schema))
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertEqual(tool["_meta"]["operly/permissions"], ["computer:execute"])

    def test_signed_access_token_is_typed_and_round_trips(self):
        previous = os.environ.get("OPERLY_MCP_TOKEN_SECRET")
        os.environ["OPERLY_MCP_TOKEN_SECRET"] = "test-mcp-secret-that-is-long-enough-for-ci"
        try:
            payload = {
                "grant_id": "grant-1",
                "principal_id": "principal-1",
                "tenant_id": "workspace-1",
                "client_id": "chatgpt",
                "resource": "https://operly.example/mcp",
                "scopes": ["computer.*"],
            }
            decoded = decode_access_token(issue_access_token(payload))
            for key, value in payload.items():
                self.assertEqual(decoded[key], value)
            self.assertEqual(decoded["token_kind"], "access")
        finally:
            if previous is None:
                os.environ.pop("OPERLY_MCP_TOKEN_SECRET", None)
            else:
                os.environ["OPERLY_MCP_TOKEN_SECRET"] = previous

    def test_pkce_s256_matches_rfc7636_example(self):
        verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
        self.assertEqual(pkce_s256(verifier), "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM")


if __name__ == "__main__":
    unittest.main()
