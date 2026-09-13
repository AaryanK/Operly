"""Database-backed runtime contract tests; external runner/HTTP boundaries are mocked."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.database.digital_job_models import DigitalPlatformJobRecord
from packages.database.db import Base
from packages.database.schema import import_all_models
from packages.database.plugin_platform_models import (
    PluginPackageRecord, PluginVersionRecord, PluginInstallationRecord,
    PluginRuntimeInstanceRecord, DigitalEventOutboxRecord,
)
from packages.plugins.contracts import PluginManifest
from packages.plugins.runtime_provider import PluginRuntimeProvider
from packages.plugins.runtime_support import manifest_runtime_support, UnsupportedPluginRuntime
from packages.plugins.service import plugin_platform
from packages.plugins.capability_source import installed_plugin_capability_source
from packages.plugins.router import plugin_foundation, runtime_profiles
from packages.plugins.runtime_router import runtime_status, reconcile_runtime, ReconcileRuntimeInput
from packages.plugins.runtime_reconciler import PluginRuntimeReconciler, RuntimeReconciliationError
from packages.plugins.worker import validate_plugin_job, isolated_validate_plugin_job, PermanentPlatformJobError, PlatformWorker, _manifest_digest
from test_plugin_sandbox_reconciler import sandbox_manifest

MODES = {
    "remote_http": ("remote-http", "remote"),
    "sandbox_job": ("sandbox-job", "job"),
    "web_service": ("python-fastapi", "web"),
    "worker": ("worker", "worker"),
    "static_site": ("react-vite", "static"),
}
SUPPORTED = {"remote_http", "sandbox_job"}


def manifest_for(mode):
    raw = json.loads(json.dumps(sandbox_manifest().to_dict()))
    raw["execution_mode"] = mode
    raw["capabilities"][0]["id"] = "test." + mode + ".echo"
    raw["runtime"]["profile"], raw["runtime"]["kind"] = MODES[mode]
    if mode == "remote_http":
        raw["runtime"]["network"] = {"mode": "egress", "allowed_hosts": ["runtime.example.com"]}
    return PluginManifest.from_dict(raw)


class RuntimeSupportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import_all_models()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        # Only plugin tables are needed. Ownership is exercised with scoped queries;
        # this fixture is not a test of foreign keys or PostgreSQL locking.
        async with self.engine.begin() as connection:
            await connection.run_sync(lambda c: Base.metadata.create_all(c, tables=[
                model.__table__ for model in (PluginPackageRecord, PluginVersionRecord,
                    PluginInstallationRecord, PluginRuntimeInstanceRecord, DigitalEventOutboxRecord, DigitalPlatformJobRecord)
            ]))
        self.db = async_sessionmaker(self.engine, expire_on_commit=False)()
        self.auth = SimpleNamespace(role="owner", tenant=SimpleNamespace(id="workspace"), user=SimpleNamespace(id="user"))

    async def asyncTearDown(self):
        await self.db.close()
        await self.engine.dispose()

    async def seed(self, mode):
        manifest = manifest_for(mode)
        package = PluginPackageRecord(id=mode, namespace="workspace", plugin_id=mode,
            display_name=mode, owner_tenant_id="workspace")
        version = PluginVersionRecord(id=mode, package_id=mode, version="1.0.0",
            manifest_json=json.dumps(manifest.to_dict()), manifest_digest=_manifest_digest(manifest.to_dict()),
            validation_status="passed", validation_report_json="{}")
        installation = PluginInstallationRecord(id=mode, tenant_id="workspace", package_id=mode,
            version_id=mode, status="active", enabled=True)
        self.db.add_all([package, version, installation])
        await self.db.flush()
        return installation, version, manifest

    async def test_advertisement_and_historical_parser_agree_without_erasing_modes(self):
        foundation = await plugin_foundation(self.auth)
        self.assertEqual(set(foundation["digital_workload_classes"]), SUPPORTED)
        profiles = (await runtime_profiles(self.auth))["profiles"]
        for mode in MODES:
            with self.subTest(mode=mode):
                manifest = manifest_for(mode)
                roundtrip = PluginManifest.from_dict(json.loads(json.dumps(manifest.to_dict())))
                self.assertEqual(roundtrip.execution_mode.value, mode)
                support = manifest_runtime_support(roundtrip)
                self.assertEqual(support["supported"], mode in SUPPORTED)
                profile = next(p for p in profiles if p["id"] == MODES[mode][0])
                self.assertEqual(profile["runtime_support"], support)
                self.assertEqual(next(s for s in foundation["runtime_support"] if s["execution_mode"] == mode), support)
                if mode not in SUPPORTED:
                    self.assertFalse(profile["supports_deploy"])
                    self.assertFalse(profile["supports_preview"])

    async def test_unsupported_publish_has_no_database_side_effects(self):
        for mode in MODES.keys() - SUPPORTED:
            with self.subTest(mode=mode), self.assertRaisesRegex(UnsupportedPluginRuntime, "unsupported_runtime_mode"):
                await plugin_platform.publish_workspace_version(self.db, tenant_id="workspace", user_id="user",
                    manifest_payload=json.loads(json.dumps(manifest_for(mode).to_dict())))
        self.assertEqual(await self.db.scalar(select(func.count()).select_from(PluginPackageRecord)), 0)

    async def test_historical_modes_inspectable_but_never_deployable_or_discoverable(self):
        for mode in MODES.keys() - SUPPORTED:
            installation, version, manifest = await self.seed(mode)
            original = version.manifest_json
            self.db.add(PluginRuntimeInstanceRecord(tenant_id="workspace", installation_id=mode,
                version_id=mode, runtime_profile=MODES[mode][0], runtime_kind=MODES[mode][1],
                state="ready", health_state="healthy", provider="historical"))
            await self.db.flush()
            with self.subTest(mode=mode):
                status = await runtime_status(mode, self.auth, self.db)
                self.assertEqual(status["runtime_support"]["reason_code"], "unsupported_runtime_mode")
                self.assertEqual(status["instances"][0]["state"], "ready")
                with self.assertRaises(UnsupportedPluginRuntime):
                    await PluginRuntimeProvider()._resolve(self.db, context=SimpleNamespace(workspace_id="workspace"),
                        capability=manifest.capability_specs()[0], require_fresh=False)
                with self.assertRaises(UnsupportedPluginRuntime):
                    await plugin_platform.record_validation(self.db, tenant_id="workspace", version_id=mode, passed=True, report={})
                with self.assertRaises(HTTPException) as caught:
                    await reconcile_runtime(mode, ReconcileRuntimeInput(), self.auth, self.db)
                self.assertEqual(caught.exception.status_code, 422)
                self.assertEqual(caught.exception.detail, status["runtime_support"])
                with self.assertRaises(UnsupportedPluginRuntime):
                    await plugin_platform.install_version(self.db, tenant_id="workspace", user_id="user", version_id=mode)
                with self.assertRaises(UnsupportedPluginRuntime):
                    await plugin_platform._assert_activation_ready(self.db, installation=installation)
                with self.assertRaises(RuntimeReconciliationError) as caught:
                    await PluginRuntimeReconciler().reconcile(self.db, tenant_id="workspace", installation_id=mode)
                self.assertTrue(caught.exception.permanent)
                self.assertEqual(caught.exception.code, "unsupported_runtime_mode")
                self.assertEqual(version.manifest_json, original)
        rows = await plugin_platform.list_installations(self.db, tenant_id="workspace")
        self.assertEqual({r["execution_mode"] for r in rows}, MODES.keys() - SUPPORTED)
        self.assertTrue(all(not r["runtime_support"]["supported"] for r in rows))
        specs = await installed_plugin_capability_source.list(self.db, context=SimpleNamespace(workspace_id="workspace"))
        self.assertEqual(specs, ())
        self.assertEqual(await self.db.scalar(select(func.count()).select_from(PluginRuntimeInstanceRecord)), 3)

    async def test_old_validation_jobs_cannot_build_or_promote_unsupported_modes(self):
        for mode in MODES.keys() - SUPPORTED:
            _, version, _ = await self.seed(mode)
            job = SimpleNamespace(tenant_id="workspace", subject_kind="plugin_version", subject_id=mode, job_type="plugin.validate")
            for handler in (validate_plugin_job, isolated_validate_plugin_job):
                version.validation_status = "passed"  # Also guards the old already-passed fast path.
                with self.subTest(mode=mode, handler=handler.__name__), self.assertRaisesRegex(PermanentPlatformJobError, "unsupported_runtime_mode"):
                    await handler(self.db, job)
                self.assertEqual(version.validation_status, "failed")
                self.assertEqual(json.loads(version.validation_report_json)["runtime_support"]["reason_code"], "unsupported_runtime_mode")

    async def test_supported_modes_queue_and_reconcile_through_actual_dispatch(self):
        for mode in SUPPORTED:
            installation, version, manifest = await self.seed(mode)
            with self.subTest(mode=mode):
                enqueue = AsyncMock(return_value=SimpleNamespace(id="job", state="queued"))
                with patch("packages.plugins.runtime_router.digital_platform_jobs.enqueue", enqueue):
                    result = await reconcile_runtime(mode, ReconcileRuntimeInput(endpoint="https://runtime.example.com"), self.auth, self.db)
                self.assertTrue(result["accepted"])
                enqueue.assert_awaited_once()
                if mode == "remote_http":
                    job = SimpleNamespace(tenant_id="workspace", subject_kind="plugin_version", subject_id=mode, job_type="plugin.validate")
                    report = await validate_plugin_job(self.db, job)
                    self.assertFalse(report["isolated_build_required"])
                    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"ok")))
                    with patch("packages.plugins.runtime_reconciler.assert_public_runtime_host", AsyncMock(return_value=["93.184.216.34"])), patch("packages.plugins.runtime_reconciler.httpx.AsyncClient", return_value=client):
                        result = await PluginRuntimeReconciler().reconcile(self.db, tenant_id="workspace", installation_id=mode, endpoint="https://runtime.example.com")
                    self.assertEqual(result.provider, "remote-http")
                else:
                    version.validation_report_json = json.dumps({"validated_artifact_id": "artifact", "validated_artifact_digest": "a" * 64})
                    artifacts = SimpleNamespace(assert_workspace_artifact=AsyncMock(return_value=SimpleNamespace(id="artifact", sha256="a" * 64)))
                    runner = SimpleNamespace(health=AsyncMock(return_value={"ok": True}))
                    with patch("packages.plugins.runtime_reconciler.ArtifactService", return_value=artifacts):
                        result = await PluginRuntimeReconciler(runner=runner).reconcile(self.db, tenant_id="workspace", installation_id=mode)
                    self.assertEqual(result.provider, "railway-sandbox-job")
                self.assertEqual(result.state, "ready")
                self.assertEqual(result.health_state, "healthy")
                await plugin_platform._assert_activation_ready(self.db, installation=installation)
                # Discovery uses exactly the same supported contract as the API.
                specs = await installed_plugin_capability_source.list(self.db, context=SimpleNamespace(workspace_id="workspace"))
                self.assertTrue(specs)
                installation.enabled = False
                await self.db.flush()

    async def test_supported_mode_cannot_borrow_an_unimplemented_profile(self):
        raw = json.loads(json.dumps(manifest_for("remote_http").to_dict()))
        raw["runtime"]["profile"] = "python-fastapi"
        manifest = PluginManifest.from_dict(raw)
        self.assertEqual(manifest_runtime_support(manifest)["reason_code"], "runtime_profile_mode_mismatch")
        with self.assertRaisesRegex(UnsupportedPluginRuntime, "runtime_profile_mode_mismatch"):
            await plugin_platform.publish_workspace_version(self.db, tenant_id="workspace", user_id="user", manifest_payload=raw)

    async def test_supported_publish_enters_normal_validation_pipeline(self):
        for mode in sorted(SUPPORTED):
            raw = json.loads(json.dumps(manifest_for(mode).to_dict()))
            raw["plugin_id"] = "test." + mode
            artifacts = SimpleNamespace(assert_workspace_artifact=AsyncMock(return_value=SimpleNamespace(sha256="a" * 64)))
            with patch("packages.plugins.service.ArtifactService", return_value=artifacts), patch("packages.plugins.worker.ArtifactService", return_value=artifacts):
                _, version, _ = await plugin_platform.publish_workspace_version(self.db,
                    tenant_id="workspace", user_id="user", manifest_payload=raw,
                    package_artifact_id="source" if mode == "sandbox_job" else None)
                job = await self.db.scalar(select(DigitalPlatformJobRecord).where(DigitalPlatformJobRecord.subject_id == version.id))
                self.assertEqual(job.job_type, "plugin.validate")
                report = await validate_plugin_job(self.db, job)
                self.assertTrue(report["runtime_support"]["supported"])
                self.assertEqual(version.validation_status, "passed" if mode == "remote_http" else "awaiting_isolated_build")
                if mode == "sandbox_job":
                    build_job = await self.db.get(DigitalPlatformJobRecord, report["isolated_validation_job_id"])
                    self.assertEqual(build_job.job_type, "plugin.isolated_validate")

    async def test_worker_persists_explicit_permanent_failure_for_historical_reconcile(self):
        worker = PlatformWorker()
        for mode in sorted(MODES.keys() - SUPPORTED):
            await self.seed(mode)
            job = DigitalPlatformJobRecord(id=mode, tenant_id="workspace", idempotency_scope="workspace",
                job_type="plugin.runtime.reconcile", subject_kind="plugin_installation", subject_id=mode,
                payload_json=json.dumps({"installation_id": mode}), idempotency_key=mode,
                state="running", locked_by=worker.worker_id, attempt=1)
            self.db.add(job)
            await self.db.commit()
            with patch("packages.plugins.worker.SessionFactory", async_sessionmaker(self.engine, expire_on_commit=False)):
                await worker._process_job(mode)
            await self.db.refresh(job)
            self.assertEqual(job.state, "failed")
            self.assertIn("unsupported_runtime_mode", job.last_error)
            self.assertIn(mode, job.last_error)
            self.assertIsNone(job.locked_by)

    async def test_foundation_retains_configuration_aware_agent_status(self):
        for enabled, configured in ((False, False), (True, False), (True, True)):
            with patch("packages.plugins.router.agent_runtime_status", return_value={"enabled": enabled, "configured": configured}):
                foundation = await plugin_foundation(self.auth)
            self.assertEqual(foundation["ai_runtime_enabled"], enabled and configured)
            self.assertFalse(foundation["ai_runtime_required"])
