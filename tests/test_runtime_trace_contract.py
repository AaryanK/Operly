import unittest

from alembic.config import Config
from alembic.script import ScriptDirectory

from packages.database.model_trace import redact_trace_value
from packages.database.schema import ALEMBIC_HEAD
from packages.model_runtime.trace_context import current_trace_metadata, runtime_trace_scope


class RuntimeTraceContractTests(unittest.TestCase):
    def test_trace_redacts_credentials_but_preserves_prompt_content(self):
        value = redact_trace_value(
            {
                "messages": [{"role": "user", "content": "debug this request"}],
                "authorization": "Bearer abcdefghijklmnop",
                "nested": {"api_key": "sk-example-secret-key-123456"},
            }
        )
        self.assertEqual(value["messages"][0]["content"], "debug this request")
        self.assertEqual(value["authorization"], "[REDACTED]")
        self.assertEqual(value["nested"]["api_key"], "[REDACTED]")

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

    def test_runtime_schema_head_matches_alembic_head(self):
        script = ScriptDirectory.from_config(Config("alembic.ini"))
        self.assertEqual(script.get_current_head(), ALEMBIC_HEAD)


if __name__ == "__main__":
    unittest.main()
