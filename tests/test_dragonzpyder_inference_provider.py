from __future__ import annotations
import os
import unittest
from unittest.mock import patch
from packages.agent_runtime.inference import AgentInferenceError, InferenceRoute

class DragonZpyderInferenceProviderTests(unittest.TestCase):
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
