import json
import os
import unittest
from unittest.mock import patch

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.api.custom_software_router import _validated_preview_target
from packages.custom_software.runner_adapters import FakeRunnerAdapter
from packages.custom_software.runner_contracts import BuildSubmission, HealthCheck, NetworkPolicy
from packages.custom_software.runner_service import (
    RunnerStateError,
    _event,
    active_preview,
    apply_runner_response,
    build_events,
    owned_build,
    stop_preview,
)
from packages.custom_software.source_bundles import BundlePolicyError, SourceFile, build_bundle
from packages.database.custom_software_models import (
    GeneratedSourceBundle,
    RunnerBuildRecord,
    RunnerPreviewRecord,
    SoftwarePlanRecord,
)
from packages.database.db import Base
from packages.database.models import AppUser, Tenant, TenantMember


class BundleAndPolicyTests(unittest.TestCase):
    def test_bundle_is_deterministic_versioned_and_traceable(self):
        files = [
            SourceFile("app.py", b"print('hello')\n", "coding-harness:test"),
            SourceFile("tests/test_app.py", b"def test_ok(): assert True\n", "coding-harness:test"),
        ]
        args = ("w1", "a1", "p1", 1, 1, "sha256:" + "0" * 64)
        a = build_bundle(files, *args)
        b = build_bundle(list(reversed(files)), *args)
        self.assertEqual(a.digest, b.digest)
        self.assertEqual(a.manifest["files"], b.manifest["files"])
        self.assertTrue(all(item["generatedBy"] for item in a.manifest["files"]))

    def test_traversal_absolute_hidden_duplicate_size_and_secret_rejected(self):
        base = ("w", "a", "p", 1, 1, "sha256:" + "0" * 64)
        for path in ("../escape", "/host", "C:/host", ".ssh/key", "x\\y"):
            with self.subTest(path=path), self.assertRaises(BundlePolicyError):
                build_bundle([SourceFile(path, b"x", "test")], *base)
        with self.assertRaises(BundlePolicyError):
            build_bundle([SourceFile("a", b"x", "t"), SourceFile("a", b"y", "t")], *base)
        with self.assertRaises(BundlePolicyError):
            build_bundle([SourceFile("key.txt", b"BEGIN PRIVATE KEY", "t")], *base)

    def test_command_port_network_dependency_and_result_policies(self):
        common = dict(
            workspaceId="w",
            applicationId="a",
            planVersion=1,
            sourceVersion=1,
            stackId="python-stdlib-web",
            sourceBundleDigest="sha256:" + "a" * 64,
            operations=["build"],
            healthCheck=HealthCheck(),
            idempotencyKey="abcdefgh",
        )
        with self.assertRaises(ValidationError):
            BuildSubmission(**common, requiredPorts=[22])
        for host in (
            "169.254.169.254",
            "172.31.255.254",
            "::1",
            "fe80::1",
            "metadata.google.internal",
            "https://example.com/path",
        ):
            with self.subTest(host=host), self.assertRaises(ValidationError):
                NetworkPolicy(mode="approved_hosts", approvedHosts=[host])
        self.assertEqual(
            NetworkPolicy(mode="approved_hosts", approvedHosts=["api.example.com."]).approvedHosts,
            ["api.example.com"],
        )
        with self.assertRaises(ValidationError):
            BuildSubmission(**common, dependencies=[{"name": "../evil", "version": "1.0"}])

    def test_preview_target_requires_approved_runner_origin(self):
        from fastapi import HTTPException

        with patch.dict(
            os.environ,
            {"OPERLY_ENV": "production", "OPERLY_SANDBOX_PREVIEW_HOSTS": "preview.runner.example"},
            clear=True,
        ):
            self.assertEqual(
                _validated_preview_target("https://preview.runner.example/app"),
                "https://preview.runner.example/app",
            )
            for value in (
                "http://preview.runner.example",
                "https://169.254.169.254/latest",
                "https://evil.example",
            ):
                with self.subTest(value=value), self.assertRaises(HTTPException):
                    _validated_preview_target(value)


class RunnerServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.sessions() as db:
            self.tenant = Tenant(name="Runner", slug="runner")
            self.other = Tenant(name="Other", slug="other")
            self.user = AppUser(email="runner@test.local", password_hash="x", display_name="Runner")
            db.add_all([self.tenant, self.other, self.user])
            await db.flush()
            db.add(TenantMember(tenant_id=self.tenant.id, user_id=self.user.id, role="owner"))
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def seed_build(self, db, *, key="runner-boundary"):
        plan = SoftwarePlanRecord(
            tenant_id=self.tenant.id,
            prompt="Runtime boundary test",
            current_version=1,
            approved_version=1,
            status="approved",
            created_by=self.user.id,
        )
        db.add(plan)
        await db.flush()
        bundle = build_bundle(
            [SourceFile("app.py", b"print('hello')\n", "coding-harness:test")],
            self.tenant.id,
            "app-test",
            plan.id,
            1,
            1,
            "sha256:" + "0" * 64,
        )
        source = GeneratedSourceBundle(
            tenant_id=self.tenant.id,
            plan_id=plan.id,
            plan_version=1,
            source_version=1,
            application_id="app-test",
            bundle_digest=bundle.digest,
            manifest_json=json.dumps(bundle.manifest),
            files_json=json.dumps(
                [
                    {
                        "path": item.path,
                        "content": item.content.decode(),
                        "generatedBy": item.generated_by,
                    }
                    for item in bundle.files
                ]
            ),
            provenance_json=json.dumps({"generator": "coding-harness:test"}),
            created_by=self.user.id,
        )
        db.add(source)
        await db.flush()
        submission = BuildSubmission(
            workspaceId=self.tenant.id,
            applicationId="app-test",
            planVersion=1,
            sourceVersion=1,
            stackId="python-stdlib-web",
            sourceBundleDigest=bundle.digest,
            operations=[
                "stage_source",
                "static_analysis",
                "build",
                "test",
                "start",
                "health_check",
                "acceptance_test",
            ],
            healthCheck=HealthCheck(),
            network=NetworkPolicy(mode="loopback_only"),
            idempotencyKey=key,
        )
        build = RunnerBuildRecord(
            tenant_id=self.tenant.id,
            plan_id=plan.id,
            source_bundle_id=source.id,
            idempotency_key=key,
            state="queued",
            runner_implementation="fake_test_only",
            isolation_profile="none_fake",
            submission_json=submission.model_dump_json(),
            result_json="{}",
            created_by=self.user.id,
        )
        db.add(build)
        await db.flush()
        return build, submission, bundle

    async def test_fake_success_persists_lifecycle_and_cross_workspace_denied(self):
        async with self.sessions() as db:
            build, submission, bundle = await self.seed_build(db)
            runner = FakeRunnerAdapter()
            response = await runner.submit(submission, bundle)
            build = await apply_runner_response(db, build, response, submission)
            self.assertEqual(build.state, "preview_ready")
            events = await build_events(db, build)
            self.assertEqual(events[-1].state, "preview_ready")
            self.assertGreater(len(events), 8)
            with self.assertRaises(LookupError):
                await owned_build(db, self.other.id, build.id)
            identity = build.id
        async with self.sessions() as restarted:
            self.assertEqual((await owned_build(restarted, self.tenant.id, identity)).state, "preview_ready")

    async def test_failure_never_exposes_preview_and_invalid_transition_is_rejected(self):
        async with self.sessions() as db:
            build, submission, bundle = await self.seed_build(db, key="failure-boundary")
            response = await FakeRunnerAdapter("test_failure").submit(submission, bundle)
            build = await apply_runner_response(db, build, response, submission)
            self.assertEqual(build.state, "tests_failed")
            self.assertFalse(json.loads(build.result_json)["previewAvailable"])
            with self.assertRaises(RunnerStateError):
                await _event(db, build, "preview_ready")

    async def test_cancel_timeout_cleanup_transitions(self):
        async with self.sessions() as db:
            build, _, _ = await self.seed_build(db, key="cancel-boundary")
            await _event(db, build, "cancel_requested")
            await _event(db, build, "cancelled")
            await _event(db, build, "cleaning")
            await _event(db, build, "cleaned")
            await db.commit()
            self.assertEqual(build.state, "cleaned")

    async def test_preview_cleanup_remains_runner_owned(self):
        async with self.sessions() as db:
            build, submission, bundle = await self.seed_build(db, key="preview-cleanup")
            runner = FakeRunnerAdapter()
            build = await apply_runner_response(
                db,
                build,
                await runner.submit(submission, bundle),
                submission,
            )
            preview = await db.scalar(
                select(RunnerPreviewRecord).where(RunnerPreviewRecord.build_id == build.id)
            )
            same, _ = await active_preview(db, self.tenant.id, preview.id)
            self.assertEqual(same.id, preview.id)
            with self.assertRaises(LookupError):
                await active_preview(db, "another-workspace", preview.id)
            await stop_preview(db, preview, build, runner)
            self.assertEqual(build.state, "cleaned")


class LegacyRuntimeRetirementTests(unittest.TestCase):
    def test_runner_service_no_longer_authors_or_repairs_source(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        service = (root / "packages/custom_software/runner_service.py").read_text()
        self.assertNotIn("generated_sources", service)
        self.assertNotIn("def create_source", service)
        self.assertNotIn("def submit_build", service)
        self.assertNotIn("def request_repair", service)
        self.assertNotIn("points += 2", service)
        self.assertFalse((root / "packages/custom_software/generated_sources.py").exists())

    def test_old_agent_harness_is_removed_and_plugin_bridge_has_no_logic(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        harness = root / "packages/harness"
        for legacy in (
            "agent.py",
            "context.py",
            "permissions.py",
            "registry.py",
            "services.py",
            "tools",
        ):
            self.assertFalse((harness / legacy).exists(), legacy)
        shim = (harness / "plugins.py").read_text()
        self.assertIn("packages.plugins.extensions", shim)
        self.assertNotIn("class RuntimePluginRegistry", shim)
        task_routing = (root / "packages/model_runtime/task_routing.py").read_text()
        self.assertNotIn("packages.harness", task_routing)


@unittest.skipUnless(
    os.getenv("OPERLY_REAL_ISOLATION_RUNNER") == "1",
    "real container or microVM runner is not available in this environment",
)
class RealIsolationAcceptance(unittest.TestCase):
    def test_external_isolation_boundary(self):
        self.fail("Configure the real runner integration harness before enabling this gate")
