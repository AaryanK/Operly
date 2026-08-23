"""Defense-in-depth policy for Studio source generation.

This module is installed after the existing Studio runtime policy. It deliberately
keeps the generic coding harness unchanged while making Studio's public success
state mean: current revision, governed dependencies, technical validity, grounded
facts, and semantic fulfillment of the owning Solution objective.
"""
from __future__ import annotations

import base64
import contextvars
import hashlib
import io
import json
import re
import shutil
import subprocess
import tarfile
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select

from packages.company.intelligence import context_for_subject
from packages.database.db import SessionFactory
from packages.database.product_models import SolutionRecord
from packages.database.scope_models import SolutionContextSnapshot, StudioModelAttempt
from packages.model_runtime import register_model_telemetry_sink
from packages.model_runtime.registry import ModelAttemptEvent


_STOP = {
    "about","after","again","also","and","are","build","create","current","for","from","have","into","make",
    "page","please","site","solution","that","the","this","with","website","your","will","want","should","using",
}
_STATEFUL_TERMS = {"attendance","arrival","arrivals","departure","departures","checkin","check-in","checkout","check-out","logger","logging","track","tracking","record","records"}
_APPROVED_DEPENDENCIES = {
    # Exact version pin. The installer resolves only registry.npmjs.org, verifies the
    # registry-provided sha512 integrity, and vendors a bounded browser artifact.
    "three": {
        "version": "0.180.0",
        "files": {"package/build/three.module.js": "vendor/three.module.js"},
    },
}


@dataclass
class _ProvenanceScope:
    run_id: str
    tenant_id: str
    model_turn: int = 0
    attempt_in_turn: int = 0
    rows: dict[tuple[int, str, str, int], str] = field(default_factory=dict)


_PROVENANCE: contextvars.ContextVar[_ProvenanceScope | None] = contextvars.ContextVar("operly_studio_provenance", default=None)
_TELEMETRY_INSTALLED = False
_APPLIED = False


def _loads(value, fallback):
    try:
        return json.loads(value or "")
    except Exception:
        return fallback


def _digest(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def _significant(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9-]{2,}", str(text or "").lower())
        if token not in _STOP
    }


def _visible_text(html: str) -> str:
    text = re.sub(r"(?is)<(script|style|svg)\b.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _source_map(files) -> dict[str, str]:
    out = {}
    for item in files:
        try:
            out[str(item.path)] = item.content.decode("utf-8")
        except Exception:
            continue
    return out


async def _solution_for_project(db, tenant_id: str, project) -> SolutionRecord | None:
    return await db.scalar(
        select(SolutionRecord).where(
            SolutionRecord.tenant_id == tenant_id,
            SolutionRecord.runtime_type == "studio",
            SolutionRecord.runtime_reference == project.id,
        )
    )


def _owner_objective(solution: SolutionRecord | None, project) -> str:
    context = _loads(solution.context_json if solution else "{}", {})
    candidates = [
        (context.get("ownerIntent") or {}).get("objective") if isinstance(context.get("ownerIntent"), dict) else None,
        (context.get("creationIntent") or {}).get("objective") if isinstance(context.get("creationIntent"), dict) else None,
        context.get("owner_objective"),
        context.get("objective"),
        project.description,
    ]
    for item in candidates:
        text = str(item or "").strip()
        if text:
            return text[:20_000]
    return f"Create {project.name}"[:20_000]


async def _solution_specification(db, tenant_id: str, project, *, editor_context: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    solution = await _solution_for_project(db, tenant_id, project)
    raw_context = _loads(solution.context_json if solution else "{}", {})
    objective = _owner_objective(solution, project)
    scoped = await context_for_subject(
        db,
        tenant_id,
        subject_kind="solution",
        subject_reference=solution.id if solution else project.id,
        subject_name=solution.name if solution else project.name,
    )
    solution_profile = (scoped.get("subject") or {}).get("profile") or {}
    inherited = (scoped.get("workspace_inherited") or {}).get("profile") or {}

    # Legacy Solution context sometimes embedded the entire tenant CompanyProfile.
    # It is explicitly suppressed as product identity. Record the conflict so a
    # lower-scope legacy profile can never silently override the current Solution.
    suppressed = []
    legacy_profile = raw_context.get("company_profile") if isinstance(raw_context.get("company_profile"), dict) else {}
    legacy_identity = str(legacy_profile.get("display_name") or legacy_profile.get("business_name") or legacy_profile.get("description") or "").strip()
    if legacy_identity:
        project_terms = _significant(" ".join((project.name or "", project.description or "", objective)))
        legacy_terms = _significant(legacy_identity)
        if project_terms and legacy_terms and not (project_terms & legacy_terms):
            suppressed.append({"source":"legacy_workspace_company_profile","reason":"identity_conflicts_with_solution_scope"})

    approved_context = {
        "scope": "solution",
        "solutionId": solution.id if solution else None,
        "solutionName": solution.name if solution else project.name,
        "projectId": project.id,
        "projectName": project.name,
        "projectDescription": project.description,
        "ownerObjective": objective,
        "solutionProfile": solution_profile,
        "workspaceInherited": inherited,
        "precedence": ["ownerObjective", "SolutionRecord", "solutionProfile", "workspaceInherited"],
        "suppressedConflicts": suppressed,
        "editorContext": editor_context or {},
    }
    text = "\n".join(
        [
            "OPERLY SOLUTION SOURCE SESSION",
            "",
            "AUTHORITATIVE OWNER OBJECTIVE",
            f"- {objective}",
            "",
            "SOLUTION IDENTITY",
            f"- Name: {project.name}",
            f"- Description: {project.description or 'Not supplied'}",
            f"- Solution-scoped facts: {json.dumps(solution_profile, ensure_ascii=False, sort_keys=True)[:12000]}",
            f"- Explicitly inherited workspace facts (non-identity only): {json.dumps(inherited, ensure_ascii=False, sort_keys=True)[:7000]}",
            "- Authority order is owner objective > SolutionRecord > Solution profile > explicitly inherited workspace facts.",
            "- Never infer product/brand identity from unrelated workspace facts.",
            "",
            "CONTEXT CONSISTENCY",
            f"- Suppressed lower-scope conflicts: {json.dumps(suppressed, ensure_ascii=False) if suppressed else 'none'}",
            "- If same-scope authoritative inputs materially contradict one another, ask the owner before mutation.",
            "",
            "FACTUAL GROUNDING",
            "- Concrete names, dates/years, prices, addresses, contacts, credentials, partnerships, metrics and claims must come from the approved context above.",
            "- Unknown facts stay unknown; do not manufacture plausible metadata.",
            "- A copyright year may be rendered dynamically from the browser current year, but do not hard-code an unsupported year.",
        ]
    )
    return text[:80_000], approved_context


async def _snapshot_context(db, tenant_id: str, project, run_id: str | None, created_by: str | None, approved: dict[str, Any]) -> None:
    solution = await _solution_for_project(db, tenant_id, project)
    if not solution:
        return
    payload = json.dumps(approved, ensure_ascii=False, sort_keys=True, default=str)
    digest = _digest(payload)
    existing = await db.scalar(
        select(SolutionContextSnapshot).where(
            SolutionContextSnapshot.tenant_id == tenant_id,
            SolutionContextSnapshot.solution_id == solution.id,
            SolutionContextSnapshot.run_id == run_id,
            SolutionContextSnapshot.context_digest == digest,
        )
    )
    if existing:
        return
    db.add(
        SolutionContextSnapshot(
            tenant_id=tenant_id,
            solution_id=solution.id,
            project_id=project.id,
            run_id=run_id,
            owner_objective=str(approved.get("ownerObjective") or "")[:20000],
            context_json=payload,
            context_digest=digest,
            created_by=created_by,
        )
    )
    await db.flush()


def _check_js_syntax(records: dict[str, str]) -> list[dict[str, Any]]:
    javascript = [(path, text) for path, text in records.items() if path.lower().endswith((".js", ".mjs", ".cjs")) and text.strip()]
    if not javascript:
        return []
    node = shutil.which("node")
    if not node:
        raise ValueError("JavaScript validation is required but the Node parser is unavailable")
    evidence = []
    for path, text in javascript:
        mode = "module" if path.lower().endswith(".mjs") or re.search(r"(?m)^\s*(?:import|export)\b", text) else "script"
        suffix = ".mjs" if mode == "module" else ".js"
        proc = subprocess.run(
            [node, "--check", "-" + suffix],
            input=text,
            text=True,
            capture_output=True,
            timeout=8,
        )
        # node --check does not consistently accept stdin with a synthetic suffix;
        # fall back to --input-type while still parsing only, never executing.
        if proc.returncode != 0:
            args = [node, "--check", "--input-type=" + ("module" if mode == "module" else "commonjs")]
            proc = subprocess.run(args, input=text, text=True, capture_output=True, timeout=8)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "JavaScript parse failed").strip()[:1800]
            raise ValueError(f"JavaScript syntax error in {path}: {detail}")
        evidence.append({"path": path, "parser": "node --check", "ok": True})
    return evidence


def _grounding_check(html: str, specification: str) -> dict[str, Any]:
    visible = _visible_text(html)
    approved = specification.lower()
    violations = []
    for match in re.finditer(r"\b(?:19|20)\d{2}\b", visible):
        year = match.group(0)
        if year not in approved:
            violations.append(f"unsupported year {year}")
    for match in re.finditer(r"(?i)\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", visible):
        if match.group(0).lower() not in approved:
            violations.append(f"unsupported email {match.group(0)}")
    for match in re.finditer(r"(?<!\w)\$\s?\d[\d,.]*(?:\.\d{2})?", visible):
        if re.sub(r"\s+", "", match.group(0).lower()) not in re.sub(r"\s+", "", approved):
            violations.append(f"unsupported price {match.group(0)}")
    if violations:
        raise ValueError("Grounding validation failed: " + "; ".join(violations[:8]))
    return {"groundingChecked": True, "unsupportedFacts": 0}


def _semantic_check(html: str, specification: str) -> dict[str, Any]:
    visible = _visible_text(html).lower()
    objective_match = re.search(r"AUTHORITATIVE OWNER OBJECTIVE\s*\n-\s*(.+?)(?:\n\n|$)", specification, re.S)
    objective = (objective_match.group(1).strip() if objective_match else specification[:3000]).lower()
    wanted = _significant(objective)
    seen = _significant(visible)
    overlap = wanted & seen
    if len(wanted) >= 3 and not overlap:
        raise ValueError("Semantic validation failed: generated artifact does not reflect the authoritative owner objective")
    stateful = bool(wanted & _STATEFUL_TERMS)
    functional = bool(re.search(r"(?i)<(form|input|select|textarea|button)\b|localStorage|sessionStorage|indexedDB|fetch\s*\(", html))
    if stateful and not functional:
        raise ValueError("Semantic validation failed: objective requires stateful logging/tracking but the artifact contains no functional input/state boundary")
    return {"objectiveChecked": True, "objectiveTermMatches": sorted(overlap)[:20], "statefulRequirement": stateful, "functionalBoundary": functional}


def validate_hardened(files, specification: str, base_validator) -> dict[str, Any]:
    report = dict(base_validator(files, specification) or {})
    records = _source_map(files)
    html = records.get("index.html", "")
    js = _check_js_syntax(records)
    grounding = _grounding_check(html, specification)
    semantic = _semantic_check(html, specification)
    report.update({"javascript": js, **grounding, **semantic, "validationAuthority": "studio_hardening_v2"})
    return report


def _reject_remote_dependency_fragment(value: str) -> None:
    if re.search(r"(?i)<script\b[^>]*\bsrc\s*=\s*[\"']https?://", str(value or "")):
        raise ValueError("Remote scripts are blocked at mutation time. Use dependency.add for an approved pinned package.")


def _install_dependency(workspace, name: str, version: str) -> dict[str, Any]:
    policy = _APPROVED_DEPENDENCIES.get(name)
    if not policy or version != policy["version"]:
        raise ValueError(f"Dependency is not approved. Allowed: {', '.join(f'{k}@{v['version']}' for k,v in _APPROVED_DEPENDENCIES.items())}")
    metadata_url = f"https://registry.npmjs.org/{name}/{version}"
    req = urllib.request.Request(metadata_url, headers={"User-Agent":"Operly-Studio-Dependency/1"})
    with urllib.request.urlopen(req, timeout=12) as response:
        if response.geturl().split("/", 3)[:3] != ["https:", "", "registry.npmjs.org"]:
            raise ValueError("Dependency registry redirect left the approved host")
        metadata = json.loads(response.read(1_000_000).decode("utf-8"))
    dist = metadata.get("dist") or {}
    tarball = str(dist.get("tarball") or "")
    integrity = str(dist.get("integrity") or "")
    if not tarball.startswith("https://registry.npmjs.org/") or not integrity.startswith("sha512-"):
        raise ValueError("Approved package metadata did not provide expected registry integrity")
    with urllib.request.urlopen(urllib.request.Request(tarball, headers={"User-Agent":"Operly-Studio-Dependency/1"}), timeout=20) as response:
        body = response.read(8_000_000)
    expected = base64.b64decode(integrity.split("-", 1)[1])
    actual = hashlib.sha512(body).digest()
    if actual != expected:
        raise ValueError("Dependency integrity verification failed")
    installed = []
    with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as archive:
        members = {member.name: member for member in archive.getmembers() if member.isfile()}
        for source_path, target_path in policy["files"].items():
            member = members.get(source_path)
            if not member or member.size > 4_000_000:
                raise ValueError(f"Approved dependency artifact missing or oversized: {source_path}")
            content = archive.extractfile(member).read().decode("utf-8")
            workspace.write(target_path, content)
            installed.append(target_path)
    lock = {
        "schemaVersion": 1,
        "dependencies": {name: {"version": version, "integrity": integrity, "registry": "https://registry.npmjs.org", "files": installed}},
    }
    workspace.write("operly.dependencies.json", json.dumps(lock, indent=2, sort_keys=True) + "\n")
    return {"ok": True, "name": name, "version": version, "integrity": integrity, "files": installed, "lockfile": "operly.dependencies.json"}


class _StudioClient:
    def __init__(self, inner):
        self.inner = inner

    async def chat(self, messages, tools=None):
        scope = _PROVENANCE.get()
        if scope:
            scope.model_turn += 1
            scope.attempt_in_turn = 0
        return await self.inner.chat(messages, tools)


def _wrap_client_factory(factory):
    def wrapped(*args, **kwargs):
        return _StudioClient(factory(*args, **kwargs))
    return wrapped


async def _telemetry(event: ModelAttemptEvent) -> None:
    scope = _PROVENANCE.get()
    if not scope:
        return
    if event.phase == "start":
        scope.attempt_in_turn += 1
        row = StudioModelAttempt(
            tenant_id=scope.tenant_id,
            run_id=scope.run_id,
            model_turn_index=max(1, scope.model_turn),
            provider_attempt_index=scope.attempt_in_turn,
            provider=event.provider,
            model_resource_id=event.resource_id,
            provider_model_id=event.provider_model_id,
            outcome="started",
            started_at=datetime.utcnow(),
        )
        async with SessionFactory() as db:
            db.add(row);await db.commit();await db.refresh(row)
        scope.rows[(scope.model_turn, event.resource_id, event.provider_model_id, event.attempt)] = row.id
        return
    key = (scope.model_turn, event.resource_id, event.provider_model_id, event.attempt)
    row_id = scope.rows.get(key)
    async with SessionFactory() as db:
        row = await db.get(StudioModelAttempt, row_id) if row_id else None
        if not row:
            row = StudioModelAttempt(
                tenant_id=scope.tenant_id,run_id=scope.run_id,model_turn_index=max(1,scope.model_turn),provider_attempt_index=max(1,scope.attempt_in_turn),provider=event.provider,model_resource_id=event.resource_id,provider_model_id=event.provider_model_id,outcome=event.phase,started_at=datetime.utcnow(),
            );db.add(row)
        row.outcome = "succeeded" if event.phase == "success" else "failed"
        row.error_classification = event.classification
        row.failover_reason = (event.detail or "")[:300] or ("retryable provider failure" if event.retryable else None)
        row.latency_ms = event.latency_ms
        row.completed_at = datetime.utcnow()
        await db.commit()


async def _attempt_summary(db, run_id: str, tenant_id: str) -> dict[str, Any]:
    rows = list((await db.scalars(select(StudioModelAttempt).where(StudioModelAttempt.run_id==run_id,StudioModelAttempt.tenant_id==tenant_id).order_by(StudioModelAttempt.model_turn_index,StudioModelAttempt.provider_attempt_index))).all())
    models=[]
    for row in rows:
        label=row.provider_model_id or row.model_resource_id
        if label and label not in models:models.append(label)
    winners=[row for row in rows if row.outcome=="succeeded"]
    winner=(winners[-1].provider_model_id or winners[-1].model_resource_id) if winners else None
    return {"modelsParticipated":models,"winningModel":winner,"attemptCount":len(rows),"modelAttempts":[{"turn":r.model_turn_index,"attempt":r.provider_attempt_index,"provider":r.provider,"resourceId":r.model_resource_id,"modelId":r.provider_model_id,"outcome":r.outcome,"classification":r.error_classification,"failoverReason":r.failover_reason,"latencyMs":r.latency_ms} for r in rows]}


def apply_studio_hardening_policy() -> None:
    global _APPLIED, _TELEMETRY_INSTALLED
    if _APPLIED:
        return
    from packages.studio import agent_runs, runtime_policy, source_agent
    import apps.api.studio_source_router as source_router

    if not _TELEMETRY_INSTALLED:
        register_model_telemetry_sink(_telemetry)
        _TELEMETRY_INSTALLED = True

    base_validator = runtime_policy.validate_studio_website

    def hardened_validator(files, specification: str = ""):
        return validate_hardened(files, specification, base_validator)

    runtime_policy.validate_studio_website = hardened_validator

    original_for_mode = runtime_policy.StudioWebsiteToolRegistry.for_mode
    def hardened_for_mode(self, mode, *, visual: bool, web: bool):
        tools = original_for_mode(self, mode, visual=visual, web=web)
        read = tools.get("read")
        if read:
            async def read_with_revision(args, session, _execute=read.execute):
                result = await _execute(args, session)
                path = str(args.get("path") or "")
                try: result["revision"] = _digest(session.workspace.raw(path))
                except Exception: pass
                return result
            tools["read"] = runtime_policy.coding.CodingTool(read.name, read.description + " Returns a revision digest for revision-aware range edits.", read.properties, read.required, read.modes, read_with_revision)
        if mode != "plan":
            async def replace_revision(args, session):
                path=str(args.get("path") or "");expected=str(args.get("revision") or "").strip()
                if not expected:raise runtime_policy.coding.WorkspacePolicyError("replace_range requires the revision returned by read")
                current=session.workspace.raw(path);actual=_digest(current)
                if actual!=expected:raise runtime_policy.coding.WorkspacePolicyError(f"stale_edit_conflict: expected revision {expected[:12]}, current revision {actual[:12]}; reread before editing")
                _reject_remote_dependency_fragment(str(args.get("content") or ""))
                runtime_policy._replace_range(session.workspace,path,int(args.get("start_line") or 0),int(args.get("end_line") or 0),str(args.get("content") or ""))
                return {"ok":True,"path":path,"previousRevision":expected,"revision":_digest(session.workspace.raw(path))}
            tools["replace_range"] = runtime_policy.coding.CodingTool("replace_range","Replace an inclusive line range only when the supplied revision still matches the file. Reread after any source mutation.",{"path":runtime_policy.coding.TEXT,"revision":runtime_policy.coding.TEXT,"start_line":runtime_policy.coding.INTEGER,"end_line":runtime_policy.coding.INTEGER,"content":runtime_policy.coding.TEXT},("path","revision","start_line","end_line","content"),frozenset({"build","edit","repair"}),replace_revision)
            async def dependency_add(args, session):
                return await runtime_policy.asyncio.to_thread(_install_dependency,session.workspace,str(args.get("name") or "").strip().lower(),str(args.get("version") or "").strip())
            tools["dependency.add"] = runtime_policy.coding.CodingTool("dependency.add","Install an approved exact-version browser dependency through Operly's governed npm registry path. Never add remote CDN script tags.",{"name":{"type":"string","enum":sorted(_APPROVED_DEPENDENCIES)},"version":runtime_policy.coding.TEXT},("name","version"),frozenset({"build","edit","repair"}),dependency_add)
            for name in ("write","edit"):
                tool=tools.get(name)
                if not tool:continue
                async def guarded(args, session, _execute=tool.execute, _name=name):
                    _reject_remote_dependency_fragment(str(args.get("content") or args.get("new") or ""))
                    result=await _execute(args,session)
                    if bool(result.get("ok",False)):
                        # Validate immediately after source mutations so unsafe remote
                        # dependencies do not survive until terminal finish.
                        try:hardened_validator(session.workspace.source_files(), runtime_policy._approved_specification(session))
                        except Exception as error:
                            result["validationWarning"]=str(error)[:1200]
                    return result
                tools[name]=runtime_policy.coding.CodingTool(tool.name,tool.description,tool.properties,tool.required,tool.modes,guarded)
        return tools
    runtime_policy.StudioWebsiteToolRegistry.for_mode = hardened_for_mode

    original_context = agent_runs.project_context
    async def scoped_context(db, tenant_id, project, *, editor_context=None):
        text, approved = await _solution_specification(db, tenant_id, project, editor_context=editor_context)
        # Keep runtime instructions from the existing policy, but discard its
        # tenant-global BUSINESS CONTEXT section by taking only runtime/design tail.
        legacy = await original_context(db, tenant_id, project, editor_context=editor_context)
        tail = legacy.split("WEBSITE RUNTIME",1)[1] if "WEBSITE RUNTIME" in legacy else ""
        return (text + "\n\nWEBSITE RUNTIME" + tail)[:80000]
    agent_runs.project_context = scoped_context
    source_agent.project_context = scoped_context

    original_run_agent = agent_runs._run_source_agent
    async def run_with_scope(db, run, project, context, progress):
        text, approved = await _solution_specification(db, run.tenant_id, project, editor_context=context)
        await _snapshot_context(db, run.tenant_id, project, run.id, run.created_by, approved)
        token=_PROVENANCE.set(_ProvenanceScope(run.id,run.tenant_id))
        try:return await original_run_agent(db,run,project,context,progress)
        finally:_PROVENANCE.reset(token)
    agent_runs._run_source_agent = run_with_scope

    agent_runs.coding_model_client = _wrap_client_factory(agent_runs.coding_model_client)
    source_agent.coding_model_client = _wrap_client_factory(source_agent.coding_model_client)

    original_create = agent_runs.create_run
    async def create_with_owner_intent(db, tenant_id, user_id, project, *, operation, instruction="", context=None):
        task=str(instruction or "").strip()
        if operation=="generate" and not task:
            solution=await _solution_for_project(db,tenant_id,project)
            task=_owner_objective(solution,project)
        return await original_create(db,tenant_id,user_id,project,operation=operation,instruction=task,context=context)
    agent_runs.create_run=create_with_owner_intent
    source_router.create_run=create_with_owner_intent

    original_run_json=agent_runs.run_json
    async def run_json_with_attempts(db, run):
        payload=await original_run_json(db,run);payload.update(await _attempt_summary(db,run.id,run.tenant_id));return payload
    agent_runs.run_json=run_json_with_attempts
    source_router.run_json=run_json_with_attempts

    _APPLIED=True
