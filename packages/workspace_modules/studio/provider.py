from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import os
import re
import shutil
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.product_models import SolutionDeployment, SolutionDomain, SolutionJob, SolutionRecord
from packages.database.software_project_models import SoftwareProjectRecord, SoftwareSourceVersionRecord
from packages.kernel.contracts import CapabilityExecutionResult, CapabilityRisk, CapabilitySpec
from packages.security.execution_context import ExecutionContext


PROVIDER_ID = "operly.workspace_studio"
STATIC_RUNTIME_HINTS = {"static", "static_html", "html", "website", "web_static", "static-site"}
MAX_PUBLISH_FILES = 1000
MAX_PUBLISH_BYTES = 50 * 1024 * 1024


def _object(properties: dict[str, Any], *, required: list[str] | None = None, additional: bool = False) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": additional,
    }


def _capability(
    capability_id: str,
    display_name: str,
    description: str,
    *,
    permission: str,
    input_schema: dict[str, Any] | None = None,
    risk: CapabilityRisk = CapabilityRisk.READ_ONLY,
    approval: bool = False,
    reversible: bool = False,
    emits: tuple[str, ...] = (),
    tags: tuple[str, ...] = (),
) -> CapabilitySpec:
    return CapabilitySpec(
        id=capability_id,
        version="1.0.0",
        display_name=display_name,
        description=description,
        provider_id=PROVIDER_ID,
        scopes=frozenset({"workspace"}),
        input_schema=input_schema or _object({}),
        output_schema=_object({}, additional=True),
        permissions=(permission,),
        risk=risk,
        approval_required=approval,
        reversible=reversible,
        emits=emits,
        tags=frozenset(("studio", "solution", *tags)),
        resource_scope="workspace",
    )


def workspace_studio_capabilities() -> tuple[CapabilitySpec, ...]:
    project_id = {"type": "string", "minLength": 1, "maxLength": 80}
    solution_id = {"type": "string", "minLength": 1, "maxLength": 80}
    deployment_id = {"type": "string", "minLength": 1, "maxLength": 80}
    return (
        _capability(
            "studio.projects.list",
            "List Studio projects",
            "List canonical Workspace software projects with source and production state.",
            permission="solution:read",
            input_schema=_object({"limit": {"type": "integer", "minimum": 1, "maximum": 100}}),
            tags=("project", "read"),
        ),
        _capability(
            "studio.project.inspect",
            "Inspect Studio project",
            "Inspect canonical source, deployability, linked Solution, and current deployment state.",
            permission="solution:read",
            input_schema=_object({"project_id": project_id}, required=["project_id"]),
            tags=("project", "source", "deployment", "read"),
        ),
        _capability(
            "studio.solution.status",
            "Read Studio solution deployment",
            "Read production, deployment, job, and custom-domain state for a Studio Solution.",
            permission="solution:read",
            input_schema=_object({"solution_id": solution_id}, required=["solution_id"]),
            tags=("deployment", "status", "read"),
        ),
        _capability(
            "studio.solution.deploy",
            "Deploy Studio solution",
            "Publish a verified static or prebuilt Studio source bundle to Operly Hosting.",
            permission="solution:write",
            input_schema=_object(
                {
                    "project_id": project_id,
                    "solution_name": {"type": "string", "maxLength": 200},
                },
                required=["project_id"],
            ),
            risk=CapabilityRisk.HIGH,
            approval=True,
            reversible=True,
            emits=("studio.solution.deployed",),
            tags=("deployment", "write", "hosting"),
        ),
        _capability(
            "studio.solution.rollback",
            "Roll back Studio solution",
            "Restore a previous healthy Studio deployment without rebuilding source.",
            permission="solution:write",
            input_schema=_object(
                {"solution_id": solution_id, "deployment_id": deployment_id},
                required=["solution_id"],
            ),
            risk=CapabilityRisk.HIGH,
            approval=True,
            reversible=True,
            emits=("studio.solution.rolled_back",),
            tags=("deployment", "rollback", "write"),
        ),
        _capability(
            "studio.solution.domain.request",
            "Request Studio custom domain",
            "Create a Workspace-owned custom-domain request and return deterministic DNS requirements.",
            permission="solution:write",
            input_schema=_object(
                {
                    "solution_id": solution_id,
                    "domain": {
                        "type": "string",
                        "minLength": 4,
                        "maxLength": 253,
                        "pattern": r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[A-Za-z]{2,63}$",
                    },
                },
                required=["solution_id", "domain"],
            ),
            risk=CapabilityRisk.MEDIUM,
            approval=True,
            reversible=True,
            emits=("studio.solution.domain.requested",),
            tags=("domain", "dns", "write"),
        ),
    )


def _json(value: str | None, fallback: Any) -> Any:
    try:
        parsed = json.loads(value or "")
        return parsed
    except (TypeError, json.JSONDecodeError):
        return fallback


def _clean_path(raw: str) -> str:
    value = str(raw or "").replace("\\", "/").lstrip("/")
    path = PurePosixPath(value)
    if not value or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Studio source contains an unsafe path")
    return str(path)


def _source_files(row: SoftwareSourceVersionRecord) -> dict[str, bytes]:
    raw = _json(row.files_json, {})
    records: list[tuple[str, Any]] = []
    if isinstance(raw, dict):
        records = [(str(path), value) for path, value in raw.items()]
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("path"):
                records.append((str(item["path"]), item))
    else:
        raise ValueError("Studio source bundle is malformed")

    files: dict[str, bytes] = {}
    total = 0
    for raw_path, value in records:
        path = _clean_path(raw_path)
        content: Any = value
        if isinstance(value, dict):
            content = value.get("content", value.get("text", ""))
            encoding = str(value.get("encoding") or "utf-8").lower()
            if encoding not in {"utf-8", "utf8", "text"}:
                raise ValueError(f"Studio source file {path} uses an unsupported encoding")
        if isinstance(content, str):
            data = content.encode("utf-8")
        elif isinstance(content, bytes):
            data = content
        else:
            continue
        total += len(data)
        if total > MAX_PUBLISH_BYTES:
            raise ValueError("Studio publish bundle exceeds the 50 MB deterministic limit")
        files[path] = data
        if len(files) > MAX_PUBLISH_FILES:
            raise ValueError("Studio publish bundle contains too many files")
    return files


def _publishable_bundle(row: SoftwareSourceVersionRecord) -> tuple[dict[str, bytes], dict[str, Any]]:
    files = _source_files(row)
    profile = str(row.runtime_profile or "").strip().lower()
    prefix = ""
    entry = "index.html"
    if "dist/index.html" in files:
        prefix = "dist/"
        entry = "dist/index.html"
    elif "build/index.html" in files:
        prefix = "build/"
        entry = "build/index.html"
    elif "index.html" in files and (profile in STATIC_RUNTIME_HINTS or "package.json" not in files):
        entry = "index.html"
    else:
        reason = (
            "React/Vite or other build source has no committed dist/build output"
            if "package.json" in files
            else "No publishable index.html was found"
        )
        return {}, {
            "deployable": False,
            "reason": reason,
            "runtime_profile": row.runtime_profile,
            "source_version_id": row.id,
            "source_version": row.source_version,
        }

    publish: dict[str, bytes] = {}
    for path, content in files.items():
        if prefix:
            if not path.startswith(prefix):
                continue
            relative = path[len(prefix) :]
        else:
            relative = path
        if relative:
            publish[_clean_path(relative)] = content
    if "index.html" not in publish:
        publish["index.html"] = files[entry]
    digest = hashlib.sha256()
    for path in sorted(publish):
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(publish[path])
        digest.update(b"\0")
    return publish, {
        "deployable": True,
        "reason": "Prebuilt output" if prefix else "Static source bundle",
        "runtime_profile": row.runtime_profile,
        "source_version_id": row.id,
        "source_version": row.source_version,
        "publish_prefix": prefix or "/",
        "file_count": len(publish),
        "size_bytes": sum(len(value) for value in publish.values()),
        "artifact_digest": digest.hexdigest(),
    }


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return (cleaned or "studio-solution")[:80]


def _deployment_root() -> Path:
    configured = os.getenv("OPERLY_DEPLOYMENT_ROOT", "").strip()
    if not configured:
        raise RuntimeError("OPERLY_DEPLOYMENT_ROOT is not configured; Studio deployment is fail-closed")
    root = Path(configured).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _public_base_url() -> str:
    configured = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if configured:
        return configured
    railway = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip().strip("/")
    if railway:
        return f"https://{railway}"
    return "http://localhost:8000"


def _write_bundle(root: Path, deployment_id: str, files: dict[str, bytes]) -> Path:
    sites_root = (root / "studio-sites").resolve()
    sites_root.mkdir(parents=True, exist_ok=True)
    target = (sites_root / deployment_id).resolve()
    if sites_root not in target.parents:
        raise RuntimeError("Resolved Studio deployment path escaped the deployment root")
    temp = (sites_root / f".tmp-{deployment_id}-{uuid4().hex[:8]}").resolve()
    temp.mkdir(parents=True, exist_ok=False)
    try:
        for relative, content in files.items():
            path = (temp / relative).resolve()
            if temp not in path.parents:
                raise ValueError("Studio source path escaped the deployment directory")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        if not (temp / "index.html").is_file():
            raise ValueError("Studio deployment has no index.html")
        if target.exists():
            shutil.rmtree(target)
        temp.rename(target)
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise
    return target


async def _project(db: AsyncSession, tenant_id: str, project_id: str) -> SoftwareProjectRecord:
    row = await db.scalar(
        select(SoftwareProjectRecord).where(
            SoftwareProjectRecord.id == project_id,
            SoftwareProjectRecord.tenant_id == tenant_id,
        )
    )
    if row is None:
        raise LookupError("Studio project not found in this Workspace")
    return row


async def _active_source(db: AsyncSession, project: SoftwareProjectRecord) -> SoftwareSourceVersionRecord | None:
    if project.active_source_version_id:
        row = await db.scalar(
            select(SoftwareSourceVersionRecord).where(
                SoftwareSourceVersionRecord.id == project.active_source_version_id,
                SoftwareSourceVersionRecord.tenant_id == project.tenant_id,
                SoftwareSourceVersionRecord.project_id == project.id,
            )
        )
        if row is not None:
            return row
    return await db.scalar(
        select(SoftwareSourceVersionRecord)
        .where(
            SoftwareSourceVersionRecord.tenant_id == project.tenant_id,
            SoftwareSourceVersionRecord.project_id == project.id,
        )
        .order_by(desc(SoftwareSourceVersionRecord.source_version))
        .limit(1)
    )


async def _linked_solution(db: AsyncSession, tenant_id: str, project_id: str) -> SolutionRecord | None:
    return await db.scalar(
        select(SolutionRecord).where(
            SolutionRecord.tenant_id == tenant_id,
            SolutionRecord.runtime_type == "software_project",
            SolutionRecord.runtime_reference == project_id,
        )
    )


async def _solution(db: AsyncSession, tenant_id: str, solution_id: str) -> SolutionRecord:
    row = await db.scalar(
        select(SolutionRecord).where(
            SolutionRecord.id == solution_id,
            SolutionRecord.tenant_id == tenant_id,
        )
    )
    if row is None:
        raise LookupError("Studio Solution not found in this Workspace")
    return row


async def _active_deployment(db: AsyncSession, tenant_id: str, solution_id: str) -> SolutionDeployment | None:
    return await db.scalar(
        select(SolutionDeployment)
        .where(
            SolutionDeployment.tenant_id == tenant_id,
            SolutionDeployment.solution_id == solution_id,
            SolutionDeployment.status == "active",
        )
        .order_by(desc(SolutionDeployment.deployed_at), desc(SolutionDeployment.created_at))
        .limit(1)
    )


def _deployment_json(row: SolutionDeployment | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "version_reference": row.version_reference,
        "provider": row.provider,
        "status": row.status,
        "health_state": row.health_state,
        "public_url": row.public_url,
        "artifact_digest": row.artifact_digest,
        "previous_deployment_id": row.previous_deployment_id,
        "deployed_at": row.deployed_at.isoformat() if row.deployed_at else None,
    }


def _solution_json(row: SolutionRecord | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "name": row.name,
        "solution_type": row.solution_type,
        "lifecycle_status": row.lifecycle_status,
        "runtime_type": row.runtime_type,
        "runtime_reference": row.runtime_reference,
        "current_version_reference": row.current_version_reference,
        "preview_state": row.preview_state,
        "preview_url": row.preview_url,
        "production_state": row.production_state,
        "production_url": row.production_url,
        "visibility": row.visibility,
    }


async def _project_json(db: AsyncSession, project: SoftwareProjectRecord) -> dict[str, Any]:
    source = await _active_source(db, project)
    solution = await _linked_solution(db, project.tenant_id, project.id)
    deployment = await _active_deployment(db, project.tenant_id, solution.id) if solution else None
    deployability = {"deployable": False, "reason": "No canonical source version exists"}
    if source is not None:
        try:
            _, deployability = _publishable_bundle(source)
        except ValueError as error:
            deployability = {"deployable": False, "reason": str(error), "source_version_id": source.id}
    return {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "state": project.state,
        "active_source_version_id": source.id if source else None,
        "source_version": source.source_version if source else None,
        "runtime_profile": source.runtime_profile if source else None,
        "deployability": deployability,
        "solution": _solution_json(solution),
        "deployment": _deployment_json(deployment),
        "updated_at": project.updated_at.isoformat() if project.updated_at else None,
    }


class WorkspaceStudioProvider:
    async def is_available(
        self,
        db: AsyncSession,
        *,
        context: ExecutionContext,
        capability: CapabilitySpec,
    ) -> bool:
        del db
        if not context.workspace_id:
            return False
        if capability.id == "studio.solution.deploy":
            return bool(os.getenv("OPERLY_DEPLOYMENT_ROOT", "").strip())
        return True

    async def execute(
        self,
        db: AsyncSession,
        *,
        context: ExecutionContext,
        capability: CapabilitySpec,
        arguments: dict[str, Any],
        minimum_context: dict[str, Any],
    ) -> CapabilityExecutionResult:
        del minimum_context
        tenant_id = context.workspace_id
        if not tenant_id:
            raise PermissionError("Studio tools require Workspace authority")
        capability_id = capability.id

        if capability_id == "studio.projects.list":
            limit = max(1, min(int(arguments.get("limit") or 50), 100))
            rows = (
                await db.scalars(
                    select(SoftwareProjectRecord)
                    .where(SoftwareProjectRecord.tenant_id == tenant_id)
                    .order_by(desc(SoftwareProjectRecord.updated_at))
                    .limit(limit)
                )
            ).all()
            projects = [await _project_json(db, row) for row in rows]
            return CapabilityExecutionResult(
                value={"projects": projects},
                resource_type="studio_project_collection",
            )

        if capability_id == "studio.project.inspect":
            row = await _project(db, tenant_id, str(arguments["project_id"]))
            return CapabilityExecutionResult(
                value=await _project_json(db, row),
                resource_type="studio_project",
                resource_id=row.id,
            )

        if capability_id == "studio.solution.status":
            solution = await _solution(db, tenant_id, str(arguments["solution_id"]))
            deployment = await _active_deployment(db, tenant_id, solution.id)
            jobs = (
                await db.scalars(
                    select(SolutionJob)
                    .where(SolutionJob.tenant_id == tenant_id, SolutionJob.solution_id == solution.id)
                    .order_by(desc(SolutionJob.created_at))
                    .limit(20)
                )
            ).all()
            domains = (
                await db.scalars(
                    select(SolutionDomain)
                    .where(SolutionDomain.tenant_id == tenant_id, SolutionDomain.solution_id == solution.id)
                    .order_by(desc(SolutionDomain.created_at))
                )
            ).all()
            return CapabilityExecutionResult(
                value={
                    "solution": _solution_json(solution),
                    "deployment": _deployment_json(deployment),
                    "jobs": [
                        {
                            "id": row.id,
                            "job_type": row.job_type,
                            "status": row.status,
                            "source_version_reference": row.source_version_reference,
                            "failure_classification": row.failure_classification,
                            "created_at": row.created_at.isoformat() if row.created_at else None,
                        }
                        for row in jobs
                    ],
                    "domains": [
                        {
                            "id": row.id,
                            "domain": row.requested_domain,
                            "verification_state": row.verification_state,
                            "ssl_state": row.ssl_state,
                            "dns_requirements": _json(row.dns_requirements_json, {}),
                        }
                        for row in domains
                    ],
                },
                resource_type="studio_solution",
                resource_id=solution.id,
            )

        if capability_id == "studio.solution.deploy":
            project = await _project(db, tenant_id, str(arguments["project_id"]))
            source = await _active_source(db, project)
            if source is None:
                raise ValueError("This Studio project has no canonical source version to deploy")
            publish, deployability = _publishable_bundle(source)
            if not deployability.get("deployable"):
                raise ValueError(str(deployability.get("reason") or "Studio source is not deployable"))

            solution = await _linked_solution(db, tenant_id, project.id)
            if solution is None:
                solution = SolutionRecord(
                    tenant_id=tenant_id,
                    name=str(arguments.get("solution_name") or project.name)[:200],
                    description=project.description or "",
                    solution_type="studio_software",
                    lifecycle_status="approved",
                    runtime_type="software_project",
                    runtime_reference=project.id,
                    current_version_reference=source.id,
                    preview_state="available",
                    preview_url=None,
                    production_state="deploying",
                    production_url=None,
                    visibility="public",
                    context_json=json.dumps({"managed_by": "workspace_studio_tools"}),
                )
                db.add(solution)
                await db.flush()

            active = await _active_deployment(db, tenant_id, solution.id)
            digest = str(deployability["artifact_digest"])
            if (
                active is not None
                and active.version_reference == source.id
                and active.artifact_digest == digest
                and active.health_state == "healthy"
                and Path(active.artifact_reference).is_dir()
            ):
                solution.production_state = "online"
                solution.production_url = active.public_url
                solution.current_version_reference = source.id
                project.state = "deployed"
                project.active_runtime_id = active.id
                return CapabilityExecutionResult(
                    value={
                        "reused": True,
                        "solution": _solution_json(solution),
                        "deployment": _deployment_json(active),
                        "deployability": deployability,
                    },
                    resource_type="studio_deployment",
                    resource_id=active.id,
                    event_payload={"solution_id": solution.id, "deployment_id": active.id, "reused": True},
                )

            idempotency_key = f"studio-deploy:{project.id}:{source.id}:{digest[:24]}"
            existing_job = await db.scalar(
                select(SolutionJob).where(
                    SolutionJob.tenant_id == tenant_id,
                    SolutionJob.idempotency_key == idempotency_key,
                )
            )
            if existing_job is not None:
                existing_deployment = await db.scalar(
                    select(SolutionDeployment).where(
                        SolutionDeployment.tenant_id == tenant_id,
                        SolutionDeployment.job_id == existing_job.id,
                    )
                )
                if existing_deployment and existing_deployment.health_state == "healthy":
                    return CapabilityExecutionResult(
                        value={
                            "reused": True,
                            "solution": _solution_json(solution),
                            "deployment": _deployment_json(existing_deployment),
                            "deployability": deployability,
                        },
                        resource_type="studio_deployment",
                        resource_id=existing_deployment.id,
                    )

            now = datetime.utcnow()
            job = SolutionJob(
                tenant_id=tenant_id,
                solution_id=solution.id,
                source_version_reference=source.id,
                job_type="deploy",
                status="running",
                attempt=1,
                queued_at=now,
                started_at=now,
                log_json=json.dumps([{"at": now.isoformat(), "message": "Verified deterministic static/prebuilt bundle"}]),
                evidence_json=json.dumps(deployability),
                idempotency_key=idempotency_key,
                created_by=context.user_id,
            )
            db.add(job)
            await db.flush()

            deployment = SolutionDeployment(
                tenant_id=tenant_id,
                solution_id=solution.id,
                job_id=job.id,
                provider="operly_static",
                provider_reference="pending",
                version_reference=source.id,
                previous_deployment_id=active.id if active else None,
                public_slug=f"{_slug(project.name)}-{solution.id[:8]}-{source.source_version}-{uuid4().hex[:6]}",
                public_url=f"{_public_base_url()}/studio-sites/{solution.id}/",
                artifact_reference="pending",
                artifact_digest=digest,
                status="provisioning",
                health_state="pending",
                health_evidence_json=json.dumps({"deployability": deployability}),
            )
            db.add(deployment)
            await db.flush()

            try:
                target = await asyncio.to_thread(_write_bundle, _deployment_root(), deployment.id, publish)
                if not (target / "index.html").is_file():
                    raise RuntimeError("Published Studio artifact failed entrypoint verification")
                deployment.artifact_reference = str(target)
                deployment.provider_reference = str(target)
                deployment.status = "active"
                deployment.health_state = "healthy"
                deployment.deployed_at = datetime.utcnow()
                deployment.health_evidence_json = json.dumps(
                    {
                        "entrypoint": "index.html",
                        "file_count": deployability["file_count"],
                        "size_bytes": deployability["size_bytes"],
                        "artifact_digest": digest,
                    }
                )
                if active is not None:
                    active.status = "superseded"
                job.status = "completed"
                job.ended_at = datetime.utcnow()
                job.log_json = json.dumps(
                    [
                        {"at": now.isoformat(), "message": "Verified deterministic static/prebuilt bundle"},
                        {"at": job.ended_at.isoformat(), "message": "Published and verified index.html"},
                    ]
                )
                solution.lifecycle_status = "published"
                solution.current_version_reference = source.id
                solution.production_state = "online"
                solution.production_url = deployment.public_url
                solution.visibility = "public"
                project.state = "deployed"
                project.active_runtime_id = deployment.id
            except Exception as error:
                job.status = "failed"
                job.ended_at = datetime.utcnow()
                job.failure_classification = "static_publish_failed"
                deployment.status = "failed"
                deployment.health_state = "unhealthy"
                solution.production_state = "failed"
                raise RuntimeError("Studio deployment could not publish the verified bundle") from error

            return CapabilityExecutionResult(
                value={
                    "reused": False,
                    "solution": _solution_json(solution),
                    "deployment": _deployment_json(deployment),
                    "deployability": deployability,
                    "job": {"id": job.id, "status": job.status, "job_type": job.job_type},
                },
                resource_type="studio_deployment",
                resource_id=deployment.id,
                event_payload={
                    "project_id": project.id,
                    "solution_id": solution.id,
                    "deployment_id": deployment.id,
                    "version_reference": source.id,
                    "artifact_digest": digest,
                },
            )

        if capability_id == "studio.solution.rollback":
            solution = await _solution(db, tenant_id, str(arguments["solution_id"]))
            current = await _active_deployment(db, tenant_id, solution.id)
            if current is None:
                raise ValueError("Studio Solution has no active deployment to roll back")
            target_id = str(arguments.get("deployment_id") or current.previous_deployment_id or "").strip()
            if not target_id:
                raise ValueError("Studio Solution has no previous deployment")
            target = await db.scalar(
                select(SolutionDeployment).where(
                    SolutionDeployment.id == target_id,
                    SolutionDeployment.tenant_id == tenant_id,
                    SolutionDeployment.solution_id == solution.id,
                )
            )
            if target is None or target.health_state != "healthy":
                raise ValueError("Rollback target is missing or is not a verified healthy deployment")
            artifact = Path(target.artifact_reference).resolve()
            root = (_deployment_root() / "studio-sites").resolve()
            if root not in artifact.parents or not (artifact / "index.html").is_file():
                raise ValueError("Rollback target artifact is no longer available")

            if current.id == target.id:
                return CapabilityExecutionResult(
                    value={"solution": _solution_json(solution), "deployment": _deployment_json(target), "reused": True},
                    resource_type="studio_deployment",
                    resource_id=target.id,
                )

            now = datetime.utcnow()
            key = f"studio-rollback:{solution.id}:{current.id}:{target.id}"
            job = await db.scalar(
                select(SolutionJob).where(
                    SolutionJob.tenant_id == tenant_id,
                    SolutionJob.idempotency_key == key,
                )
            )
            if job is None:
                job = SolutionJob(
                    tenant_id=tenant_id,
                    solution_id=solution.id,
                    source_version_reference=target.version_reference,
                    job_type="rollback",
                    status="completed",
                    queued_at=now,
                    started_at=now,
                    ended_at=now,
                    log_json=json.dumps([{"at": now.isoformat(), "message": f"Restored deployment {target.id}"}]),
                    evidence_json=json.dumps({"from_deployment_id": current.id, "to_deployment_id": target.id}),
                    idempotency_key=key,
                    created_by=context.user_id,
                )
                db.add(job)
            current.status = "rolled_back"
            target.status = "active"
            solution.production_state = "online"
            solution.production_url = target.public_url
            solution.current_version_reference = target.version_reference
            if solution.runtime_type == "software_project":
                project = await _project(db, tenant_id, solution.runtime_reference)
                project.active_runtime_id = target.id
                project.state = "deployed"
            return CapabilityExecutionResult(
                value={
                    "solution": _solution_json(solution),
                    "deployment": _deployment_json(target),
                    "rolled_back_from": current.id,
                    "job": {"id": job.id, "status": job.status, "job_type": "rollback"},
                },
                resource_type="studio_deployment",
                resource_id=target.id,
                event_payload={
                    "solution_id": solution.id,
                    "deployment_id": target.id,
                    "rolled_back_from": current.id,
                },
            )

        if capability_id == "studio.solution.domain.request":
            solution = await _solution(db, tenant_id, str(arguments["solution_id"]))
            domain = str(arguments["domain"]).strip().lower()
            if not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", domain):
                raise ValueError("Invalid custom domain")
            existing = await db.scalar(
                select(SolutionDomain).where(
                    SolutionDomain.tenant_id == tenant_id,
                    SolutionDomain.requested_domain == domain,
                )
            )
            if existing is not None and existing.solution_id != solution.id:
                raise ValueError("That custom domain is already assigned to another Workspace Solution")
            host = urlparse(solution.production_url or _public_base_url()).hostname or ""
            if not host:
                raise ValueError("Operly Hosting does not have a resolvable public host")
            requirements = {
                "type": "CNAME",
                "name": domain,
                "value": host,
                "automated_change": False,
                "verification": "pending",
            }
            row = existing
            if row is None:
                row = SolutionDomain(
                    tenant_id=tenant_id,
                    solution_id=solution.id,
                    requested_domain=domain,
                    verification_state="pending",
                    dns_requirements_json=json.dumps(requirements),
                    ssl_state="pending",
                )
                db.add(row)
                await db.flush()
            else:
                row.dns_requirements_json = json.dumps(requirements)
            return CapabilityExecutionResult(
                value={
                    "id": row.id,
                    "solution_id": solution.id,
                    "domain": domain,
                    "verification_state": row.verification_state,
                    "ssl_state": row.ssl_state,
                    "dns_requirements": requirements,
                },
                resource_type="studio_solution_domain",
                resource_id=row.id,
                event_payload={"solution_id": solution.id, "domain": domain},
            )

        raise LookupError(f"Studio capability is not implemented: {capability_id}")
