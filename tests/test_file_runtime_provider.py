import base64
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from packages.business_brain.attachments.models import GeneratedOutput, OutputFile
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
        path = Path(temp_dir or tempfile.mkdtemp()) / "result.txt"
        path.write_text("processed", encoding="utf-8")
        return GeneratedOutput(
            "Complete",
            [OutputFile(path, "result.txt", "text/plain", path.stat().st_size)],
            [],
            "extract",
            [item.filename for item in bundle.attachments],
            [],
        )


class FileRuntimeProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_processes_trusted_inline_files_and_returns_output(self):
        processor = FakeProcessor()
        provider = FileRuntimeProvider(processor)
        result = await provider.execute(
            SimpleNamespace(tenant_id="tenant-1", actor_id="user-1"),
            "files.process",
            {
                "request": "Extract this file",
                "output_format": "txt",
                "files": [
                    {
                        "filename": "notes.txt",
                        "content_type": "text/plain",
                        "content_base64": base64.b64encode(b"hello").decode("ascii"),
                    }
                ],
            },
        )
        self.assertTrue(result.success)
        self.assertFalse(result.changed)
        self.assertEqual(result.evidence["accepted"], ["notes.txt"])
        self.assertEqual(base64.b64decode(result.evidence["files"][0]["content_base64"]), b"processed")
        self.assertEqual(processor.bundle.tenant_id, "tenant-1")
        self.assertEqual(processor.bundle.actor_id, "user-1")

    async def test_rejects_invalid_base64_before_processor(self):
        processor = FakeProcessor()
        provider = FileRuntimeProvider(processor)
        result = await provider.execute(
            SimpleNamespace(tenant_id="tenant-1", actor_id="user-1"),
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


if __name__ == "__main__":
    unittest.main()
