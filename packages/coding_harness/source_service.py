"""Persist coding-harness source and edits as immutable OPERLY source bundles."""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import func, select

from packages.coding_harness.opencode_agent import CodingHarnessError, CodingHarnessResult, OpenCodeStyleCodingAgent
from packages.coding_harness.runtime_resolution import RuntimeResolutionError, validate_runtime_contract
from packages.custom_software.source_bundles import SourceFile, build_bundle
from packages.database.custom_software_models import GeneratedSourceBundle


def _plan_specification(plan) -> str:
    data = plan.model_dump(mode="json") if hasattr(plan, "model_dump") else dict(plan)
    selected = {
        "projectName": data.get("projectName"),
        "summary": data.get("summary"),
        "effectiveRequirements": data.get("effectiveRequirements") or [],
        "requirementLedger": data.get("requirementLedger") or [],
        "planTree": data.get("planTree") or [],
        "globalValidation": data.get("globalValidation"),
        "unsupportedRequirements": data.get("unsupportedRequirements") or [],
        "operlyExecutionContract": {
            "tests": "Critical tests must run non-interactively in the isolated runner and must exercise generated application code.",
            "staticWeb": "For browser HTML/CSS/vanilla JavaScript, keep domain logic importable from tests and use Node built-ins only for runner verification.",
            "pythonStdlibWeb": "For Python standard-library web applications, provide app.py, build.py, and executable Python tests without third-party packages.",
            "execution": "Never execute code in the OPERLY control plane. The runner selects deterministic execution mechanics from the completed source tree.",
        },
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


def source_files_from_record(row: GeneratedSourceBundle) -> list[SourceFile]:
    try:
        records = json.loads(row.files_json)
        return [SourceFile(str(item["path"]), str(item["content"]).encode("utf-8"), str(item.get("generatedBy") or "coding_harness")) for item in records]
    except Exception as error:
        raise CodingHarnessError("Stored coding-harness source is invalid") from error


async def latest_source(db, tenant_id: str, plan_id: str, plan_version: int | None = None):
    query = select(GeneratedSourceBundle).where(GeneratedSourceBundle.tenant_id == tenant_id, GeneratedSourceBundle.plan_id == plan_id)
    if plan_version is not None:
        query = query.where(GeneratedSourceBundle.plan_version == plan_version)
    return await db.scalar(query.order_by(GeneratedSourceBundle.source_version.desc()))


async def _next_source_version(db, tenant_id: str, application_id: str) -> int:
    return int(await db.scalar(select(func.max(GeneratedSourceBundle.source_version)).where(GeneratedSourceBundle.tenant_id == tenant_id, GeneratedSourceBundle.application_id == application_id)) or 0) + 1


async def _persist_result(
    db,
    tenant_id: str,
    user_id: str,
    plan_row,
    plan,
    result: CodingHarnessResult,
    *,
    kind: str,
    parent: GeneratedSourceBundle | None = None,
    instruction: str | None = None,
    failure_evidence: dict | None = None,
):
    approved_version = int(plan_row.approved_version or 0)
    if not approved_version:
        raise ValueError("Source generation requires an approved plan")
    application_id = f"plan-{plan_row.id}"
    source_version = await _next_source_version(db, tenant_id, application_id)
    prompt = _prompt_text(plan)
    prompt_digest = "sha256:" + hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    bundle = build_bundle(result.files, tenant_id, application_id, plan_row.id, approved_version, source_version, prompt_digest)
    try:
        runtime_profile_id = validate_runtime_contract(bundle)
    except RuntimeResolutionError as error:
        raise CodingHarnessError(str(error)) from error

    provenance = {
        "harness": "opencode_style_v2",
        "modelProvider": "ollama",
        "agentMode": {"plan": "read_only", "build": "project_edit", "repair": "runner_feedback"},
        "terminalExecution": "isolated_runner_only",
        "sourceOperation": kind,
        "parentSourceBundleId": parent.id if parent else None,
        "instruction": (instruction or "")[:20_000],
        "failureEvidence": failure_evidence or {},
        "agentPlan": result.plan[:20_000],
        "summary": result.summary[:4_000],
        "verificationIntent": result.verification,
        "changedPaths": result.changed_paths,
        "toolTrace": [item.__dict__ for item in result.trace[-300:]],
        "originalPrompt": prompt,
        "semanticInput": "validated_requirement_ledger_and_plan_tree",
        "legacyPresentationFieldsUsed": False,
        "detectedRuntimeProfile": runtime_profile_id,
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
        files_json=json.dumps([{"path": item.path, "content": item.content.decode("utf-8"), "generatedBy": item.generated_by} for item in bundle.files], ensure_ascii=False),
        provenance_json=json.dumps(provenance, ensure_ascii=False),
        created_by=user_id,
    )
    db.add(row)
    await db.flush()
    return row, result


async def generate_source_for_plan(db, tenant_id: str, user_id: str, plan_row, plan, client=None):
    agent = OpenCodeStyleCodingAgent(client=client)
    result = await agent.build(_plan_specification(plan))
    return await _persist_result(db, tenant_id, user_id, plan_row, plan, result, kind="generate")


async def edit_source_for_plan(
    db,
    tenant_id: str,
    user_id: str,
    plan_row,
    plan,
    source: GeneratedSourceBundle,
    instruction: str,
    *,
    client=None,
    edit_kind: str = "source_edit",
    context: dict | None = None,
):
    agent = OpenCodeStyleCodingAgent(client=client)
    task = str(instruction or "").strip()
    if context:
        task += "\n\nEDITOR CONTEXT:\n" + json.dumps(context, ensure_ascii=False, sort_keys=True)[:15_000]
    result = await agent.edit(_plan_specification(plan), source_files_from_record(source), task)
    return await _persist_result(db, tenant_id, user_id, plan_row, plan, result, kind=edit_kind, parent=source, instruction=instruction)


async def repair_source_for_plan(
    db,
    tenant_id: str,
    user_id: str,
    plan_row,
    plan,
    source: GeneratedSourceBundle,
    failure_evidence: dict,
    *,
    client=None,
):
    agent = OpenCodeStyleCodingAgent(client=client)
    result = await agent.repair(_plan_specification(plan), source_files_from_record(source), failure_evidence)
    return await _persist_result(db, tenant_id, user_id, plan_row, plan, result, kind="runner_repair", parent=source, failure_evidence=failure_evidence)


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
        "modelProvider": provenance.get("modelProvider", "ollama"),
        "agentMode": provenance.get("agentMode"),
        "terminalExecution": provenance.get("terminalExecution"),
        "runtimeProfile": provenance.get("detectedRuntimeProfile"),
        "sourceOperation": provenance.get("sourceOperation"),
        "parentSourceBundleId": provenance.get("parentSourceBundleId"),
        "changedPaths": provenance.get("changedPaths", []),
        "summary": provenance.get("summary"),
        "verificationIntent": provenance.get("verificationIntent", []),
    }
