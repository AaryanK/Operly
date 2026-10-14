from __future__ import annotations
import os
import unittest
from unittest.mock import patch
from packages.agent_runtime.inference import InferenceRoute, AgentInferenceError

class DragonZpyderApiCutoverTests(unittest.TestCase):
    def test_private_api_railway_route(self):
        with patch.dict(os.environ,{
            "OPERLY_AGENT_MODEL_PROVIDER":"dragonzpyder",
            "DRAGONZPYDER_RUNTIME_TOKEN":"test-value",
            "DRAGONZPYDER_INFERENCE_BASE_URL":"http://dragonzpyder-api.railway.internal/internal/v1"
        }):
            route=InferenceRoute.from_environment()
        self.assertEqual(route.base_url,"http://dragonzpyder-api.railway.internal/internal/v1")
    def test_public_api_host(self):
        with patch.dict(os.environ,{
            "OPERLY_AGENT_MODEL_PROVIDER":"dragonzpyder",
            "DRAGONZPYDER_RUNTIME_TOKEN":"test-value",
            "DRAGONZPYDER_INFERENCE_BASE_URL":"https://api.dragonzpyder.xyz/internal/v1"
        }):
            route=InferenceRoute.from_environment()
        self.assertEqual(route.base_url,"https://api.dragonzpyder.xyz/internal/v1")
    def test_unsafe_api_destination_rejected(self):
        for url in ("https://other.example/internal/v1","http://api.dragonzpyder.xyz/internal/v1",
                    "http://dragonzpyder-api.railway.internal.attacker.example/internal/v1",
                    "https://api.dragonzpyder.xyz/internal/v1?redirect=https://evil.example"):
            with self.subTest(url=url), patch.dict(os.environ,{
                "OPERLY_AGENT_MODEL_PROVIDER":"dragonzpyder",
                "DRAGONZPYDER_RUNTIME_TOKEN":"test-value",
                "DRAGONZPYDER_INFERENCE_BASE_URL":url,
            }):
                with self.assertRaises(AgentInferenceError):
                    InferenceRoute.from_environment()
