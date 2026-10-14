from __future__ import annotations
import os
import unittest
import httpx
from unittest.mock import patch
from packages.agent_runtime.inference import AgentInferenceError, InferenceRoute, OpenAICompatibleAgentModel

class DragonZpyderInferenceProviderTests(unittest.IsolatedAsyncioTestCase):
    def test_route_uses_dragonzpyder_service(self):
        with patch.dict(os.environ, {
            "OPERLY_AGENT_MODEL_PROVIDER": "dragonzpyder",
            "DRAGONZPYDER_RUNTIME_TOKEN": "test-token",
            "DRAGONZPYDER_INFERENCE_BASE_URL": "https://dragonzpyder.xyz/internal/v1",
            "OPERLY_AGENT_MODEL_ID": "openai/gpt-oss-120b",
        }):
            route = InferenceRoute.from_environment()
        self.assertEqual(route.provider, "dragonzpyder")
        self.assertEqual(route.base_url, "https://dragonzpyder.xyz/internal/v1")
        self.assertEqual(route.api_key, "test-token")
    def test_missing_secret_fails_closed(self):
        with patch.dict(os.environ, {"OPERLY_AGENT_MODEL_PROVIDER":"dragonzpyder","DRAGONZPYDER_RUNTIME_TOKEN":""}):
            with self.assertRaises(AgentInferenceError) as error:
                InferenceRoute.from_environment()
        self.assertEqual(error.exception.code, "inference_not_configured")
    def test_untrusted_gateway_origin_rejected(self):
        with patch.dict(os.environ, {
            "OPERLY_AGENT_MODEL_PROVIDER":"dragonzpyder",
            "DRAGONZPYDER_RUNTIME_TOKEN":"test-token",
            "DRAGONZPYDER_INFERENCE_BASE_URL":"https://evil.example/internal/v1",
        }):
            with self.assertRaises(AgentInferenceError):
                InferenceRoute.from_environment()
    def test_railway_private_origin_allowed(self):
        with patch.dict(os.environ, {
            "OPERLY_AGENT_MODEL_PROVIDER":"dragonzpyder",
            "DRAGONZPYDER_RUNTIME_TOKEN":"test-token",
            "DRAGONZPYDER_INFERENCE_BASE_URL":"http://dragonzpyder-web.railway.internal/internal/v1",
        }):
            self.assertEqual(InferenceRoute.from_environment().provider,"dragonzpyder")

    async def test_real_adapter_uses_authenticated_dragonzpyder_chat_endpoint(self):
        def backend(request: httpx.Request) -> httpx.Response:
            self.assertEqual(str(request.url), "https://dragonzpyder.xyz/internal/v1/chat/completions")
            self.assertEqual(request.headers.get("authorization"), "Bearer test-token")
            self.assertEqual(request.headers.get("content-type"), "application/json")
            return httpx.Response(
                200,
                json={"choices":[{"message":{"role":"assistant","content":"{\"ok\":true}"}}],
                      "usage":{"prompt_tokens":10,"completion_tokens":5}},
            )
        original = httpx.AsyncClient
        with patch.dict(os.environ, {
            "OPERLY_AGENT_MODEL_PROVIDER":"dragonzpyder",
            "DRAGONZPYDER_RUNTIME_TOKEN":"test-token",
            "DRAGONZPYDER_INFERENCE_BASE_URL":"https://dragonzpyder.xyz/internal/v1",
            "OPERLY_AGENT_MODEL_ID":"openai/gpt-oss-120b",
        }):
            with patch("packages.agent_runtime.inference.httpx.AsyncClient", side_effect=lambda **kwargs: original(transport=httpx.MockTransport(backend), **kwargs)):
                model = OpenAICompatibleAgentModel()
                result = await model._chat(system="Return JSON", user_payload={"request":"test"}, structured=True, max_tokens=160)
        self.assertEqual(result, '{"ok":true}')
