import json
import unittest

from alembic.config import Config
from alembic.script import ScriptDirectory

from packages.database.model_trace import encode_trace_envelope, redact_trace_value
from packages.database.schema import ALEMBIC_HEAD
from packages.model_runtime.trace_context import (
    ProviderWireEvent,
    current_trace_metadata,
    emit_provider_wire_event,
    register_provider_wire_telemetry_sink,
    runtime_trace_scope,
)


class RuntimeTraceContractTests(unittest.IsolatedAsyncioTestCase):
    def test_trace_redacts_credentials_and_hidden_reasoning_but_preserves_prompt_content(self):
        value = redact_trace_value(
            {
                "messages": [
                    {"role": "user", "content": "debug this request"},
                    {
                        "role": "assistant",
                        "content": "visible answer",
                        "reasoning_details": "hidden provider reasoning",
                    },
                ],
                "authorization": "Bearer abcdefghijklmnop",
                "nested": {"api_key": "sk-example-secret-key-123456"},
            }
        )
        self.assertEqual(value["messages"][0]["content"], "debug this request")
        self.assertEqual(value["messages"][1]["content"], "visible answer")
        self.assertEqual(
            value["messages"][1]["reasoning_details"],
            "[REDACTED_HIDDEN_REASONING]",
        )
        self.assertEqual(value["authorization"], "[REDACTED]")
        self.assertEqual(value["nested"]["api_key"], "[REDACTED]")

    def test_trace_envelope_keeps_large_model_visible_payload_complete(self):
        # This intentionally exceeds the former 4M trace cap. AI Debug must be able
        # to answer what the model actually received, not replace it with a summary.
        model_visible_content = "context-segment-" * 300_000
        encoded = encode_trace_envelope(
            {
                "input": {
                    "messages": [
                        {"role": "system", "content": model_visible_content},
                        {"role": "user", "content": "Use all supplied context."},
                    ],
                    "tools": [{"type": "function", "function": {"name": "lookup", "description": "test"}}],
                },
                "authorization": "Bearer abcdefghijklmnop",
            }
        )
        envelope = json.loads(encoded)
        self.assertNotIn("truncated", envelope)
        self.assertEqual(envelope["payload"]["input"]["messages"][0]["content"], model_visible_content)
        self.assertEqual(envelope["payload"]["input"]["tools"][0]["function"]["name"], "lookup")
        self.assertEqual(envelope["payload"]["authorization"], "[REDACTED]")

    def test_nested_trace_context_inherits_conversation_and_run(self):
        self.assertEqual(current_trace_metadata(), {})
        with runtime_trace_scope({"conversation_id": "conversation-1", "runtime_run_id": "run-1"}):
            with runtime_trace_scope({"runtime_component": "task_router", "runtime_step": 2}):
                metadata = current_trace_metadata()
                self.assertEqual(metadata["conversation_id"], "conversation-1")
                self.assertEqual(metadata["runtime_run_id"], "run-1")
                self.assertEqual(metadata["runtime_component"], "task_router")
                self.assertEqual(metadata["runtime_step"], 2)
        self.assertEqual(current_trace_metadata(), {})

    async def test_provider_wire_telemetry_preserves_trace_correlation(self):
        observed = []

        async def sink(event):
            observed.append(event)

        register_provider_wire_telemetry_sink(sink)
        with runtime_trace_scope(
            {
                "conversation_id": "conversation-wire",
                "runtime_run_id": "run-wire",
                "runtime_step": 3,
            }
        ):
            await emit_provider_wire_event(
                ProviderWireEvent(
                    phase="request",
                    wire_call_id="wire-1",
                    provider="test-provider",
                    provider_model_id="test-model",
                    payload={"url": "https://provider.test/chat", "body": {"model": "test-model"}},
                    metadata=current_trace_metadata(),
                )
            )

        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0].metadata["conversation_id"], "conversation-wire")
        self.assertEqual(observed[0].metadata["runtime_run_id"], "run-wire")
        self.assertEqual(observed[0].metadata["runtime_step"], 3)

    def test_runtime_schema_head_matches_alembic_head(self):
        script = ScriptDirectory.from_config(Config("alembic.ini"))
        self.assertEqual(script.get_current_head(), ALEMBIC_HEAD)


if __name__ == "__main__":
    unittest.main()
