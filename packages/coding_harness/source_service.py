"""Persist source authored by the coding harness as immutable OPERLY source bundles."""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import func, select

from packages.coding_harness.opencode_agent import OpenCodeStyleCodingAgent
from packages.custom_software.source_bundles import build_bundle
from packages.database.custom_software_models import GeneratedSourceBundle


def _plan_specification(plan) -> str:
    data = plan.model_dump(mode="json") if hasattr(plan, "model_dump") else dict(plan)
    selected = {
        "projectName": data.get("projectName"),
        "summary": data.get("summary"),
        "effectiveRequirements": data.get("effectiveRequirements") or [],
        "requirementLedger": data.get("requirementLedger") or [],
        "planTree": data.get("planTree") or [],
        "stack": data.get("stack"),
        "roles": data.get("roles") or [],
        "entities": data.get("entities") or [],
        "workflows": data.get("workflows") or [],
        "surfaces": data.get("surfaces") or [],
        "globalValidation": data.get("globalValidation"),
        "unsupportedRequirements": data.get("unsupportedRequirements") or [],
    }
    return json.dumps(selected, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _prompt_text(plan) -> str:
    provenance = getattr(plan, "provenance", {}) or {}
    if hasattr(provenance, "model_dump"):
        provenance = provenance.model_dump(mode="json")
    if isinstance(provenance, dict):
        original = str(provenance.get("originalPrompt") or "").strip()
        if original:
            return original
    return str(getattr(plan, "summary", "") or "")


async def latest_source(db, tenant_id: str, plan_id: str, plan_version: int | None = None):
    query = select(GeneratedSourceBundle).where(
        GeneratedSourceBundle.tenant_id == tenant_id,
        GeneratedSourceBundle.plan_id == plan_id,
    )
    if plan_version is not None:
        query = query.where(GeneratedSourceBundle.plan_version == plan_version)
    query = query.order_by(GeneratedSourceBundle.source_version.desc())
    return await db.scalar(query)


async def generate_source_for_plan(db, tenant_id: str, user_id: str, plan_row, plan, client=None):
    approved_version = int(plan_row.approved_version or 0)
    if not approved_version:
        raise ValueError("Source generation requires an approved plan")

    agent = OpenCodeStyleCodingAgent(client=client)
    result = await agent.build(_plan_specification(plan))
    application_id = f"plan-{plan_row.id}"
    source_version = (
        await db.scalar(
            select(func.max(GeneratedSourceBundle.source_version)).where(
                GeneratedSourceBundle.tenant_id == tenant_id,
                GeneratedSourceBundle.application_id == application_id,
            )
        )
        or 0
    ) + 1
    prompt = _prompt_text(plan)
    prompt_digest = "sha256:" + hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    bundle = build_bundle(
        result.files,
        tenant_id,
        application_id,
        plan_row.id,
        approved_version,
        source_version,
        prompt_digest,
    )
    provenance = {
        "harness": "opencode_style_v1",
        "agentMode": {"plan": "read_only", "build": "project_edit"},
        "terminalExecution": "isolated_runner_only",
        "agentPlan": result.plan[:20_000],
        "summary": result.summary[:4_000],
        "verificationIntent": result.verification,
        "toolTrace": [item.__dict__ for item in result.trace[-200:]],
        "originalPrompt": prompt,
        "secretValuesStored": False,
    }
    row = GeneratedSourceBundle(
        tenant_id=tenant_id,
        plan_id=plan_row.id,
        plan_version=approved_version,
        source_version=source_version,
        application_id=application_id,
        bundle_digest=bundle.digest,
        manifest_json=json.dumps(bundle.manifest, ensure_ascii=False),
        files_json=json.dumps(
            [
                {
                    "path": item.path,
                    "content": item.content.decode("utf-8"),
                    "generatedBy": item.generated_by,
                }
                for item in bundle.files
            ],
            ensure_ascii=False,
        ),
        provenance_json=json.dumps(provenance, ensure_ascii=False),
        created_by=user_id,
    )
    db.add(row)
    await db.flush()
    return row, result


def source_record_json(row) -> dict:
    manifest = json.loads(row.manifest_json)
    provenance = json.loads(row.provenance_json)
    return {
        "id": row.id,
        "planId": row.plan_id,
        "planVersion": row.plan_version,
        "applicationId": row.application_id,
        "sourceVersion": row.source_version,
        "bundleDigest": row.bundle_digest,
        "files": manifest.get("files", []),
        "totalBytes": manifest.get("totalBytes", 0),
        "harness": provenance.get("harness"),
        "agentMode": provenance.get("agentMode"),
        "terminalExecution": provenance.get("terminalExecution"),
        "summary": provenance.get("summary"),
        "verificationIntent": provenance.get("verificationIntent", []),
    }
