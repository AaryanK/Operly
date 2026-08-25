import unittest
from types import SimpleNamespace

from packages.capabilities.defaults import default_registry
from packages.capabilities.file_runtime_provider import FileRuntimeProvider


class FakeProcessor:
    class Limits:
        max_attachments = 10
        max_attachment_bytes = 1024 * 1024
        max_total_bytes = 2 * 1024 * 1024

    limits = Limits()

    def __init__(self):
        self.bundle = None

    async def process(self, bundle, temp_dir=None):
        self.bundle = bundle
        raise AssertionError("processor should not be reached for invalid transport")


class FileRuntimeProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_invalid_base64_before_processor_or_persistence(self):
        processor = FakeProcessor()
        provider = FileRuntimeProvider(processor)
        result = await provider.execute(
            SimpleNamespace(
                tenant_id="tenant-1",
                actor_id="user-1",
                scope_kind="workspace",
                scope_id="tenant-1",
                owner_user_id=None,
                db=None,
                invocation={"metadata": {"runtime_run_id": "run-test"}},
            ),
            "files.process",
            {"request": "Read it", "files": [{"filename": "bad.txt", "content_base64": "%%%"}]},
        )
        self.assertFalse(result.success)
        self.assertEqual(result.evidence["reason"], "file_processing_failed")
        self.assertIsNone(processor.bundle)

    def test_registered_in_canonical_runtime_for_agents_and_workflows(self):
        registry = default_registry()
        definition = registry.definition("files.process")
        self.assertEqual(definition.category, "files")
        self.assertEqual(definition.permissions, ("files:process",))
        self.assertEqual(registry.provider_name("files.process"), "operly_file_runtime")

        batch = registry.definition("files.batch_process")
        self.assertEqual(batch.category, "files")
        self.assertIn("process many files", batch.semantic_operations)
        self.assertEqual(registry.provider_name("files.batch_process"), "operly_file_runtime")

    def test_files_process_prefers_artifact_ids_and_does_not_return_binary_contract(self):
        registry = default_registry()
        schema = registry.definition("files.process").input_schema
        self.assertIn("artifact_ids", schema["properties"])
        # Generated file bytes are intentionally absent from the stable output
        # contract; results return durable artifact IDs instead.
        self.assertNotIn("content_base64", registry.definition("files.process").output_schema.get("properties", {}))


if __name__ == "__main__":
    unittest.main()
