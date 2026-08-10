"""Persist coding-agent source and edits as immutable OPERLY source bundles."""
from __future__ import annotations

import hashlib
import json
import os

from sqlalchemy import func, select

from packages.coding_harness.opencode_agent import CodingHarnessError, CodingHarnessResult, OpenCodeStyleCodingAgent
from packages.coding_harness.runtime_resolution import RuntimeResolutionError, validate_runtime_contract
from packages.custom_software.source_bundles import SourceFile, build_bundle
from packages.database.custom_software_models import GeneratedSourceBundle


def _compact_requirement(item: dict) -> dict:
    exact = str(item.get("exactText") or "").strip()
    normalized = str(item.get("normalizedMeaning") or exact).strip()
    result = {
        "id": item.get("id"),
        "requirement": normalized,
        "mandatory": bool(item.get("mandatory", True)),
        "acceptance": list(item.get("acceptanceCriteria") or []),
        "nodeIds": list(item.get("relatedPlanNodeIds") or []),
    }
    if exact and exact != normalized:
        result["source"] = exact
    exclusions = list(item.get("exclusions") or [])
    if exclusions:
        result["exclusions"] = exclusions
    return result


def _compact_node(item: dict) -> dict:
    return {
        "id": item.get("id"),
        "title": item.get("title"),
        "objective": item.get("objective") or item.get("description"),
        "responsibilities": list(item.get("responsibilities") or []),
        "requirementIds": list(item.get("originalRequirementIds") or []),
        "dependencies": list(item.get("dependencies") or []),
        "inputs": list(item.get("inputs") or []),
        "outputs": list(item.get("outputs") or []),
        "invariants": list(item.get("invariants") or []),
        "failureCases": list(item.get("failureCases") or []),
        "security": list(item.get("securityRequirements") or []),
        "persistence": list(item.get("persistenceBehavior") or []),
        "tests": list(item.get("requiredTests") or []),
    }


def _plan_specification(plan) -> str:
    """Return only the semantic contract the coding loop needs.

    Historical presentation fields, duplicate effective-requirement lists, planner
    validation prose and provenance stay in persistence. Re-sending them on every
    coding turn wastes context without giving the coding agent new authority.
    """
    data = plan.model_dump(mode="json") if hasattr(plan, "model_dump") else dict(plan)
    requirements = [
        _compact_requirement(item)
        for item in (data.get("requirementLedger") or [])
        if isinstance(item, dict)
    ]
    graph = [
        _compact_node(item)
        for item in (data.get("planTree") or [])
        if isinstance(item, dict)
    ]
    selected = {
        "projectName": data.get("projectName"),
        "objective": data.get("summary") or data.get("primaryGoal"),
        "requirements": requirements,
        "capabilityGraph": graph,
        "globalValidation": data.get("globalValidation"),
        "unsupportedRequirements": data.get("unsupportedRequirements") or [],
        "operlyExecutionContract": {
            "tests": "Critical acceptance tests run non-interactively in the isolated runner and exercise generated application code.",
            "staticWeb": "For browser HTML/CSS/vanilla JavaScript, keep domain logic importable from tests and use Node built-ins only for runner verification.",
            "pythonStdlibWeb": "For Python standard-library web applications, provide app.py, build.py, and executable Python tests without third-party packages.",
            "execution": "Never execute code in the OPERLY control plane. The runner selects deterministic execution mechanics from the completed source tree.",
            "implementationFreedom": "Choose framework, storage, protocol and internal interface mechanics from the approved behavior and available runtime unless the requirement ledger explicitly constrains them.",
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
        "harness": "operly_tool_loop_v1",
        "inspiration": "opencode_session_tool_loop",
        "modelProvider": "ollama",
        "agentMode": {
            "planning": "read_only_permission_mode_available",
            "coding": "persistent_project_tool_loop",
            "repair": "same_agent_with_runner_feedback",
            "visual": "studio_dom_observation_to_source_mapping",
        },
        "terminalExecution": "isolated_runner_only",
        "webTools": "ollama_web_search_and_fetch_when_enabled",
        "sourceOperation": kind,
        "parentSourceBundleId": parent.id if parent else None,
        "instruction": (instruction or "")[:20_000],
        "failureEvidence": failure_evidence or {},
        "agentPlan": result.plan[:20_000],
        "summary": result.summary[:4_000],
        "verificationIntent": result.verification,
        "changedPaths": result.changed_paths,
        "toolTrace": [item.__dict__ for item in result.trace[-400:]],
        "originalPrompt": prompt,
        "semanticInput": "compact_requirement_ledger_and_dynamic_capability_graph",
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


def _contract_repair_budget() -> int:
    try:
        value = int(os.getenv("OPERLY_CODING_SOURCE_CONTRACT_REPAIRS", "2"))
    except ValueError:
        value = 2
    return max(0, min(value, 3))


async def _persist_with_contract_repair(
    db,
    tenant_id: str,
    user_id: str,
    plan_row,
    plan,
    agent: OpenCodeStyleCodingAgent,
    result: CodingHarnessResult,
    *,
    kind: str,
    parent: GeneratedSourceBundle | None = None,
    instruction: str | None = None,
    failure_evidence: dict | None = None,
):
    """Feed deterministic source-contract failures back into the same agent/workspace semantics."""
    specification = _plan_specification(plan)
    current = result
    budget = _contract_repair_budget()
    for attempt in range(budget + 1):
        try:
            return await _persist_result(
                db,
                tenant_id,
                user_id,
                plan_row,
                plan,
                current,
                kind=kind,
                parent=parent,
                instruction=instruction,
                failure_evidence=failure_evidence,
            )
        except CodingHarnessError as error:
            if attempt >= budget:
                raise
            current = await agent.repair(
                specification,
                current.files,
                {
                    "classification": "source_contract_failure",
                    "message": str(error),
                    "instruction": "Repair only what is necessary to satisfy the deterministic OPERLY source/runtime contract while preserving requested product behavior.",
                    "contractRepairAttempt": attempt + 1,
                },
            )
    raise CodingHarnessError("Source contract repair budget exhausted")


async def generate_source_for_plan(db, tenant_id: str, user_id: str, plan_row, plan, client=None, progress_callback=None):
    agent = OpenCodeStyleCodingAgent(client=client, progress_callback=progress_callback)
    result = await agent.build(_plan_specification(plan))
    return await _persist_with_contract_repair(db, tenant_id, user_id, plan_row, plan, agent, result, kind="generate")


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
    result = await agent.edit(
        _plan_specification(plan),
        source_files_from_record(source),
        task,
        context=context or {},
    )
    return await _persist_with_contract_repair(db, tenant_id, user_id, plan_row, plan, agent, result, kind=edit_kind, parent=source, instruction=instruction)


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
    return await _persist_with_contract_repair(db, tenant_id, user_id, plan_row, plan, agent, result, kind="runner_repair", parent=source, failure_evidence=failure_evidence)


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
