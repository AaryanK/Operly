"""Registry invariants and real backend dispatch with mocked infrastructure."""
import hashlib
import json
import unittest
import subprocess
import sys
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import async_sessionmaker

from packages.database.digital_job_models import DigitalPlatformJobRecord
from packages.database.plugin_platform_models import PluginRuntimeInstanceRecord, PluginPackageRecord
from packages.plugins.contracts import PluginExecutionMode as Mode, PluginManifest
from packages.plugins.runtime_registry import (
    RuntimeAdapter, RuntimeAdapterRegistry, RemoteHttpRuntimeAdapter, SandboxJobRuntimeAdapter,
    get_runtime_registry,
)
from packages.plugins.runtime_support import (
    require_supported_runtime, manifest_runtime_support, runtime_profile_support, UnsupportedPluginRuntime,
)
from packages.plugins.runtime_provider import PluginRuntimeProvider
from packages.plugins.sandbox_job_runtime import SandboxJobPluginRuntimeProvider
from packages.plugins.runtime_reconciler import PluginRuntimeReconciler, RuntimeReconciliationError
from packages.plugins.router import plugin_foundation, runtime_profiles
from packages.plugins.runtime_router import runtime_status, reconcile_runtime, ReconcileRuntimeInput
from packages.plugins.service import plugin_platform
from packages.plugins.worker import PlatformWorker
from packages.plugins.capability_source import installed_plugin_capability_source
import test_plugin_runtime_support as support_tests
from test_plugin_runtime_support import manifest_for


class RegistryTests(unittest.TestCase):
    def test_registration_requires_complete_concrete_implementations(self):
        with self.assertRaises(TypeError):
            RuntimeAdapter()
        with self.assertRaisesRegex(ValueError, "implementations"):
            RuntimeAdapterRegistry([SimpleNamespace(execution_mode=Mode.REMOTE_HTTP)])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            RuntimeAdapterRegistry([RemoteHttpRuntimeAdapter(), RemoteHttpRuntimeAdapter()])
        with self.assertRaisesRegex(ValueError, "reserved"):
            RuntimeAdapterRegistry([replace(RemoteHttpRuntimeAdapter(), execution_mode=Mode.PLATFORM_NATIVE)])
        with self.assertRaisesRegex(ValueError, "profile/kind"):
            RuntimeAdapterRegistry([replace(RemoteHttpRuntimeAdapter(), compatible_pairs=())])

    def test_registry_is_immutable_and_imports_without_loading_execution_backends(self):
        registry = get_runtime_registry()
        with self.assertRaises(FrozenInstanceError):
            registry._by_mode = {}
        with self.assertRaises(TypeError):
            registry._by_mode[Mode.REMOTE_HTTP] = SandboxJobRuntimeAdapter()
        check = subprocess.run([sys.executable, "-c", (
            "import sys; from packages.plugins.runtime_registry import get_runtime_registry; "
            "assert 'packages.plugins.runtime_provider' not in sys.modules; "
            "assert 'packages.plugins.runtime_reconciler' not in sys.modules; "
            "assert 'packages.plugins.sandbox_job_runtime' not in sys.modules; "
            "import packages.plugins.sandbox_job_runtime; import packages.plugins.runtime_reconciler"
        )], capture_output=True, text=True, timeout=30)
        self.assertEqual(check.returncode, 0, check.stderr)

    def test_compatibility_is_owned_by_adapter_not_enum_or_profile_catalog(self):
        registry = get_runtime_registry()
        for mode in (Mode.REMOTE_HTTP, Mode.SANDBOX_JOB):
            adapter = registry.resolve(mode)
            self.assertIsInstance(adapter, RuntimeAdapter)
            manifest = manifest_for(mode.value)
            self.assertIs(require_supported_runtime(manifest), adapter)
            self.assertTrue(adapter.check_support(profile=manifest.runtime.profile, kind=manifest.runtime.kind))
            self.assertFalse(adapter.check_support(profile="wrong", kind=manifest.runtime.kind))
            self.assertFalse(adapter.check_support(profile=manifest.runtime.profile, kind="web"))
            for profile, kind in (("wrong", manifest.runtime.kind), (manifest.runtime.profile, "web")):
                support = registry.support(mode, profile=profile, kind=kind, check_profile=True)
                self.assertEqual(support["reason_code"], "runtime_profile_mode_mismatch")
        for mode in (Mode.WEB_SERVICE, Mode.WORKER, Mode.STATIC_SITE, Mode.PLATFORM_NATIVE):
            self.assertIsNone(registry.resolve(mode))
            self.assertFalse(registry.support(mode)["supported"])
        # A profile sharing a supported kind is not necessarily implemented.
        self.assertFalse(runtime_profile_support("unregistered-profile", "remote")["supported"])
        raw = json.loads(json.dumps(manifest_for("remote_http").to_dict()))
        raw["execution_mode"] = "platform_native"
        raw.pop("runtime")
        self.assertEqual(PluginManifest.from_dict(raw).execution_mode, Mode.PLATFORM_NATIVE)


class RegistryDatabaseTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = support_tests.RuntimeSupportTests.asyncSetUp
    asyncTearDown = support_tests.RuntimeSupportTests.asyncTearDown
    seed = support_tests.RuntimeSupportTests.seed

    async def ready(self, mode):
        installation, version, manifest = await self.seed(mode)
        instance = PluginRuntimeInstanceRecord(tenant_id="workspace", installation_id=mode, version_id=mode,
            runtime_profile=manifest.runtime.profile, runtime_kind=manifest.runtime.kind,
            state="ready", health_state="healthy", last_heartbeat_at=datetime.utcnow(),
            provider="remote-http" if mode == "remote_http" else "railway-sandbox-job",
            endpoint_reference="https://runtime.example.com", artifact_id="artifact" if mode == "sandbox_job" else None)
        self.db.add(instance)
        await self.db.flush()
        return installation, version, manifest, instance

    async def test_removing_registration_closes_every_boundary_even_for_healthy_rows(self):
        worker = PlatformWorker()
        for mode in ("remote_http", "sandbox_job"):
            installation, version, manifest, _ = await self.ready(mode)
            for job_type, subject in (("plugin.validate", "plugin_version"), ("plugin.isolated_validate", "plugin_version"), ("plugin.runtime.reconcile", "plugin_installation")):
                self.db.add(DigitalPlatformJobRecord(id=mode + job_type, tenant_id="workspace", idempotency_scope="workspace",
                    job_type=job_type, subject_kind=subject, subject_id=mode, idempotency_key=mode + job_type,
                    state="running", locked_by=worker.worker_id, attempt=1))
            await self.db.commit()
            with patch("packages.plugins.runtime_support.get_runtime_registry", return_value=RuntimeAdapterRegistry(())):
                self.assertFalse(manifest_runtime_support(manifest)["supported"])
                self.assertEqual((await plugin_foundation(self.auth))["digital_workload_classes"], [])
                for profile in (await runtime_profiles(self.auth))["profiles"]:
                    self.assertFalse(profile["runtime_support"]["supported"])
                    self.assertFalse(profile["supports_deploy"])
                status = await runtime_status(mode, self.auth, self.db)
                self.assertEqual(status["instances"][0]["state"], "ready")
                self.assertFalse(status["runtime_support"]["supported"])
                before = await self.db.scalar(select(func.count()).select_from(PluginPackageRecord))
                with self.assertRaises(UnsupportedPluginRuntime):
                    await plugin_platform.publish_workspace_version(self.db, tenant_id="workspace", user_id="user",
                        manifest_payload=json.loads(version.manifest_json), package_artifact_id="artifact")
                self.assertEqual(await self.db.scalar(select(func.count()).select_from(PluginPackageRecord)), before)
                with self.assertRaises(UnsupportedPluginRuntime):
                    await plugin_platform.install_version(self.db, tenant_id="workspace", user_id="user", version_id=mode)
                with self.assertRaises(UnsupportedPluginRuntime):
                    await plugin_platform._assert_activation_ready(self.db, installation=installation)
                with self.assertRaises(UnsupportedPluginRuntime):
                    await plugin_platform.record_validation(self.db, tenant_id="workspace", version_id=mode, passed=True, report={})
                with self.assertRaises(UnsupportedPluginRuntime):
                    await plugin_platform.record_runtime_instance(self.db, tenant_id="workspace", installation_id=mode,
                        runtime_profile=manifest.runtime.profile, runtime_kind=manifest.runtime.kind, state="ready", health_state="healthy")
                with self.assertRaises(HTTPException) as caught:
                    await reconcile_runtime(mode, ReconcileRuntimeInput(), self.auth, self.db)
                self.assertEqual(caught.exception.detail["reason_code"], "unsupported_runtime_mode")
                self.assertEqual(await installed_plugin_capability_source.list(self.db, context=SimpleNamespace(workspace_id="workspace")), ())
                with self.assertRaises(UnsupportedPluginRuntime):
                    await PluginRuntimeProvider().execute(self.db, context=SimpleNamespace(workspace_id="workspace"),
                        capability=manifest.capability_specs()[0], arguments={}, minimum_context={})
                # Independent sessions exercise the worker's permanent failure commit.
                for job_type in ("plugin.runtime.reconcile", "plugin.validate", "plugin.isolated_validate"):
                    with patch("packages.plugins.worker.SessionFactory", async_sessionmaker(self.engine, expire_on_commit=False)):
                        await worker._process_job(mode + job_type)
                    job = await self.db.get(DigitalPlatformJobRecord, mode + job_type)
                    await self.db.refresh(job)
                    self.assertEqual(job.state, "failed")
                    self.assertIn("unsupported_runtime_mode", job.last_error)
                    with patch("packages.plugins.worker.SessionFactory", async_sessionmaker(self.engine, expire_on_commit=False)):
                        await worker._process_job(mode + job_type)
                    await self.db.refresh(job)
                    self.assertEqual(job.attempt, 1)

    async def test_remote_adapter_executes_http_and_revokes_identity(self):
        _, _, manifest, _ = await self.ready("remote_http")
        provider = PluginRuntimeProvider()
        provider._issue_runtime_identity = AsyncMock(return_value=SimpleNamespace(token="test-token", expires_at=datetime.utcnow(), identity_id="identity"))
        provider._revoke_runtime_identity = AsyncMock()
        requests = []
        def transport(request):
            requests.append(request)
            return httpx.Response(200, json={"result": {"value": "remote"}})
        client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        with patch("packages.plugins.runtime_provider.assert_public_runtime_host", AsyncMock(return_value=["93.184.216.34"])), patch("packages.plugins.runtime_provider.httpx.AsyncClient", return_value=client):
            result = await provider.execute(self.db, context=SimpleNamespace(workspace_id="workspace"),
                capability=manifest.capability_specs()[0], arguments={"value": "remote"}, minimum_context={})
        self.assertEqual(result.value, {"value": "remote"})
        self.assertEqual(requests[0].method, "POST")
        self.assertEqual(json.loads(requests[0].content)["operly"]["runtime_token"], "test-token")
        provider._revoke_runtime_identity.assert_awaited_once_with(tenant_id="workspace", identity_id="identity")

    async def test_sandbox_adapter_preserves_injected_runner_and_isolation(self):
        _, _, manifest, _ = await self.ready("sandbox_job")
        payload = b"immutable validated zip bytes"
        digest = hashlib.sha256(payload).hexdigest()
        artifacts = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(id="artifact", sha256=digest)), read_bytes=AsyncMock(return_value=payload))
        runner = SimpleNamespace(start=AsyncMock(return_value={"id": "runtime"}), stop=AsyncMock(),
            tool=AsyncMock(side_effect=[{"sha256": digest}, {"exit_code": 0}, {}, {"exit_code": 0, "stdout": json.dumps({"result": {"value": "sandbox"}})}]))
        provider = SandboxJobPluginRuntimeProvider(runner=runner)
        available_slots = provider._execution_gate._value
        with patch("packages.plugins.sandbox_job_runtime.ArtifactService", return_value=artifacts):
            result = await provider.execute(self.db, context=SimpleNamespace(workspace_id="workspace", user_id="user"),
                capability=manifest.capability_specs()[0], arguments={}, minimum_context={})
        self.assertEqual(result.value, {"value": "sandbox"})
        self.assertEqual(runner.start.await_args.kwargs["network_policy"], "off")
        self.assertEqual(runner.tool.await_count, 4)
        runner.stop.assert_awaited_once_with("runtime")
        self.assertEqual(provider._execution_gate._value, available_slots)

    async def test_readiness_failure_does_not_remove_adapter_support(self):
        _, _, manifest, instance = await self.ready("remote_http")
        instance.last_heartbeat_at = datetime.utcnow() - timedelta(days=1)
        await self.db.flush()
        self.assertTrue(manifest_runtime_support(manifest)["supported"])
        self.assertFalse(await PluginRuntimeProvider().is_available(self.db, context=SimpleNamespace(workspace_id="workspace"), capability=manifest.capability_specs()[0]))
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: (_ for _ in ()).throw(httpx.ConnectError("offline"))))
        with patch("packages.plugins.runtime_reconciler.assert_public_runtime_host", AsyncMock(return_value=["93.184.216.34"])), patch("packages.plugins.runtime_reconciler.httpx.AsyncClient", return_value=client):
            with self.assertRaises(RuntimeReconciliationError) as caught:
                await PluginRuntimeReconciler().reconcile(self.db, tenant_id="workspace", installation_id="remote_http")
        self.assertFalse(caught.exception.permanent)
        self.assertTrue(manifest_runtime_support(manifest)["supported"])
