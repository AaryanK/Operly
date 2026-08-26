import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from packages.harness.plugins import (
    RuntimePluginContext,
    RuntimePluginRegistry,
    RuntimePluginUnavailable,
)
from packages.model_runtime import InferenceRequest, InferenceResult
from packages.model_runtime.routing_policy import role_routing_profile
from packages.model_runtime.semantic_router import SemanticDecision, SemanticRoutingError
from packages.model_runtime.task_routing import (
    ModelTaskRouterPlugin,
    TaskRoutedBusinessModel,
    route_business_task,
)


class _DecliningPlugin:
    id = "decline"
    kind = "test"
    priority = 1

    def supports(self, payload, context):
        return True

    async def invoke(self, payload, context):
        raise RuntimePluginUnavailable("not for me")


class _WorkingPlugin:
    id = "work"
    kind = "test"
    priority = 2

    def supports(self, payload, context):
        return True

    async def invoke(self, payload, context):
        return {"ok": True, "value": payload["value"]}


class _FakeSpecialist:
    id = "fake-specialist"

    def __init__(self):
        self.requests = []

    async def infer(self, request):
        self.requests.append(request)
        return InferenceResult(
            message={"role": "assistant", "content": "done"},
            model_resource_id=self.id,
            provider="test",
            provider_model_id=self.id,
            latency_ms=1,
        )


class RuntimePluginRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def test_registry_falls_through_only_when_plugin_declines(self):
        registry = RuntimePluginRegistry()
        registry.register(_DecliningPlugin())
        registry.register(_WorkingPlugin())
        result = await registry.invoke(
            "test",
            {"value": 7},
            RuntimePluginContext(channel="test"),
        )
        self.assertEqual(result, {"ok": True, "value": 7})


class TaskRouterPluginTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_router_selects_role_by_semantics_not_keyword_classifier(self):
        semantic = SemanticDecision(
            domain_match=True,
            known=True,
            route_id="bounded_task",
            reason="The objective requires a governed external operation after reading supplied material.",
        )
        with patch(
            "packages.model_runtime.task_routing._base_model_for_role",
            return_value=object(),
        ), patch(
            "packages.model_runtime.task_routing.SemanticRouter.decide",
            new=AsyncMock(return_value=semantic),
        ):
            decision = await ModelTaskRouterPlugin().invoke(
                {
                    "objective": "Make sure Dad receives a separate explanation for every document.",
                    "has_attachments": True,
                    "attachment_count": 7,
                    "tool_count": 12,
                },
                RuntimePluginContext(channel="discord", surface="private/direct"),
            )

        self.assertEqual(decision.role, "bounded_task")
        self.assertEqual(decision.task_type, "bounded_operation")
        self.assertIn("model router", decision.reason)

    async def test_router_model_failure_uses_deterministic_fallback(self):
        with patch(
            "packages.model_runtime.task_routing._base_model_for_role",
            return_value=object(),
        ), patch(
            "packages.model_runtime.task_routing.SemanticRouter.decide",
            new=AsyncMock(side_effect=SemanticRoutingError("bad routing response")),
        ):
            decision = await route_business_task("debug the website code")

        self.assertEqual(decision.role, "coding")
        self.assertIn("fallback heuristic", decision.reason)

    async def test_first_hop_route_is_reused_inside_tool_loop(self):
        specialist = _FakeSpecialist()
        request = InferenceRequest(
            messages=(
                {"role": "user", "content": "do the work"},
                {
                    "role": "assistant",
                    "content": "working",
                    "_operly_task_route": {
                        "taskType": "bounded_operation",
                        "role": "bounded_task",
                        "toolPolicy": "bounded_action_with_approval",
                        "confidence": 0.9,
                        "reason": "already routed",
                    },
                },
                {"role": "tool", "content": "{}", "tool_name": "x"},
            ),
            tools=(),
        )
        with patch(
            "packages.model_runtime.task_routing.model_for_requirements",
            return_value=specialist,
        ) as resolver, patch(
            "packages.model_runtime.task_routing.route_business_task",
            new=AsyncMock(side_effect=AssertionError("router should not run twice")),
        ):
            result = await TaskRoutedBusinessModel().infer(request)

        resolver.assert_called_once()
        requirements = resolver.call_args.args[0]
        self.assertEqual(resolver.call_args.kwargs["fallback_role"], "bounded_task")
        self.assertEqual(requirements.requires, frozenset({"text"}))
        self.assertEqual(result.message["content"], "done")
        self.assertEqual(specialist.requests[0].metadata["task_route"]["role"], "bounded_task")

    def test_router_and_operation_profiles_have_correct_capability_requirements(self):
        self.assertEqual(
            role_routing_profile("router").requires,
            frozenset({"text", "reasoning"}),
        )
        self.assertEqual(
            role_routing_profile("bounded_task").requires,
            frozenset({"text", "tools"}),
        )
        self.assertEqual(
            role_routing_profile("attachment_vision").requires,
            frozenset({"text", "reasoning", "vision"}),
        )


class AttachmentPluginTests(unittest.TestCase):
    def test_attachment_processor_selects_model_role_but_never_action_tools(self):
        from packages.business_brain.attachments.multimodal_processor import (
            MultimodalProcessor,
            SYSTEM_PROMPT,
        )

        text_client = object()
        vision_client = object()
        with patch(
            "packages.business_brain.attachments.multimodal_processor.model_chat_client_for_role",
            side_effect=[text_client, vision_client],
        ) as resolver:
            processor = MultimodalProcessor()
            self.assertIs(processor._client(vision=False), text_client)
            self.assertIs(processor._client(vision=True), vision_client)

        self.assertEqual(
            [call.args[0] for call in resolver.call_args_list],
            ["attachment_text", "attachment_vision"],
        )
        self.assertIn("perception plugin only", SYSTEM_PROMPT)
        self.assertIn("never claim that an email", SYSTEM_PROMPT)


class DiscordAttachmentHandoffTests(unittest.IsolatedAsyncioTestCase):
    async def test_personal_attachment_turn_uses_connector_ingress_then_channel_agent(self):
        from packages.channels.envelope import ChannelAttachment
        from packages.connectors.discord import bot_shared

        class _Typing:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class _Channel:
            id = 42

            def typing(self):
                return _Typing()

        message = SimpleNamespace(
            id=12345,
            author=SimpleNamespace(bot=False, id=7, display_name="User"),
            content="Use this file and email the result",
            attachments=[SimpleNamespace(filename="input.pdf")],
            guild=None,
            channel=_Channel(),
        )

        @asynccontextmanager
        async def _fake_session_scope():
            yield object()

        resolution = SimpleNamespace(
            user_id="u-1",
            tenant_id="t-1",
            allow_tenant_context=False,
        )
        response = SimpleNamespace(
            message="approval created",
            base_message="approval created",
            tenant_id=None,
            status="ok",
        )
        collected = [
            ChannelAttachment(
                filename="input.pdf",
                content_type="application/pdf",
                size_bytes=4,
                content_bytes=b"%PDF",
            )
        ]

        with patch.object(bot_shared, "handle_operly_command", AsyncMock(return_value=False)), patch.object(
            bot_shared, "server_tenant", AsyncMock(return_value=None)
        ), patch.object(bot_shared, "addressed_to_operly", return_value=True), patch.object(
            bot_shared, "session_scope", _fake_session_scope
        ), patch.object(
            bot_shared.ChannelService, "resolve", AsyncMock(return_value=resolution)
        ), patch.object(
            bot_shared, "collect_discord_attachments", AsyncMock(return_value=collected)
        ) as collect, patch.object(
            bot_shared.ChannelService, "handle", AsyncMock(return_value=response)
        ) as handle, patch.object(
            bot_shared, "send_discord_response", AsyncMock(return_value=SimpleNamespace())
        ), patch.object(
            bot_shared, "schedule_new_pending_jobs", AsyncMock()
        ):
            await bot_shared.on_message(message)

        collect.assert_awaited_once_with(message)
        handle.assert_awaited_once()
        envelope = handle.await_args.args[0]
        self.assertEqual(envelope.attachments, collected)


if __name__ == "__main__":
    unittest.main()
