import os
import unittest
from unittest.mock import patch

from packages.connectors.runtime import ConnectorRuntime


class ConnectorRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_is_off_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            runtime = ConnectorRuntime()
            self.assertFalse(runtime.enabled)
            await runtime.start()
            self.assertEqual(runtime._tasks, [])
            await runtime.stop()

    async def test_embedded_runtime_can_start_without_optional_adapters(self):
        with patch.dict(
            os.environ,
            {"OPERLY_CONNECTOR_RUNTIME": "embedded"},
            clear=True,
        ):
            runtime = ConnectorRuntime()
            self.assertTrue(runtime.enabled)
            await runtime.start()
            self.assertEqual(runtime._tasks, [])
            await runtime.stop()

    async def test_explicit_mode_overrides_environment(self):
        with patch.dict(
            os.environ,
            {"OPERLY_CONNECTOR_RUNTIME": "off"},
            clear=True,
        ):
            self.assertTrue(ConnectorRuntime(mode="embedded").enabled)
            self.assertFalse(ConnectorRuntime(mode="off").enabled)


if __name__ == "__main__":
    unittest.main()
