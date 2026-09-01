import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from packages.database.plugin_platform_models import (
    DigitalEventOutboxRecord,
    PluginRuntimeInstanceRecord,
)
from packages.plugins.contracts import PluginManifest
from packages.plugins.runtime_reconciler import (
    PluginRuntimeReconciler,
    RuntimeReconciliationError,
)


def sandbox_manifest() -> PluginManifest:
    return PluginManifest.from_dict(
        {
            "schema_version": "operly.plugin/v1",
            "plugin_id": "test.sandbox",
            "version": "1.0.0",
            "display_name": "Sandbox test",
            "description": "Test sandbox job reconciliation.",
            "execution_mode": "sandbox_job",
            "permissions": [],
            "runtime": {
                "profile": "sandbox-job",
                "kind": "job",
                "network": {"mode": "off", "allowed_hosts": []},
                "resources": {
                    "cpu_millicores": 250,
                    "memory_mb": 256,
                    "disk_mb": 512,
                    "max_runtime_seconds": 120,
                    "max_concurrency": 1,
                },
            },
            "capabilities": [
                {
                    "id": "test.sandbox.echo",
                    "display_name": "Echo",
                    "description": "Echo a value.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"value": {"type": "string"}},
                        "required": ["value"],
                        "additionalProperties": False,
                    },
                    "output_schema": {
                        "type": "object",
                        "properties": {"value": {"type": "string"}},
                        "required": ["value"],
                    },
                    "permissions": [],
                    "risk": "read_only",
                }
            ],
        }
    )


class FakeDb:
    def __init__(self) -> None:
        self.added = []

    async def scalar(self, _query):
        return None

    def add(self, value) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        return None


class SandboxJobRuntimeReconciliationTests(unittest.IsolatedAsyncioTestCase):
    async def test_reconcile_promotes_validated_artifact_to_healthy_ephemeral_runtime(self):
        manifest = sandbox_manifest()
        installation = SimpleNamespace(id="installation-1", tenant_id="workspace-1")
        version = SimpleNamespace(
            id="version-1",
            validation_status="passed",
            validation_report_json=json.dumps(
                {
                    "validated_artifact_id": "artifact-validated",
                    "validated_artifact_digest": "a" * 64,
                    "source_artifact_id": "artifact-source",
                    "build_logs_artifact_id": "artifact-logs",
                }
            ),
        )
        runner = SimpleNamespace(
            health=AsyncMock(return_value={"ok": True, "service": "sandbox-runner"})
        )
        reconciler = PluginRuntimeReconciler(runner=runner)
        reconciler._context = AsyncMock(
            return_value=(installation, version, manifest)
        )
        artifact_service = SimpleNamespace(
            assert_workspace_artifact=AsyncMock(
                return_value=SimpleNamespace(
                    id="artifact-validated",
                    sha256="a" * 64,
                )
            )
        )
        db = FakeDb()

        with patch(
            "packages.plugins.runtime_reconciler.ArtifactService",
            return_value=artifact_service,
        ):
            result = await reconciler.reconcile_sandbox_job(
                db,
                tenant_id="workspace-1",
                installation_id="installation-1",
            )

        self.assertEqual(result.provider, "railway-sandbox-job")
        self.assertEqual(result.state, "ready")
        self.assertEqual(result.health_state, "healthy")
        self.assertIsNone(result.endpoint)
        runtime = next(
            item
            for item in db.added
            if isinstance(item, PluginRuntimeInstanceRecord)
        )
        self.assertEqual(runtime.artifact_id, "artifact-validated")
        self.assertEqual(runtime.provider, "railway-sandbox-job")
        self.assertEqual(runtime.provider_reference, "ephemeral-per-invocation")
        self.assertEqual(runtime.health_state, "healthy")
        self.assertEqual(
            json.loads(runtime.health_evidence_json)["execution_model"],
            "fresh_railway_sandbox_per_invocation",
        )
        event = next(
            item
            for item in db.added
            if isinstance(item, DigitalEventOutboxRecord)
        )
        self.assertEqual(event.event_type, "plugin.runtime.healthy")
        self.assertEqual(
            json.loads(event.payload_json)["artifact_id"],
            "artifact-validated",
        )
        runner.health.assert_awaited_once()

    async def test_reconcile_fails_closed_when_validation_artifact_is_missing(self):
        manifest = sandbox_manifest()
        installation = SimpleNamespace(id="installation-1", tenant_id="workspace-1")
        version = SimpleNamespace(
            id="version-1",
            validation_status="passed",
            validation_report_json=json.dumps({}),
        )
        reconciler = PluginRuntimeReconciler(
            runner=SimpleNamespace(health=AsyncMock(return_value={"ok": True}))
        )
        reconciler._context = AsyncMock(
            return_value=(installation, version, manifest)
        )

        with self.assertRaises(RuntimeReconciliationError) as caught:
            await reconciler.reconcile_sandbox_job(
                FakeDb(),
                tenant_id="workspace-1",
                installation_id="installation-1",
            )

        self.assertTrue(caught.exception.permanent)
        self.assertIn("promoted immutable artifact", str(caught.exception))

    async def test_reconcile_fails_closed_when_runner_is_unavailable(self):
        from packages.workspace_modules.agent_computer.sandbox import ComputerRunnerError

        manifest = sandbox_manifest()
        installation = SimpleNamespace(id="installation-1", tenant_id="workspace-1")
        version = SimpleNamespace(
            id="version-1",
            validation_status="passed",
            validation_report_json=json.dumps(
                {
                    "validated_artifact_id": "artifact-validated",
                    "validated_artifact_digest": "a" * 64,
                }
            ),
        )
        runner = SimpleNamespace(
            health=AsyncMock(side_effect=ComputerRunnerError("offline"))
        )
        reconciler = PluginRuntimeReconciler(runner=runner)
        reconciler._context = AsyncMock(
            return_value=(installation, version, manifest)
        )
        artifact_service = SimpleNamespace(
            assert_workspace_artifact=AsyncMock(
                return_value=SimpleNamespace(
                    id="artifact-validated",
                    sha256="a" * 64,
                )
            )
        )

        with patch(
            "packages.plugins.runtime_reconciler.ArtifactService",
            return_value=artifact_service,
        ):
            with self.assertRaises(RuntimeReconciliationError) as caught:
                await reconciler.reconcile_sandbox_job(
                    FakeDb(),
                    tenant_id="workspace-1",
                    installation_id="installation-1",
                )

        self.assertFalse(caught.exception.permanent)
        self.assertIn("Sandbox Runner is unavailable", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
