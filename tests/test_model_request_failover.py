import unittest

from packages.model_runtime import InferenceRequest, InferenceResult, ModelInferenceError
from packages.model_runtime.registry import ModelPool


class _FailingModel:
    def __init__(self, model_id: str, *, provider: str, classification: str):
        self.id = model_id
        self.provider = provider
        self.tags = frozenset({"reliable"})
        self.capabilities = frozenset({"text", "tools"})
        self.traits = object()
        self.classification = classification
        self.calls = 0

    async def infer(self, request):
        del request
        self.calls += 1
        raise ModelInferenceError(
            f"{self.id} failed",
            classification=self.classification,
            retryable=False,
            provider=self.provider,
            model_id=self.id,
        )


class _SuccessModel:
    def __init__(self, model_id: str, *, provider: str):
        self.id = model_id
        self.provider = provider
        self.tags = frozenset({"reliable"})
        self.capabilities = frozenset({"text", "tools"})
        self.traits = object()
        self.calls = 0

    async def infer(self, request):
        del request
        self.calls += 1
        return InferenceResult(
            message={"role": "assistant", "content": "ok"},
            model_resource_id=self.id,
            provider=self.provider,
            provider_model_id=self.id,
            latency_ms=1,
            finish_reason="stop",
        )


class ModelRequestFailoverTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_specific_invalid_request_falls_through(self):
        first = _FailingModel(
            "tool-schema-incompatible",
            provider="provider-a",
            classification="invalid_request",
        )
        second = _SuccessModel("compatible", provider="provider-b")
        result = await ModelPool([first, second]).infer(
            InferenceRequest(
                messages=({"role": "user", "content": "use a tool"},),
                tools=({"type": "function", "function": {"name": "example"}},),
            )
        )
        self.assertEqual(result.provider_model_id, "compatible")
        self.assertEqual(first.calls, 1)
        self.assertEqual(second.calls, 1)

    async def test_auth_failure_cools_provider_and_uses_other_provider(self):
        first = _FailingModel("bad-auth-a", provider="provider-a", classification="auth")
        same_provider = _SuccessModel("would-work-a", provider="provider-a")
        other_provider = _SuccessModel("works-b", provider="provider-b")
        result = await ModelPool([first, same_provider, other_provider]).infer(
            InferenceRequest(messages=({"role": "user", "content": "hello"},))
        )
        self.assertEqual(result.provider_model_id, "works-b")
        self.assertEqual(first.calls, 1)
        self.assertEqual(same_provider.calls, 0)
        self.assertEqual(other_provider.calls, 1)


if __name__ == "__main__":
    unittest.main()
