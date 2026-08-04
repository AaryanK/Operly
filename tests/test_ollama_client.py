import os
import unittest
from unittest.mock import AsyncMock, patch

from packages.business_brain.ollama_client import OllamaClient, OllamaError


class OllamaClientTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = patch.dict(
            os.environ,
            {
                "OLLAMA_API_KEY": "test-key",
                "OLLAMA_URL": "https://example.invalid/api/chat",
                "OLLAMA_MODEL": "primary",
                "OLLAMA_FALLBACK_MODEL": "fallback",
                "OLLAMA_MAX_ATTEMPTS": "3",
            },
            clear=False,
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()

    async def test_retries_retryable_error_then_succeeds(self):
        client = OllamaClient()
        success = {"role": "assistant", "content": "OK"}
        client._request_once = AsyncMock(
            side_effect=[
                OllamaError("temporary", status=500, retryable=True),
                success,
            ]
        )

        with patch("asyncio.sleep", new=AsyncMock()):
            result = await client._chat_model(
                AsyncMock(), {}, "primary", [], [], attempts=3
            )

        self.assertEqual(result, success)
        self.assertEqual(client._request_once.await_count, 2)

    async def test_does_not_retry_auth_failure(self):
        client = OllamaClient()
        client._request_once = AsyncMock(
            side_effect=OllamaError("denied", status=401, retryable=False)
        )

        with self.assertRaises(OllamaError):
            await client._chat_model(
                AsyncMock(), {}, "primary", [], [], attempts=3
            )

        self.assertEqual(client._request_once.await_count, 1)

    async def test_public_error_preserves_reference(self):
        error = OllamaError(
            "Internal Server Error",
            status=500,
            reference="832be54b-63f4-4f05-99dd-6d80e86fb5fd",
            retryable=True,
        )
        self.assertIn("832be54b", error.public_message)
        self.assertNotIn("API", error.public_message)


if __name__ == "__main__":
    unittest.main()
