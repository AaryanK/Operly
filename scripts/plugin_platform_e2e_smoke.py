from __future__ import annotations

import asyncio
import io
import json
import time
import zipfile
from uuid import uuid4

from sqlalchemy import delete, select

from packages.artifacts import ArtifactScope, ArtifactService
from packages.database.artifact_models import ArtifactRecord
from packages.database.db import SessionFactory, init_db
from packages.database.models import AppUser, Tenant, TenantMember
from packages.database.plugin_platform_models import PluginRuntimeInstanceRecord, PluginVersionRecord
from packages.kernel.contracts import RuntimeRequest
from packages.plugins.contracts import PluginLifecycleState
from packages.plugins.jobs import digital_platform_jobs
from packages.plugins.service import plugin_platform
from packages.security.execution_context import ExecutionContext, ScopeKind
from packages.security.surfaces import SurfaceKind
from packages.workspace_modules.tools.runtime import build_workspace_runtime

CAPABILITY_ID = "studio.runner.e2e.echo"


def build_package() -> tuple[dict, bytes]:
    manifest = {
        "schema_version": "operly.plugin/v1",
        "plugin_id": "studio.runner.e2e",
        "version": "1.0.0",
        "display_name": "Studio Runner E2E",
        "description": "Disposable Studio-style plugin used to verify Operly production sandbox-job lifecycle.",
        "execution_mode": "sandbox_job",
        "capabilities": [{
            "id": CAPABILITY_ID,
            "display_name": "Echo from Studio plugin",
            "description": "Return a deterministic result proving this installed plugin ran inside the production Sandbox Runner.",
            "input_schema": {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"], "additionalProperties": False},
            "output_schema": {"type": "object", "properties": {"message": {"type": "string"}, "runner": {"type": "string"}, "invocation_schema": {"type": "string"}}, "required": ["message", "runner", "invocation_schema"], "additionalProperties": False},
            "permissions": [], "risk": "read_only", "approval_required": False, "reversible": False,
            "aliases": ["studio plugin echo"], "emits": [], "tags": ["studio", "e2e", "sandbox"],
        }],
        "permissions": [],
        "configuration_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "runtime": {"profile": "sandbox-job", "kind": "job", "network": {"mode": "off", "allowed_hosts": []}, "resources": {"cpu_millicores": 500, "memory_mb": 768, "disk_mb": 2048, "max_runtime_seconds": 300, "max_concurrency": 1}},
        "storage": [], "credentials": [], "produces_events": [], "consumes_events": [], "requested_bindings": [], "ui": [],
        "metadata": {"source": "studio-e2e-smoke", "disposable": True},
    }
    runtime = '''from __future__ import annotations\nimport json, sys\npacket = json.load(sys.stdin)\narguments = packet.get("arguments") or {}\nprint(json.dumps({"result": {"message": str(arguments.get("message") or ""), "runner": "canonical-sandbox-job-ok", "invocation_schema": str(packet.get("schema") or "")}}, separators=(",", ":"), sort_keys=True))\n'''
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("operly.plugin.json", json.dumps(manifest, separators=(",", ":"), sort_keys=True))
        zf.writestr("operly_runtime.py", runtime)
    return manifest, buf.getvalue()


async def wait_for_validation(version_id: str, timeout: float = 240.0) -> None:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        async with SessionFactory() as db:
            row = await db.get(PluginVersionRecord, version_id)
            if row is None:
                raise RuntimeError("plugin version disappeared")
            if row.validation_status != last:
                print("VALIDATION_STATE", row.validation_status, flush=True)
                last = row.validation_status
            if row.validation_status == "passed":
                report = json.loads(row.validation_report_json or "{}")
                print("VALIDATION_PASSED", json.dumps({k: report.get(k) for k in ("validated_artifact_id", "runtime_profile", "isolated_validation", "supply_chain_state", "control_plane_execution")}, sort_keys=True), flush=True)
                return
            if row.validation_status == "failed":
                raise RuntimeError("plugin validation failed: " + row.validation_report_json)
        await asyncio.sleep(2)
    raise TimeoutError("timed out waiting for Platform Worker validation")


async def wait_for_runtime(tenant_id: str, installation_id: str, timeout: float = 120.0) -> PluginRuntimeInstanceRecord:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        async with SessionFactory() as db:
            row = await db.scalar(select(PluginRuntimeInstanceRecord).where(
                PluginRuntimeInstanceRecord.tenant_id == tenant_id,
                PluginRuntimeInstanceRecord.installation_id == installation_id,
            ).order_by(PluginRuntimeInstanceRecord.updated_at.desc()))
            if row is not None:
                print("RUNTIME_STATE", row.provider, row.state, row.health_state, flush=True)
                if row.state == "ready" and row.health_state == "healthy":
                    return row
                if row.state == "failed" or row.health_state == "unhealthy":
                    raise RuntimeError("runtime reconciliation failed: " + row.health_evidence_json)
        await asyncio.sleep(2)
    raise TimeoutError("timed out waiting for Platform Worker runtime reconciliation")


async def cleanup(tenant_id: str | None, user_id: str | None) -> None:
    async with SessionFactory() as db:
        try:
            if tenant_id:
                await db.execute(delete(ArtifactRecord).where(ArtifactRecord.tenant_id == tenant_id))
                await db.execute(delete(TenantMember).where(TenantMember.tenant_id == tenant_id))
                tenant = await db.get(Tenant, tenant_id)
                if tenant is not None:
                    await db.delete(tenant)
            if user_id:
                user = await db.get(AppUser, user_id)
                if user is not None:
                    await db.delete(user)
            await db.commit()
            print("E2E_CLEANUP=PASS", flush=True)
        except Exception as exc:
            await db.rollback()
            print("E2E_CLEANUP=FAILED", repr(exc), flush=True)


async def main() -> None:
    await init_db()
    manifest, package_bytes = build_package()
    tenant_id = user_id = None
    suffix = uuid4().hex[:12]
    try:
        async with SessionFactory() as db:
            tenant = Tenant(name=f"Plugin E2E Smoke {suffix}", slug=f"plugin-e2e-{suffix}", timezone="UTC")
            user = AppUser(email=f"plugin-e2e-{suffix}@example.invalid", display_name="Plugin E2E Smoke", active=True)
            db.add_all([tenant, user]); await db.flush()
            tenant_id, user_id = tenant.id, user.id
            db.add(TenantMember(tenant_id=tenant.id, user_id=user.id, role="owner")); await db.flush()
            artifact = await ArtifactService(db).create_bytes(
                ArtifactScope("workspace", tenant.id, tenant_id=tenant.id), filename="studio-runner-e2e.zip",
                content=package_bytes, content_type="application/zip", source="plugin_e2e_smoke", created_by=user.id,
                metadata={"disposable": True, "test": "studio_runner_e2e"},
            )
            package, version, _ = await plugin_platform.publish_workspace_version(
                db, tenant_id=tenant.id, user_id=user.id, manifest_payload=manifest,
                package_artifact_id=artifact.id, source_digest=artifact.sha256,
            )
            version_id = version.id
            await db.commit()
            print("PUBLISHED", package.plugin_id, version_id, artifact.sha256, flush=True)

        await wait_for_validation(version_id)

        async with SessionFactory() as db:
            installation = await plugin_platform.install_version(db, tenant_id=tenant_id, user_id=user_id, version_id=version_id, granted_permissions=[], configuration={})
            installation_id = installation.id
            await digital_platform_jobs.enqueue(
                db, tenant_id=tenant_id, job_type="plugin.runtime.reconcile", subject_kind="plugin_installation",
                subject_id=installation_id, idempotency_key=f"plugin.runtime.reconcile:e2e:{installation_id}", payload={}, priority=70, created_by=user_id,
            )
            await db.commit()
            print("INSTALLED_AND_RECONCILE_QUEUED", installation_id, flush=True)

        runtime_row = await wait_for_runtime(tenant_id, installation_id)
        print("RUNTIME_RECONCILED", json.dumps({"provider": runtime_row.provider, "state": runtime_row.state, "health_state": runtime_row.health_state, "artifact_id": runtime_row.artifact_id}, sort_keys=True), flush=True)

        async with SessionFactory() as db:
            await plugin_platform.set_installation_state(db, tenant_id=tenant_id, installation_id=installation_id, status=PluginLifecycleState.ACTIVE, enabled=True)
            await db.commit()
            print("PLUGIN_ACTIVATED", installation_id, flush=True)

        async with SessionFactory() as db:
            context = ExecutionContext(
                workspace_id=tenant_id, user_id=user_id, membership_id="e2e-owner", role="owner", permissions=frozenset(),
                channel="operly", surface=SurfaceKind.WEB, scope_kind=ScopeKind.WORKSPACE,
                focus_workspace_id=tenant_id, principal_id=f"user:{user_id}", workspace_mode="full",
            )
            runtime = build_workspace_runtime()
            available = await runtime.available_capabilities(db, context=context, query=CAPABILITY_ID, limit=20)
            ids = [item.id for item in available]
            print("CAPABILITY_DISCOVERED", CAPABILITY_ID in ids, ids, flush=True)
            if CAPABILITY_ID not in ids:
                raise RuntimeError("active plugin capability not discoverable through canonical Workspace runtime")
            response = await runtime.execute(db, context=context, request=RuntimeRequest(
                capability_id=CAPABILITY_ID, arguments={"message": "hello from canonical Operly"}, request_id=f"plugin-e2e-{suffix}",
            ))
            payload = response.as_dict()
            print("KERNEL_RESPONSE", json.dumps(payload, sort_keys=True, default=str), flush=True)
            result = dict(response.result or {})
            if not response.done or response.status != "completed":
                raise RuntimeError("canonical runtime did not complete plugin invocation")
            if result.get("runner") != "canonical-sandbox-job-ok" or result.get("invocation_schema") != "operly.sandbox-job-invocation/v1" or result.get("message") != "hello from canonical Operly":
                raise RuntimeError("unexpected plugin execution result: " + json.dumps(result, sort_keys=True))
            await db.commit()
        print("PLUGIN_PLATFORM_E2E=PASS", flush=True)
    finally:
        await cleanup(tenant_id, user_id)


if __name__ == "__main__":
    asyncio.run(main())
