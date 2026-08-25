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


async def finalize_software_job(
    db,
    *,
    job: SolutionJob,
    solution: SolutionRecord,
    generated_source=None,
) -> dict[str, Any]:
    """Idempotently archive, wake the parent run, and deliver terminal output."""
    evidence=_json(job.evidence_json,{})
    if not isinstance(evidence,dict):evidence={}
    if bool(evidence.get("completionFinalized")):
        return evidence

    context=_json(solution.context_json,{})
    request=context.get("softwareBuildRequest") if isinstance(context,dict) and isinstance(context.get("softwareBuildRequest"),dict) else {}
    succeeded=job.status=="succeeded" and str(evidence.get("buildState") or "")=="preview_ready"
    artifact_ids:list[str]=[]
    if succeeded and bool(request.get("returnSourceArchive",evidence.get("returnSourceArchive",True))) and generated_source is not None:
        archive=await persist_generated_source_archive(
            db,
            tenant_id=job.tenant_id,
            created_by=str(job.created_by or evidence.get("createdBy") or ""),
            source=generated_source,
            filename=f"{solution.name}-source.zip",
            run_id=str(evidence.get("parentAgentRunId") or request.get("parentAgentRunId") or "") or None,
        )
        artifact_ids.append(str(archive["artifact_id"]))
        evidence.update({
            "sourceArchiveArtifactId":archive["artifact_id"],
            "sourceArchiveFilename":archive["filename"],
            "sourceArchiveSha256":archive["sha256"],
        })

    parent_run_id=str(evidence.get("parentAgentRunId") or request.get("parentAgentRunId") or "") or None
    await _append_parent_completion(
        db,parent_run_id=parent_run_id,tenant_id=job.tenant_id,job=job,solution=solution,
        succeeded=succeeded,artifact_ids=artifact_ids,evidence=evidence,
    )

    target=evidence.get("deliveryTarget") if isinstance(evidence.get("deliveryTarget"),dict) else request.get("deliveryTarget")
    receipt=None
    if isinstance(target,dict) and target.get("provider"):
        message=(
            f"{solution.name} is ready. The application passed its isolated build, tests, startup, health, and acceptance checks."
            if succeeded else
            f"{solution.name} could not complete successfully. Operly kept the failed build evidence and did not mark the application preview-ready."
        )
        try:
            receipt=await deliver_task_output(target,message,artifact_ids=artifact_ids)
            evidence["deliveryStatus"]="VERIFIED"
            evidence["deliveryReceipt"]=receipt
        except Exception as error:
            # Finalization stays retryable. Do not mark finalized if delivery was
            # explicitly requested but failed authorization/provider delivery.
            evidence["deliveryStatus"]="FAILED"
            evidence["deliveryError"]=f"{type(error).__name__}:{str(error)[:300]}"
            job.evidence_json=json.dumps(evidence,ensure_ascii=False,sort_keys=True,default=str)
            await db.commit()
            return evidence

    evidence["completionFinalized"]=True
    evidence["completionFinalizedAt"]=datetime.utcnow().isoformat()+"Z"
    job.evidence_json=json.dumps(evidence,ensure_ascii=False,sort_keys=True,default=str)
    await db.commit()
    return evidence
