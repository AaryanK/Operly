import json
import re
from datetime import datetime
from enum import StrEnum

from sqlalchemy import desc, select

from packages.company.events import append_event
from packages.database.product_models import SolutionDeployment, SolutionJob
from packages.software_projects import ProjectState, SoftwareProjectService, SoftwareSourceService, files_from_row
from packages.software_projects.static_assets import canonical_static_document
from packages.solutions import LifecycleStatus, RuntimeType
from packages.solutions.deployment import DeploymentFailure, DeploymentUnavailable, configured_provider


class JobType(StrEnum):
    BUILD = "build"
    VERIFY = "verify"
    PREVIEW = "preview"
    PUBLISH = "publish"
    ROLLBACK = "rollback"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"


TRANSITIONS = {
    JobStatus.QUEUED: {JobStatus.RUNNING, JobStatus.CANCEL_REQUESTED},
    JobStatus.RUNNING: {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCEL_REQUESTED},
    JobStatus.CANCEL_REQUESTED: {JobStatus.CANCELLED},
}


def transition(job, status, *, evidence=None, failure=None, log=None):
    status = JobStatus(status)
    if status != job.status and status not in TRANSITIONS.get(JobStatus(job.status), set()):
        raise ValueError(f"Invalid Solution job transition {job.status} -> {status}")
    now = datetime.utcnow()
    job.status = status
    if status == JobStatus.RUNNING:
        job.started_at = now
    if status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}:
        job.ended_at = now
    if failure:
        job.failure_classification = failure
    if evidence is not None:
        job.evidence_json = json.dumps(evidence, sort_keys=True)[:32_000]
    if log:
        clean = re.sub(
            r"(?i)(secret|token|password|api[_ -]?key)\s*[:=]\s*\S+",
            r"\1=[redacted]",
            str(log),
        )
        items = json.loads(job.log_json or "[]")
        items.append(clean[:2000])
        job.log_json = json.dumps(items[-20:])[:32_000]


def job_json(row):
    return {
        "id": row.id,
        "solution_id": row.solution_id,
        "source_version": row.source_version_reference,
        "job_type": row.job_type,
        "status": row.status,
        "attempt": row.attempt,
        "queued_at": row.queued_at.isoformat(),
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "ended_at": row.ended_at.isoformat() if row.ended_at else None,
        "evidence": json.loads(row.evidence_json or "{}"),
        "failure_classification": row.failure_classification,
        "cancellation_requested": row.cancellation_requested,
    }


class ProductionService:
    """Publish canonical static SoftwareProject source without reviving SiteSchema."""

    def __init__(self, solutions, provider=None):
        self.solutions = solutions
        self.provider = provider or configured_provider()
        self.projects = SoftwareProjectService()
        self.sources = SoftwareSourceService()

    async def _job(self, db, tenant_id, solution_id, version, job_type, key):
        existing = await db.scalar(
            select(SolutionJob).where(
                SolutionJob.tenant_id == tenant_id,
                SolutionJob.idempotency_key == key,
            )
        )
        if existing:
            return existing, False
        attempt = (
            await db.scalar(
                select(SolutionJob.attempt)
                .where(
                    SolutionJob.tenant_id == tenant_id,
                    SolutionJob.solution_id == solution_id,
                    SolutionJob.job_type == job_type,
                )
                .order_by(desc(SolutionJob.attempt))
            )
        ) or 0
        row = SolutionJob(
            tenant_id=tenant_id,
            solution_id=solution_id,
            source_version_reference=version,
            job_type=job_type,
            status=JobStatus.QUEUED,
            attempt=attempt + 1,
            idempotency_key=key,
        )
        db.add(row)
        await db.flush()
        return row, True

    async def _source(self, db, tenant_id, project_id, version_reference=None):
        if version_reference:
            return await self.sources.get(db, tenant_id, project_id, version_reference)
        source = await self.sources.latest(db, tenant_id, project_id)
        if source is None:
            raise LookupError("Canonical source version not found")
        return source

    async def publish(
        self,
        db,
        tenant_id,
        solution_id,
        user_id,
        *,
        idempotency_key=None,
        version_reference=None,
        job_type=JobType.PUBLISH,
    ):
        row, runtime = await self.solutions.resolve(db, tenant_id, solution_id)
        if RuntimeType(row.runtime_type) != RuntimeType.SOFTWARE_PROJECT:
            raise ValueError("Only SoftwareProject-backed Solutions can be published")

        source = await self._source(db, tenant_id, runtime.id, version_reference)
        if source.runtime_profile != "static-web-js":
            raise ValueError(
                f"Runtime profile {source.runtime_profile!r} requires a deployment RuntimePlugin; managed static publishing only supports static-web-js"
            )
        version_id = source.id
        key = idempotency_key or f"{solution_id}:{version_id}:{job_type}"
        job, created = await self._job(db, tenant_id, solution_id, version_id, job_type, key)
        if not created:
            return job, row

        event_prefix = "solution.rollback" if job_type == JobType.ROLLBACK else "solution.publish"
        await append_event(
            db,
            tenant_id=tenant_id,
            event_type=f"{event_prefix}.requested",
            payload={
                "solution_id": row.id,
                "job_id": job.id,
                "version": version_id,
                "kind": "software_source_version",
            },
            actor_type="owner",
            actor_id=user_id,
            source="solution_production",
        )
        transition(job, JobStatus.RUNNING, log="Canonical static deployment started")
        row.lifecycle_status = LifecycleStatus.PUBLISHING
        await self.projects.set_execution_state(
            db,
            workspace_id=tenant_id,
            project_id=runtime.id,
            state=ProjectState.PUBLISHING,
        )
        await append_event(
            db,
            tenant_id=tenant_id,
            event_type=f"{event_prefix}.started",
            payload={"solution_id": row.id, "job_id": job.id},
            source="solution_production",
        )

        current = await db.scalar(
            select(SolutionDeployment)
            .where(
                SolutionDeployment.tenant_id == tenant_id,
                SolutionDeployment.solution_id == solution_id,
                SolutionDeployment.status == "active",
            )
            .order_by(desc(SolutionDeployment.deployed_at))
        )
        build_job = verify_job = None
        try:
            build_job, build_created = await self._job(
                db, tenant_id, solution_id, version_id, JobType.BUILD, key + ":build"
            )
            if build_created:
                transition(build_job, JobStatus.RUNNING, log="Rendering canonical static SoftwareProject source")
            files = files_from_row(source)
            html = canonical_static_document(files)
            html = html.replace(
                "/api/public/sites/production/forms/",
                f"/api/public/presence/{solution_id}/forms/",
            )
            if build_created:
                transition(
                    build_job,
                    JobStatus.SUCCEEDED,
                    evidence={
                        "output_bytes": len(html.encode()),
                        "source_version": version_id,
                        "source_authority": "software_source_versions",
                    },
                    log="Canonical static artifact rendered",
                )

            verify_job, verify_created = await self._job(
                db, tenant_id, solution_id, version_id, JobType.VERIFY, key + ":verify"
            )
            if verify_created:
                transition(verify_job, JobStatus.RUNNING, log="Verifying static artifact policy")
            if not html.strip().lower().startswith("<!doctype html>") or len(html.encode()) > 2_000_000:
                raise DeploymentFailure("Rendered canonical artifact failed verification")
            if verify_created:
                transition(
                    verify_job,
                    JobStatus.SUCCEEDED,
                    evidence={
                        "source_authority": "software_source_versions",
                        "static_policy_valid": True,
                    },
                    log="Artifact verification passed",
                )

            result = await self.provider.deploy(
                solution_id=solution_id,
                version_reference=version_id,
                content=html,
            )
            healthy, health = await self.provider.health(result)
            if not healthy:
                raise DeploymentFailure("Published artifact failed health verification")

            deployment = SolutionDeployment(
                tenant_id=tenant_id,
                solution_id=solution_id,
                job_id=job.id,
                provider=self.provider.name,
                provider_reference=result.provider_reference,
                version_reference=version_id,
                previous_deployment_id=current.id if current else None,
                public_slug=f"{solution_id[:12]}-{job.id[:8]}",
                public_url=f"/presence/{solution_id}",
                artifact_reference=result.artifact_reference,
                artifact_digest=result.artifact_digest,
                status="active",
                health_state="healthy",
                health_evidence_json=json.dumps(health, sort_keys=True),
                deployed_at=datetime.utcnow(),
            )
            db.add(deployment)
            await db.flush()
            if current:
                current.status = "superseded"

            row.lifecycle_status = LifecycleStatus.LIVE
            row.production_state = "live"
            row.production_url = deployment.public_url
            row.current_version_reference = version_id
            row.visibility = "public"
            await self.projects.set_execution_state(
                db,
                workspace_id=tenant_id,
                project_id=runtime.id,
                source_version_id=source.id,
                runtime_id=source.runtime_profile,
                state=ProjectState.LIVE,
            )
            transition(
                job,
                JobStatus.SUCCEEDED,
                evidence={
                    "deployment_id": deployment.id,
                    "public_url": deployment.public_url,
                    "health": health,
                    "source_authority": "software_source_versions",
                },
                log="Health verification passed",
            )
            await append_event(
                db,
                tenant_id=tenant_id,
                event_type=f"{event_prefix}.succeeded",
                payload={
                    "solution_id": row.id,
                    "job_id": job.id,
                    "deployment_id": deployment.id,
                    "version": version_id,
                    "kind": "software_source_version",
                    "public_url": deployment.public_url,
                },
                source="solution_production",
            )
            return job, row
        except (DeploymentUnavailable, DeploymentFailure, ValueError) as error:
            for stage in (build_job, verify_job):
                if stage and stage.status == JobStatus.RUNNING:
                    transition(
                        stage,
                        JobStatus.FAILED,
                        failure="verification_failure",
                        evidence={"reason": str(error)[:500]},
                        log=str(error),
                    )
            classification = (
                "provider_unconfigured"
                if isinstance(error, DeploymentUnavailable)
                else "health_check_failure"
                if "health" in str(error).lower()
                else "deployment_failure"
            )
            transition(
                job,
                JobStatus.FAILED,
                failure=classification,
                evidence={"reason": str(error)[:500]},
                log=str(error),
            )
            row.lifecycle_status = LifecycleStatus.LIVE if current else LifecycleStatus.FAILED
            row.production_state = "live" if current else "failed"
            await self.projects.set_execution_state(
                db,
                workspace_id=tenant_id,
                project_id=runtime.id,
                state=ProjectState.LIVE if current else ProjectState.FAILED,
            )
            await append_event(
                db,
                tenant_id=tenant_id,
                event_type=f"{event_prefix}.failed",
                payload={
                    "solution_id": row.id,
                    "job_id": job.id,
                    "failure_classification": classification,
                    "previous_live_preserved": bool(current),
                },
                source="solution_production",
            )
            return job, row

    async def rollback(self, db, tenant_id, solution_id, user_id, idempotency_key=None):
        current = await db.scalar(
            select(SolutionDeployment).where(
                SolutionDeployment.tenant_id == tenant_id,
                SolutionDeployment.solution_id == solution_id,
                SolutionDeployment.status == "active",
            )
        )
        if not current or not current.previous_deployment_id:
            raise ValueError("No previous published version is available")
        previous = await db.scalar(
            select(SolutionDeployment).where(
                SolutionDeployment.id == current.previous_deployment_id,
                SolutionDeployment.tenant_id == tenant_id,
                SolutionDeployment.solution_id == solution_id,
            )
        )
        if not previous:
            raise LookupError("Previous deployment not found")
        return await self.publish(
            db,
            tenant_id,
            solution_id,
            user_id,
            idempotency_key=idempotency_key
            or f"{solution_id}:{previous.version_reference}:rollback:{current.id}",
            version_reference=previous.version_reference,
            job_type=JobType.ROLLBACK,
        )
