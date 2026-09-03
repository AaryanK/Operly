from __future__ import annotations

import asyncio
import hashlib
import json
import os
import socket
import traceback
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.artifacts import ArtifactService
from packages.database.db import SessionFactory, init_db
from packages.database.digital_event_models import DigitalEventDeliveryRecord
from packages.database.digital_job_models import DigitalPlatformJobRecord
from packages.database.plugin_platform_models import (
    DigitalEventOutboxRecord,
    DigitalEventSubscriptionRecord,
    PluginPackageRecord,
    PluginVersionRecord,
)
from packages.plugins.builds import IsolatedPluginValidationError, sandbox_plugin_validator
from packages.plugins.contracts import PluginExecutionMode, PluginManifest
from packages.plugins.deliveries import EventDeliveryError, digital_event_deliveries
from packages.plugins.events import digital_events
from packages.plugins.jobs import digital_platform_jobs
from packages.plugins.runtime_profiles import default_runtime_profiles
from packages.plugins.runtime_reconciler import (
    RuntimeReconciliationError,
    plugin_runtime_reconciler,
)


class PermanentPlatformJobError(RuntimeError):
    """A deterministic job failure that retrying cannot repair."""


JobHandler = Callable[[AsyncSession, DigitalPlatformJobRecord], Awaitable[dict[str, Any]]]


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _manifest_digest(manifest: dict[str, Any]) -> str:
    return hashlib.sha256(_json(manifest).encode("utf-8")).hexdigest()


def _event_matches(pattern: str, event_type: str) -> bool:
    clean = str(pattern or "").strip()
    if clean == "*":
        return True
    if clean.endswith(".*"):
        prefix = clean[:-1]
        return event_type.startswith(prefix)
    return clean == event_type


def _object(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


async def _plugin_version_context(
    db: AsyncSession,
    job: DigitalPlatformJobRecord,
) -> tuple[PluginVersionRecord, PluginPackageRecord, PluginManifest]:
    if not job.tenant_id or job.subject_kind != "plugin_version":
        raise PermanentPlatformJobError(
            f"{job.job_type} requires a Workspace plugin_version subject"
        )
    version = await db.get(PluginVersionRecord, job.subject_id)
    if version is None:
        raise PermanentPlatformJobError("Plugin version no longer exists")
    package = await db.get(PluginPackageRecord, version.package_id)
    if package is None or package.owner_tenant_id != job.tenant_id:
        raise PermanentPlatformJobError("Plugin package is unavailable to this Workspace")
    try:
        raw_manifest = json.loads(version.manifest_json)
        if not isinstance(raw_manifest, dict):
            raise ValueError("manifest is not an object")
        manifest = PluginManifest.from_dict(raw_manifest)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        version.validation_status = "failed"
        version.validation_report_json = _json(
            {"manifest_valid": False, "error": str(error)[:2000]}
        )
        raise PermanentPlatformJobError("Stored plugin manifest is invalid") from error
    return version, package, manifest


async def validate_plugin_job(
    db: AsyncSession,
    job: DigitalPlatformJobRecord,
) -> dict[str, Any]:
    version, _, manifest = await _plugin_version_context(db, job)

    calculated_digest = _manifest_digest(manifest.to_dict())
    if calculated_digest != version.manifest_digest:
        version.validation_status = "failed"
        version.validation_report_json = _json(
            {"manifest_valid": True, "manifest_digest_valid": False}
        )
        raise PermanentPlatformJobError(
            "Plugin manifest digest does not match immutable version identity"
        )
    if manifest.runtime is None:
        raise PermanentPlatformJobError("Workspace plugin runtime requirement is missing")

    profile = default_runtime_profiles().get(manifest.runtime.profile)
    if profile.kind != manifest.runtime.kind:
        raise PermanentPlatformJobError(
            "Plugin runtime kind does not match trusted runtime profile"
        )

    artifact_report: dict[str, Any] = {
        "package_artifact_id": version.package_artifact_id,
        "sbom_artifact_id": version.sbom_artifact_id,
        "package_sha256": None,
        "sbom_sha256": None,
    }
    artifacts = ArtifactService(db)
    if version.package_artifact_id:
        artifact = await artifacts.assert_workspace_artifact(
            tenant_id=job.tenant_id,
            artifact_id=version.package_artifact_id,
        )
        artifact_report["package_sha256"] = artifact.sha256
        if version.source_digest and version.source_digest.lower() != artifact.sha256.lower():
            raise PermanentPlatformJobError(
                "Declared source digest does not match package artifact"
            )
    elif manifest.execution_mode is not PluginExecutionMode.REMOTE_HTTP:
        raise PermanentPlatformJobError("Executable plugin package artifact is missing")

    if version.sbom_artifact_id:
        sbom = await artifacts.assert_workspace_artifact(
            tenant_id=job.tenant_id,
            artifact_id=version.sbom_artifact_id,
        )
        artifact_report["sbom_sha256"] = sbom.sha256

    report: dict[str, Any] = {
        "manifest_valid": True,
        "manifest_digest_valid": True,
        "runtime_profile": profile.id,
        "runtime_kind": profile.kind,
        "execution_mode": manifest.execution_mode.value,
        "artifact_identity": artifact_report,
        "validated_at": datetime.utcnow().isoformat(),
        "control_plane_execution": False,
    }
    if manifest.execution_mode is PluginExecutionMode.REMOTE_HTTP:
        version.validation_status = "passed"
        report["isolated_build_required"] = False
        report["supply_chain_state"] = "not_applicable_remote_http"
    else:
        version.validation_status = "awaiting_isolated_build"
        report["isolated_build_required"] = True
        report["supply_chain_state"] = "queued_for_isolated_build_and_scan"
        isolated_job = await digital_platform_jobs.enqueue(
            db,
            tenant_id=job.tenant_id,
            job_type="plugin.isolated_validate",
            subject_kind="plugin_version",
            subject_id=version.id,
            idempotency_key=f"plugin.isolated_validate:{version.id}:{version.manifest_digest}",
            payload={
                "manifest_digest": version.manifest_digest,
                "source_artifact_id": version.package_artifact_id,
                "runtime_profile": profile.id,
            },
            priority=60,
            max_attempts=5,
            created_by=version.created_by,
        )
        report["isolated_validation_job_id"] = isolated_job.id
    version.validation_report_json = _json(report)
    await db.flush()
    return report


async def isolated_validate_plugin_job(
    db: AsyncSession,
    job: DigitalPlatformJobRecord,
) -> dict[str, Any]:
    version, _, manifest = await _plugin_version_context(db, job)
    if manifest.execution_mode is PluginExecutionMode.REMOTE_HTTP:
        raise PermanentPlatformJobError(
            "Remote HTTP plugins do not have an isolated executable package"
        )
    if manifest.runtime is None:
        raise PermanentPlatformJobError("Workspace plugin runtime requirement is missing")
    if version.validation_status == "passed":
        existing = _object(version.validation_report_json)
        return existing or {"already_validated": True}
    if version.validation_status not in {"awaiting_isolated_build", "pending"}:
        raise PermanentPlatformJobError(
            f"Plugin version cannot enter isolated validation from {version.validation_status}"
        )

    profile = default_runtime_profiles().get(manifest.runtime.profile)
    if profile.kind != manifest.runtime.kind:
        raise PermanentPlatformJobError(
            "Plugin runtime kind does not match trusted runtime profile"
        )
    try:
        isolated = await sandbox_plugin_validator.validate(
            db,
            tenant_id=str(job.tenant_id),
            version=version,
            manifest=manifest,
            profile=profile,
        )
    except IsolatedPluginValidationError as error:
        if not error.permanent:
            raise
        report = _object(version.validation_report_json)
        report.update(
            {
                "isolated_validation": "failed",
                "supply_chain_state": "failed",
                "error": str(error)[:2000],
                "failed_at": datetime.utcnow().isoformat(),
                "control_plane_execution": False,
            }
        )
        version.validation_status = "failed"
        version.validation_report_json = _json(report)
        await db.flush()
        raise PermanentPlatformJobError(str(error)) from error

    report = _object(version.validation_report_json)
    report.update(
        {
            "isolated_validation": "passed",
            "supply_chain_state": "validated_in_isolated_sandbox",
            "validated_artifact_id": isolated.validated_artifact_id,
            "validated_artifact_digest": isolated.validated_artifact_digest,
            "build_logs_artifact_id": isolated.build_logs_artifact_id,
            "source_artifact_id": isolated.source_artifact_id,
            "source_digest": isolated.source_digest,
            "runtime_profile": isolated.runtime_profile,
            "file_count": isolated.file_count,
            "unpacked_bytes": isolated.unpacked_bytes,
            "build_commands_run": isolated.build_commands_run,
            "build_network_policy": isolated.build_network_policy,
            "isolated_evidence": isolated.evidence,
            "isolated_validated_at": datetime.utcnow().isoformat(),
            "control_plane_execution": False,
        }
    )
    version.validation_status = "passed"
    version.validation_report_json = _json(report)
    await db.flush()
    return report


async def reconcile_plugin_runtime_job(
    db: AsyncSession,
    job: DigitalPlatformJobRecord,
) -> dict[str, Any]:
    if not job.tenant_id or job.subject_kind != "plugin_installation":
        raise PermanentPlatformJobError(
            "plugin.runtime.reconcile requires a Workspace plugin_installation subject"
        )
    payload = _object(job.payload_json)
    endpoint = str(payload.get("endpoint") or "").strip() or None
    try:
        result = await plugin_runtime_reconciler.reconcile(
            db,
            tenant_id=job.tenant_id,
            installation_id=job.subject_id,
            endpoint=endpoint,
        )
    except RuntimeReconciliationError as error:
        if error.permanent:
            raise PermanentPlatformJobError(str(error)) from error
        raise
    return {
        "runtime_instance_id": result.runtime_instance_id,
        "state": result.state,
        "health_state": result.health_state,
        "provider": result.provider,
        "endpoint": result.endpoint,
        "evidence": result.evidence,
    }


DEFAULT_HANDLERS: dict[str, JobHandler] = {
    "plugin.validate": validate_plugin_job,
    "plugin.isolated_validate": isolated_validate_plugin_job,
    "plugin.runtime.reconcile": reconcile_plugin_runtime_job,
}


async def _run_hosting_e2e_once() -> None:
    try:
        from packages.plugins.hosting_e2e import main as hosting_e2e_main

        print("PLUGIN_HOSTING_E2E_WORKER_TRIGGER_START", flush=True)
        await hosting_e2e_main()
    except Exception as error:
        print(
            f"PLUGIN_HOSTING_E2E_WORKER_TRIGGER_FAILED {type(error).__name__}: {error}",
            flush=True,
        )
        traceback.print_exc()


class PlatformWorker:
    """One durable infrastructure dispatcher for Operly digital workloads.

    Handlers orchestrate trusted control-plane state. Untrusted plugin/build code must
    cross a runtime-controller/Sandbox Runner boundary; handlers must never import it.
    """

    def __init__(self) -> None:
        self.worker_id = (
            f"operly-platform:{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"
        )
        self.poll_seconds = max(
            0.25,
            min(float(os.getenv("OPERLY_PLATFORM_WORKER_POLL_SECONDS", "2")), 60.0),
        )
        self.lease_seconds = max(
            30,
            min(int(os.getenv("OPERLY_PLATFORM_WORKER_LEASE_SECONDS", "180")), 900),
        )
        self.batch_size = max(
            1,
            min(int(os.getenv("OPERLY_PLATFORM_WORKER_BATCH_SIZE", "10")), 100),
        )
        self.handlers = dict(DEFAULT_HANDLERS)

    async def _lease_jobs(self) -> list[str]:
        async with SessionFactory() as db:
            rows = await digital_platform_jobs.lease_batch(
                db,
                worker_id=self.worker_id,
                limit=self.batch_size,
                lease_seconds=self.lease_seconds,
            )
            ids = [row.id for row in rows]
            await db.commit()
            return ids

    async def _process_job(self, job_id: str) -> None:
        async with SessionFactory() as db:
            job = await db.get(DigitalPlatformJobRecord, job_id)
            if job is None or job.state != "running" or job.locked_by != self.worker_id:
                return
            handler = self.handlers.get(job.job_type)
            if handler is None:
                await digital_platform_jobs.fail(
                    db,
                    job_id=job.id,
                    worker_id=self.worker_id,
                    error=f"No trusted platform handler registered for {job.job_type}",
                    permanent=True,
                )
                await db.commit()
                return
            try:
                result = await handler(db, job)
                await digital_platform_jobs.complete(
                    db,
                    job_id=job.id,
                    worker_id=self.worker_id,
                    result=result,
                )
                await db.commit()
            except PermanentPlatformJobError as error:
                await digital_platform_jobs.fail(
                    db,
                    job_id=job.id,
                    worker_id=self.worker_id,
                    error=str(error),
                    permanent=True,
                )
                await db.commit()
            except Exception as error:
                await db.rollback()
                job = await db.get(DigitalPlatformJobRecord, job_id)
                if job is None or job.state != "running" or job.locked_by != self.worker_id:
                    return
                retry = min(30 * (2 ** max(0, job.attempt - 1)), 3600)
                await digital_platform_jobs.fail(
                    db,
                    job_id=job.id,
                    worker_id=self.worker_id,
                    error=f"{type(error).__name__}: {str(error)[:4000]}",
                    retry_after_seconds=retry,
                )
                await db.commit()

    async def _lease_events(self) -> list[str]:
        async with SessionFactory() as db:
            rows = await digital_events.lease_batch(
                db,
                worker_id=self.worker_id,
                limit=self.batch_size * 2,
                lease_seconds=self.lease_seconds,
            )
            ids = [row.id for row in rows]
            await db.commit()
            return ids

    async def _fanout_event(self, event_id: str) -> None:
        async with SessionFactory() as db:
            event = await db.get(DigitalEventOutboxRecord, event_id)
            if event is None or event.status != "leased" or event.locked_by != self.worker_id:
                return
            try:
                subscriptions = list(
                    (
                        await db.scalars(
                            select(DigitalEventSubscriptionRecord).where(
                                DigitalEventSubscriptionRecord.tenant_id == event.tenant_id,
                                DigitalEventSubscriptionRecord.enabled.is_(True),
                            )
                        )
                    ).all()
                )
                matched = [
                    item
                    for item in subscriptions
                    if _event_matches(item.event_pattern, event.event_type)
                ]
                for subscription in matched:
                    existing = await db.scalar(
                        select(DigitalEventDeliveryRecord).where(
                            DigitalEventDeliveryRecord.event_id == event.id,
                            DigitalEventDeliveryRecord.subscription_id == subscription.id,
                        )
                    )
                    if existing is None:
                        db.add(
                            DigitalEventDeliveryRecord(
                                tenant_id=event.tenant_id,
                                event_id=event.id,
                                subscription_id=subscription.id,
                                status="pending",
                                attempts=0,
                            )
                        )
                await db.flush()
                await digital_events.complete(
                    db, event_id=event.id, worker_id=self.worker_id
                )
                await db.commit()
            except Exception as error:
                await db.rollback()
                event = await db.get(DigitalEventOutboxRecord, event_id)
                if event is None or event.status != "leased" or event.locked_by != self.worker_id:
                    return
                await digital_events.fail(
                    db,
                    event_id=event.id,
                    worker_id=self.worker_id,
                    error=f"{type(error).__name__}: {str(error)[:2000]}",
                )
                await db.commit()

    async def _lease_deliveries(self) -> list[str]:
        async with SessionFactory() as db:
            rows = await digital_event_deliveries.lease_batch(
                db,
                worker_id=self.worker_id,
                limit=self.batch_size * 2,
                lease_seconds=self.lease_seconds,
            )
            ids = [row.id for row in rows]
            await db.commit()
            return ids

    async def _process_delivery(self, delivery_id: str) -> None:
        async with SessionFactory() as db:
            delivery = await db.get(DigitalEventDeliveryRecord, delivery_id)
            if (
                delivery is None
                or delivery.status != "delivering"
                or delivery.locked_by != self.worker_id
            ):
                return
            subscription = await db.get(
                DigitalEventSubscriptionRecord, delivery.subscription_id
            )
            policy = _object(subscription.delivery_policy_json) if subscription else {}
            max_attempts = max(1, min(int(policy.get("max_attempts", 8)), 50))
            try:
                evidence = await digital_event_deliveries.deliver(
                    db, delivery=delivery
                )
                await digital_event_deliveries.complete(
                    db,
                    delivery_id=delivery.id,
                    worker_id=self.worker_id,
                    evidence=evidence,
                )
                await db.commit()
            except EventDeliveryError as error:
                retry_after = min(30 * (2 ** max(0, delivery.attempts - 1)), 3600)
                await digital_event_deliveries.fail(
                    db,
                    delivery_id=delivery.id,
                    worker_id=self.worker_id,
                    error=str(error),
                    retry_after_seconds=retry_after,
                    max_attempts=max_attempts,
                    permanent=error.permanent,
                )
                await db.commit()
            except Exception as error:
                await db.rollback()
                delivery = await db.get(DigitalEventDeliveryRecord, delivery_id)
                if (
                    delivery is None
                    or delivery.status != "delivering"
                    or delivery.locked_by != self.worker_id
                ):
                    return
                retry_after = min(30 * (2 ** max(0, delivery.attempts - 1)), 3600)
                await digital_event_deliveries.fail(
                    db,
                    delivery_id=delivery.id,
                    worker_id=self.worker_id,
                    error=f"{type(error).__name__}: {str(error)[:2000]}",
                    retry_after_seconds=retry_after,
                    max_attempts=max_attempts,
                )
                await db.commit()

    async def run_once(self) -> int:
        job_ids = await self._lease_jobs()
        for job_id in job_ids:
            await self._process_job(job_id)
        event_ids = await self._lease_events()
        for event_id in event_ids:
            await self._fanout_event(event_id)
        delivery_ids = await self._lease_deliveries()
        for delivery_id in delivery_ids:
            await self._process_delivery(delivery_id)
        return len(job_ids) + len(event_ids) + len(delivery_ids)

    async def run_forever(self) -> None:
        await init_db()
        print(f"Operly Platform Worker started as {self.worker_id}", flush=True)
        e2e_enabled = os.getenv("OPERLY_PLUGIN_HOSTING_E2E", "").strip().lower()
        if e2e_enabled in {"1", "true", "yes", "on"}:
            asyncio.create_task(_run_hosting_e2e_once())
        while True:
            processed = await self.run_once()
            if processed == 0:
                await asyncio.sleep(self.poll_seconds)


async def main() -> None:
    enabled = os.getenv("OPERLY_PLATFORM_WORKER_ENABLED", "true").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        print("Operly Platform Worker is disabled")
        return
    await PlatformWorker().run_forever()


if __name__ == "__main__":
    asyncio.run(main())
