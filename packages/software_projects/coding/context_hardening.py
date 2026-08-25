"""Solution-scoped context and terminal fulfillment validation for Studio."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from typing import Any

from sqlalchemy import select

from packages.software_projects.coding.opencode_agent import CodingAgentNeedsUserInput
from packages.company.intelligence import context_for_subject
from packages.database.product_models import SolutionRecord
from packages.database.scope_models import SolutionContextSnapshot


_STOP = {
    "about", "after", "again", "also", "and", "are", "build", "create", "current",
    "for", "from", "have", "into", "make", "page", "please", "site", "solution",
    "that", "the", "this", "using", "want", "website", "will", "with", "your",
}
_STATEFUL_TERMS = {
    "attendance", "arrival", "arrivals", "departure", "departures", "checkin",
    "check-in", "checkout", "check-out", "logger", "logging", "record", "records",
    "track", "tracking",
}


def _loads(value: str | None, fallback):
    try:
        return json.loads(value or "")
    except Exception:
        return fallback


def digest(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def significant_terms(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9-]{2,}", str(value or "").lower())
        if token not in _STOP
    }


def visible_text(html: str) -> str:
    text = re.sub(r"(?is)<(script|style|svg)\b.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def source_map(files) -> dict[str, str]:
    records: dict[str, str] = {}
    for item in files:
        try:
            records[str(item.path)] = item.content.decode("utf-8", errors="strict")
        except Exception:
            continue
    return records


async def solution_for_project(db, tenant_id: str, project) -> SolutionRecord | None:
    return await db.scalar(
        select(SolutionRecord).where(
            SolutionRecord.tenant_id == tenant_id,
            SolutionRecord.runtime_type == "studio",
            SolutionRecord.runtime_reference == project.id,
        )
    )


def owner_objective(solution: SolutionRecord | None, project) -> str:
    context = _loads(solution.context_json if solution else "{}", {})
    owner_intent = context.get("ownerIntent") if isinstance(context.get("ownerIntent"), dict) else {}
    creation_intent = context.get("creationIntent") if isinstance(context.get("creationIntent"), dict) else {}
    for value in (
        owner_intent.get("objective"),
        creation_intent.get("objective"),
        context.get("owner_objective"),
        context.get("objective"),
        project.description,
    ):
        text = str(value or "").strip()
        if text:
            return text[:20_000]
    return f"Create {project.name}"[:20_000]


async def approved_solution_context(
    db,
    tenant_id: str,
    project,
    *,
    editor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    solution = await solution_for_project(db, tenant_id, project)
    raw = _loads(solution.context_json if solution else "{}", {})
    objective = owner_objective(solution, project)
    subject_reference = solution.id if solution else project.id
    scoped = await context_for_subject(
        db,
        tenant_id,
        subject_kind="solution",
        subject_reference=subject_reference,
        subject_name=solution.name if solution else project.name,
    )
    subject_payload = scoped.get("subject") or {}
    solution_profile = subject_payload.get("profile") or {}
    inherited = (scoped.get("workspace_inherited") or {}).get("profile") or {}
    unresolved = list(subject_payload.get("conflicts") or [])

    suppressed: list[dict[str, str]] = []
    legacy = raw.get("company_profile") if isinstance(raw.get("company_profile"), dict) else {}
    legacy_identity = str(
        legacy.get("display_name")
        or legacy.get("business_name")
        or legacy.get("description")
        or ""
    ).strip()
    if legacy_identity:
        project_terms = significant_terms(" ".join((project.name or "", project.description or "", objective)))
        legacy_terms = significant_terms(legacy_identity)
        if project_terms and legacy_terms and not (project_terms & legacy_terms):
            suppressed.append(
                {
                    "source": "legacy_workspace_company_profile",
                    "reason": "identity_conflicts_with_solution_scope",
                }
            )

    return {
        "scope": "solution",
        "solutionId": solution.id if solution else None,
        "solutionName": solution.name if solution else project.name,
        "projectId": project.id,
        "projectName": project.name,
        "projectDescription": project.description,
        "ownerObjective": objective,
        "solutionProfile": solution_profile,
        "workspaceInherited": inherited,
        "precedence": [
            "ownerObjective",
            "SolutionRecord",
            "solutionProfile",
            "workspaceInherited",
        ],
        "suppressedConflicts": suppressed,
        "unresolvedSolutionConflicts": unresolved,
        "editorContext": editor_context or {},
    }


def assert_context_consistent(context: dict[str, Any]) -> None:
    conflicts = [str(item) for item in context.get("unresolvedSolutionConflicts") or []]
    identity_conflicts = [
        item
        for item in conflicts
        if item
        in {
            "legal_name",
            "display_name",
            "business_name",
            "description",
            "brand",
            "business_type",
            "category",
            "website",
        }
    ]
    if identity_conflicts:
        raise CodingAgentNeedsUserInput(
            "Studio found unresolved Solution identity facts that conflict at the same authority level. "
            "Choose the intended product identity before source is changed.",
            [f"Resolve {item}" for item in identity_conflicts[:6]],
        )


def specification_text(context: dict[str, Any]) -> str:
    return "\n".join(
        [
            "OPERLY SOLUTION SOURCE SESSION",
            "",
            "AUTHORITATIVE OWNER OBJECTIVE",
            f"- {context.get('ownerObjective') or 'Not supplied'}",
            "",
            "SOLUTION IDENTITY",
            f"- Name: {context.get('projectName') or context.get('solutionName')}",
            f"- Description: {context.get('projectDescription') or 'Not supplied'}",
            "- Solution-scoped facts: "
            + json.dumps(context.get("solutionProfile") or {}, ensure_ascii=False, sort_keys=True)[:12_000],
            "- Explicitly inherited workspace facts (non-identity only): "
            + json.dumps(context.get("workspaceInherited") or {}, ensure_ascii=False, sort_keys=True)[:7_000],
            "- Authority order: owner objective > SolutionRecord > Solution profile > explicitly inherited workspace facts.",
            "- Never infer product or brand identity from unrelated workspace facts.",
            "",
            "CONTEXT CONSISTENCY",
            "- Suppressed lower-scope conflicts: "
            + (
                json.dumps(context.get("suppressedConflicts"), ensure_ascii=False)
                if context.get("suppressedConflicts")
                else "none"
            ),
            "- Same-scope unresolved identity conflicts stop before mutation and require owner input.",
            "",
            "FACTUAL GROUNDING",
            "- Concrete names, dates/years, prices, addresses, contacts, credentials, partnerships, metrics and claims must come from approved context.",
            "- Unknown facts stay unknown; do not manufacture plausible metadata.",
            "- Copyright year may be browser-current-year code; do not hard-code an unsupported year.",
        ]
    )[:80_000]


async def snapshot_context(
    db,
    tenant_id: str,
    project,
    *,
    run_id: str | None,
    created_by: str | None,
    context: dict[str, Any],
) -> None:
    solution = await solution_for_project(db, tenant_id, project)
    if solution is None:
        return
    payload = json.dumps(context, ensure_ascii=False, sort_keys=True, default=str)
    context_digest = digest(payload)
    existing = await db.scalar(
        select(SolutionContextSnapshot).where(
            SolutionContextSnapshot.tenant_id == tenant_id,
            SolutionContextSnapshot.solution_id == solution.id,
            SolutionContextSnapshot.run_id == run_id,
            SolutionContextSnapshot.context_digest == context_digest,
        )
    )
    if existing is not None:
        return
    db.add(
        SolutionContextSnapshot(
            tenant_id=tenant_id,
            solution_id=solution.id,
            project_id=project.id,
            run_id=run_id,
            owner_objective=str(context.get("ownerObjective") or "")[:20_000],
            context_json=payload,
            context_digest=context_digest,
            created_by=created_by,
        )
    )
    await db.flush()


def validate_javascript(records: dict[str, str]) -> list[dict[str, Any]]:
    javascript = [
        (path, text)
        for path, text in records.items()
        if path.lower().endswith((".js", ".mjs", ".cjs")) and text.strip()
    ]
    if not javascript:
        return []
    node = shutil.which("node")
    if not node:
        raise ValueError("JavaScript validation is required but the Node parser is unavailable")
    evidence: list[dict[str, Any]] = []
    for path, text in javascript:
        is_module = path.lower().endswith(".mjs") or bool(
            re.search(r"(?m)^\s*(?:import|export)\b", text)
        )
        suffix = ".mjs" if is_module else ".cjs"
        temporary = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=suffix, encoding="utf-8", delete=False) as handle:
                handle.write(text)
                temporary = handle.name
            proc = subprocess.run(
                [node, "--check", temporary],
                text=True,
                capture_output=True,
                timeout=8,
                check=False,
            )
        finally:
            if temporary:
                try:
                    import os
                    os.unlink(temporary)
                except OSError:
                    pass
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "JavaScript parse failed").strip()[:1800]
            raise ValueError(f"JavaScript syntax error in {path}: {detail}")
        evidence.append({"path": path, "parser": "node --check", "ok": True})
    return evidence


def validate_grounding(html: str, specification: str) -> dict[str, Any]:
    visible = visible_text(html)
    approved = specification.lower()
    approved_compact = re.sub(r"\s+", "", approved)
    violations: list[str] = []
    for match in re.finditer(r"\b(?:19|20)\d{2}\b", visible):
        if match.group(0) not in approved:
            violations.append(f"unsupported year {match.group(0)}")
    for match in re.finditer(r"(?i)\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", visible):
        if match.group(0).lower() not in approved:
            violations.append(f"unsupported email {match.group(0)}")
    phone_pattern = re.compile(r"(?<!\d)(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?!\d)")
    for match in phone_pattern.finditer(visible):
        if re.sub(r"\D", "", match.group(0)) not in re.sub(r"\D", "", approved):
            violations.append(f"unsupported phone {match.group(0)}")
    for match in re.finditer(r"(?<!\w)\$\s?\d[\d,.]*(?:\.\d{2})?", visible):
        if re.sub(r"\s+", "", match.group(0).lower()) not in approved_compact:
            violations.append(f"unsupported price {match.group(0)}")
    if violations:
        raise ValueError("Grounding validation failed: " + "; ".join(violations[:8]))
    return {"groundingChecked": True, "unsupportedFacts": 0}


def validate_semantics(html: str, specification: str) -> dict[str, Any]:
    visible = visible_text(html).lower()
    objective_match = re.search(
        r"AUTHORITATIVE OWNER OBJECTIVE\s*\n-\s*(.+?)(?:\n\n|$)",
        specification,
        flags=re.S,
    )
    objective = (
        objective_match.group(1).strip()
        if objective_match
        else specification[:3000]
    ).lower()
    wanted = significant_terms(objective)
    seen = significant_terms(visible)
    overlap = wanted & seen
    if len(wanted) >= 3 and not overlap:
        raise ValueError(
            "Semantic validation failed: generated artifact does not reflect the authoritative owner objective"
        )
    stateful = bool(wanted & _STATEFUL_TERMS)
    functional = bool(
        re.search(
            r"(?i)<(form|input|select|textarea|button)\b|localStorage|sessionStorage|indexedDB|fetch\s*\(",
            html,
        )
    )
    if stateful and not functional:
        raise ValueError(
            "Semantic validation failed: objective requires stateful logging/tracking but the artifact has no functional input/state boundary"
        )
    return {
        "objectiveChecked": True,
        "objectiveTermMatches": sorted(overlap)[:20],
        "statefulRequirement": stateful,
        "functionalBoundary": functional,
    }


def validate_hardened(files, specification: str, base_validator) -> dict[str, Any]:
    report = dict(base_validator(files, specification) or {})
    records = source_map(files)
    html = records.get("index.html", "")
    report.update(
        {
            "javascript": validate_javascript(records),
            **validate_grounding(html, specification),
            **validate_semantics(html, specification),
            "validationAuthority": "studio_hardening_v2",
        }
    )
    return report
