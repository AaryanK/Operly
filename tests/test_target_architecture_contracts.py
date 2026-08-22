import os
import unittest
from pathlib import Path
from unittest.mock import patch

from packages.capabilities.contracts import ApprovalPolicy, CapabilityDefinition, CapabilityResult
from packages.capabilities.firewall import ActionBackedCapabilityFirewall, CapabilityDecision, CapabilityInvocation
from packages.capabilities.providers import BaseProvider
from packages.capabilities.registry import CapabilityRegistry
from packages.capabilities.session_view import SessionCapabilityView
from packages.model_runtime import (
    ConfiguredModel,
    InferenceBudget,
    InferenceRequest,
    ModelPool,
    ModelRegistry,
    ModelSelector,
    register_model_provider,
)
from packages.model_runtime.ollama_client import OllamaError
from packages.plugins import PluginManifest, PluginManifestRegistry
from packages.runtime_plugins import register_builtin_runtimes
from packages.security.execution_context import ExecutionContext


class _DemoProvider(BaseProvider):
    name = "demo-provider"
    capabilities = (
        CapabilityDefinition(
            "demo.read",
            "demo_read",
            "Read demo records",
            {"type": "object", "properties": {}, "additionalProperties": False},
            {"type": "object"},
            risk_level="read_only",
            permissions=("demo:read",),
            approval_policy=ApprovalPolicy.AUTO,
            plugin_id="demo",
            category="demo",
            tags=frozenset({"records", "read"}),
            semantic_operations=frozenset({"look up demo record"}),
        ),
        CapabilityDefinition(
            "demo.send",
            "demo_send",
            "Send a demo notification",
            {
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="high",
            permissions=("demo:send",),
            approval_policy=ApprovalPolicy.ALWAYS,
            plugin_id="demo",
            category="messaging",
            tags=frozenset({"send", "message"}),
            semantic_operations=frozenset({"send notification"}),
        ),
    )

    async def execute(self, context, capability_name, arguments):
        return CapabilityResult(True, capability_name == "demo.send", {"ok": True})

    async def verify(self, context, capability_name, arguments, result):
        return result


class _FailClient:
    last_model = "fail-model"

    async def chat(self, messages, tools=None):
        raise OllamaError("temporary upstream failure", status=503, retryable=True)


class _OkClient:
    def __init__(self, route):
        self.last_model = route.primary

    async def chat(self, messages, tools=None):
        return {"role": "assistant", "content": "ok"}


class TargetArchitectureContractsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        register_model_provider("test-fail", lambda route: _FailClient(), replace=True)
        register_model_provider("test-ok", lambda route: _OkClient(route), replace=True)

    async def test_model_pool_fails_over_across_providers(self):
        pool = ModelPool(
            (
                ConfiguredModel(
                    resource_id="first",
                    provider="test-fail",
                    provider_model_id="fail-model",
                    tags={"fast"},
                    capabilities={"text", "tools"},
                ),
                ConfiguredModel(
                    resource_id="second",
                    provider="test-ok",
                    provider_model_id="ok-model",
                    tags={"reliable"},
                    capabilities={"text", "tools"},
                ),
            )
        )
        result = await pool.infer(
            InferenceRequest(
                messages=({"role": "user", "content": "hello"},),
                budget=InferenceBudget(timeout_seconds=1, attempts_per_model=1),
            )
        )
        self.assertEqual(result.provider, "test-ok")
        self.assertEqual(result.provider_model_id, "ok-model")
        self.assertEqual(result.message["content"], "ok")

    def test_model_selector_prefers_tags_not_provider_names(self):
        registry = ModelRegistry()
        registry.configure(
            id="slow",
            provider="test-ok",
            model="slow-model",
            tags={"heavy"},
            capabilities={"unit-test-capability"},
        )
        registry.configure(
            id="fast",
            provider="test-ok",
            model="fast-model",
            tags={"fast", "free"},
            capabilities={"unit-test-capability"},
        )
        selected = registry.resolve(
            ModelSelector(
                requires=frozenset({"unit-test-capability"}),
                prefer_tags=frozenset({"fast"}),
            )
        )
        self.assertEqual(selected.id, "fast")

    def test_capability_discovery_is_separate_from_authority(self):
        registry = CapabilityRegistry()
        registry.register(_DemoProvider())
        rows = registry.search(
            "workspace-a",
            "send notification",
            authority={"demo:read"},
        )
        self.assertEqual(rows[0]["id"], "demo.send")
        self.assertFalse(rows[0]["authorized"])
        described = registry.describe(
            "workspace-a",
            ["demo.send"],
            authority={"demo:read"},
        )
        self.assertFalse(described[0]["authorized"])
        self.assertIn("input_schema", described[0])

    def test_progressive_view_exposes_described_schema_only_when_authorized(self):
        registry = CapabilityRegistry()
        registry.register(_DemoProvider())
        view = SessionCapabilityView(
            registry,
            "workspace-a",
            {"demo:read", "demo:send"},
        )
        self.assertNotIn("demo.send", {row["function"]["name"] for row in view.schemas()})
        view.observe(
            "capability.describe",
            {
                "observation": {
                    "capabilities": [{"id": "demo.send", "authorized": True}]
                }
            },
        )
        self.assertIn("demo.send", {row["function"]["name"] for row in view.schemas()})

    async def test_firewall_evaluation_preserves_existing_permission_and_approval_contract(self):
        registry = CapabilityRegistry()
        registry.register(_DemoProvider())
        firewall = ActionBackedCapabilityFirewall(registry)
        allowed = ExecutionContext(
            workspace_id="workspace-a",
            user_id="user-a",
            membership_id="member-a",
            role="owner",
            permissions=frozenset({"demo:read", "demo:send"}),
            channel="web",
        )
        denied = ExecutionContext(
            workspace_id="workspace-a",
            user_id="user-b",
            membership_id="member-b",
            role="employee",
            permissions=frozenset({"demo:read"}),
            channel="web",
        )
        request = CapabilityInvocation(
            capability_id="demo.send",
            arguments={"message": "hello"},
            objective="send demo",
        )
        self.assertEqual(await firewall.evaluate(request, allowed), CapabilityDecision.ASK)
        self.assertEqual(await firewall.evaluate(request, denied), CapabilityDecision.DENY)

    def test_plugin_manifest_owns_capabilities(self):
        registry = PluginManifestRegistry()
        manifest = PluginManifest(
            id="demo",
            version="1.0.0",
            display_name="Demo",
            capabilities=_DemoProvider.capabilities,
        )
        registry.register(manifest)
        self.assertEqual(registry.owner_for_capability("demo.send"), "demo")

    def test_builtin_runtime_profiles_are_plugins(self):
        registry = register_builtin_runtimes()
        self.assertEqual(registry.get("static-web-js").spec.id, "static-web-js")
        self.assertEqual(registry.get("python-stdlib-web").spec.id, "python-stdlib-web")

    def test_studio_has_no_concrete_model_provider_import(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "packages/studio/model_latency_policy.py").read_text()
        self.assertNotIn("OpenRouterClient", source)
        self.assertNotIn("OllamaClient", source)
        placement = (root / "packages/capability_sandbox/target_resolution.py").read_text()
        self.assertNotIn("OllamaClient", placement)
        planning = (root / "packages/custom_software/provider_planning.py").read_text()
        self.assertNotIn("OpenRouterClient", planning)
        self.assertNotIn("OllamaClient", planning)


if __name__ == "__main__":
    unittest.main()
