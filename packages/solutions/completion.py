"""Finalize asynchronous software work back into the originating AgentRun/surface."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import func, select

from packages.database.artifact_models import AgentRunEventRecord, AgentRunRecord
from packages.database.product_models import SolutionJob, SolutionRecord
from packages.software_projects.delivery import persist_generated_source_archive
from packages.tasks.delivery import deliver_task_output


def _json(value: str | None, default):
    try:
        parsed=json.loads(value or "")
    except Exception:
        return default
    return parsed


async def _persist_evidence(db, job: SolutionJob, evidence: dict[str, Any]) -> None:
    job.evidence_json=json.dumps(evidence,ensure_ascii=False,sort_keys=True,default=str)
    await db.commit()


async def _append_parent_completion(
    db,
    *,
    parent_run_id: str | None,
    tenant_id: str,
    job: SolutionJob,
    solution: SolutionRecord,
    succeeded: bool,
    artifact_ids: list[str],
    evidence: dict[str, Any],
) -> None:
    run_id=str(parent_run_id or "").strip()
    if not run_id:return
    row=await db.get(AgentRunRecord,run_id)
    if row is None:return
    # Cross-scope completion is allowed only when the durable run itself belongs to
    # this Workspace. Personal-origin requests keep their Personal parent run; in
    # that case delivery still occurs but Workspace evidence is never copied into a
    # Personal source store. Only opaque scoped handles are attached below.
    checkpoint=_json(row.checkpoint_json,{})
    if not isinstance(checkpoint,dict):checkpoint={}
    facts=checkpoint.get("facts") if isinstance(checkpoint.get("facts"),dict) else {}
    deferred=facts.get("deferred_work") if isinstance(facts.get("deferred_work"),dict) else {}
    if deferred and str(deferred.get("job_id") or "") not in {"",job.id}:
        return
    facts["deferred_work"]={
        **deferred,
        "job_id":job.id,
        "solution_id":solution.id,
        "state":"completed" if succeeded else "failed",
        "completed_at":datetime.utcnow().isoformat()+"Z",
    }
    facts["software_completion"]={
        "solution_id":solution.id,
        "project_id":solution.runtime_reference,
        "job_id":job.id,
        "build_id":evidence.get("buildId"),
        "canonical_source_version_id":evidence.get("canonicalSourceVersionId"),
        "artifact_ids":artifact_ids,
        "verified":bool(succeeded and str(evidence.get("buildState") or "")=="preview_ready"),
    }
    checkpoint["facts"]=facts
    refs={str(item) for item in (checkpoint.get("artifact_refs") or []) if str(item).strip()}
    refs.update(artifact_ids)
    checkpoint["artifact_refs"]=sorted(refs)
    row.checkpoint_json=json.dumps(checkpoint,ensure_ascii=False,sort_keys=True,default=str)[:250_000]
    row.artifact_refs_json=json.dumps(sorted(refs),ensure_ascii=False)[:50_000]
    row.state="completed" if succeeded else "failed"
    row.last_error=None if succeeded else str(evidence.get("error") or evidence.get("failureMessage") or "software build failed")[:20_000]
    row.updated_at=datetime.utcnow()
    row.completed_at=datetime.utcnow()
    sequence=int(await db.scalar(select(func.coalesce(func.max(AgentRunEventRecord.sequence),0)).where(AgentRunEventRecord.run_id==run_id)) or 0)+1
    db.add(AgentRunEventRecord(
        run_id=run_id,sequence=sequence,event_type="run.external_completion",
        payload_json=json.dumps({
            "kind":"software_build","job_id":job.id,"solution_id":solution.id,
            "project_id":solution.runtime_reference,"succeeded":succeeded,
            "artifact_ids":artifact_ids,"evidence":{
                "buildId":evidence.get("buildId"),"buildState":evidence.get("buildState"),
                "canonicalSourceVersionId":evidence.get("canonicalSourceVersionId"),
            },
        },ensure_ascii=False,sort_keys=True,default=str)[:250_000],
    ))


async def _retryable_finalization_failure(db, job: SolutionJob, evidence: dict[str, Any], stage: str, error: Exception) -> dict[str, Any]:
    """Persist completion failure without mutating verified software build truth."""
    evidence["completionFinalized"]=False
    evidence["completionStatus"]="RETRYABLE"
    evidence["completionFailedStage"]=stage
    evidence["completionError"]=f"{type(error).__name__}:{str(error)[:500]}"
    await _persist_evidence(db,job,evidence)
    return evidence


async def finalize_software_job(
    db,
    *,
    job: SolutionJob,
    solution: SolutionRecord,
    generated_source=None,
) -> dict[str, Any]:
    """Idempotently archive, wake the parent run, and deliver terminal output.

    Build/test/start/health/acceptance evidence is authoritative for software
    success. Artifact projection or surface delivery failures are completion
    failures only: they stay retryable and must never rewrite a verified build as
    failed. Each durable stage commits its receipt before the next stage begins so
    retrying finalization cannot recreate the archive or append parent completion
    twice.
    """
    evidence=_json(job.evidence_json,{})
    if not isinstance(evidence,dict):evidence={}
    if bool(evidence.get("completionFinalized")):
        return evidence

    context=_json(solution.context_json,{})
    request=context.get("softwareBuildRequest") if isinstance(context,dict) and isinstance(context.get("softwareBuildRequest"),dict) else {}
    succeeded=job.status=="succeeded" and str(evidence.get("buildState") or "")=="preview_ready"
    return_archive=succeeded and bool(request.get("returnSourceArchive",evidence.get("returnSourceArchive",True))) and generated_source is not None

    artifact_ids:list[str]=[]
    existing_archive_id=str(evidence.get("sourceArchiveArtifactId") or "").strip()
    if existing_archive_id:
        artifact_ids.append(existing_archive_id)
        evidence.setdefault("sourceArchiveStatus","VERIFIED")
    elif return_archive:
        try:
            archive=await persist_generated_source_archive(
                db,
                tenant_id=job.tenant_id,
                created_by=str(job.created_by or evidence.get("createdBy") or ""),
                source=generated_source,
                filename=f"{solution.name}-source.zip",
                run_id=str(evidence.get("parentAgentRunId") or request.get("parentAgentRunId") or "") or None,
            )
        except Exception as error:
            return await _retryable_finalization_failure(db,job,evidence,"source_archive",error)
        artifact_ids.append(str(archive["artifact_id"]))
        evidence.update({
            "sourceArchiveArtifactId":archive["artifact_id"],
            "sourceArchiveFilename":archive["filename"],
            "sourceArchiveSha256":archive["sha256"],
            "sourceArchiveStatus":"VERIFIED",
        })
        await _persist_evidence(db,job,evidence)
    else:
        evidence.setdefault("sourceArchiveStatus","NOT_REQUIRED")

    parent_run_id=str(evidence.get("parentAgentRunId") or request.get("parentAgentRunId") or "") or None
    if parent_run_id and evidence.get("parentRunCompletionStatus")!="VERIFIED":
        try:
            await _append_parent_completion(
                db,parent_run_id=parent_run_id,tenant_id=job.tenant_id,job=job,solution=solution,
                succeeded=succeeded,artifact_ids=artifact_ids,evidence=evidence,
            )
        except Exception as error:
            return await _retryable_finalization_failure(db,job,evidence,"parent_run",error)
        evidence["parentRunCompletionStatus"]="VERIFIED"
        evidence["parentRunCompletionIdempotencyKey"]=f"software-job:{job.id}:parent-completion"
        await _persist_evidence(db,job,evidence)
    elif not parent_run_id:
        evidence.setdefault("parentRunCompletionStatus","NOT_REQUIRED")

    target=evidence.get("deliveryTarget") if isinstance(evidence.get("deliveryTarget"),dict) else request.get("deliveryTarget")
    receipt=evidence.get("deliveryReceipt") if isinstance(evidence.get("deliveryReceipt"),dict) else None
    if isinstance(target,dict) and target.get("provider") and evidence.get("deliveryStatus")!="VERIFIED":
        message=(
            f"{solution.name} is ready. The application passed its isolated build, tests, startup, health, and acceptance checks."
            if succeeded else
            f"{solution.name} could not complete successfully. Operly kept the failed build evidence and did not mark the application preview-ready."
        )
        delivery_target=dict(target)
        delivery_target.setdefault("idempotency_key",f"software-job:{job.id}:terminal-delivery")
        try:
            receipt=await deliver_task_output(delivery_target,message,artifact_ids=artifact_ids)
            evidence["deliveryStatus"]="VERIFIED"
            evidence["deliveryReceipt"]=receipt
            evidence["deliveryIdempotencyKey"]=delivery_target["idempotency_key"]
            evidence.pop("deliveryError",None)
            await _persist_evidence(db,job,evidence)
        except Exception as error:
            evidence["deliveryStatus"]="FAILED"
            evidence["deliveryError"]=f"{type(error).__name__}:{str(error)[:300]}"
            return await _retryable_finalization_failure(db,job,evidence,"surface_delivery",error)
    elif not (isinstance(target,dict) and target.get("provider")):
        evidence.setdefault("deliveryStatus","NOT_REQUIRED")

    evidence["completionFinalized"]=True
    evidence["completionStatus"]="VERIFIED"
    evidence["completionFinalizedAt"]=datetime.utcnow().isoformat()+"Z"
    evidence.pop("completionFailedStage",None)
    evidence.pop("completionError",None)
    await _persist_evidence(db,job,evidence)
    return evidence
