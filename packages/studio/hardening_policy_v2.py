"""Compose Studio defense-in-depth after the base website runtime policy."""
from __future__ import annotations

import base64
import json
import re
from typing import Any

from packages.studio.context_hardening import (
    approved_solution_context,
    assert_context_consistent,
    snapshot_context,
    specification_text,
    validate_hardened,
)
from packages.studio.model_provenance import (
    begin as begin_provenance,
    end as end_provenance,
    install_telemetry,
    summary as provenance_summary,
    wrap_client_factory,
)
from packages.studio.tool_hardening import install_tool_policy


_APPLIED = False


def _append_runtime_tail(scoped: str, legacy: str) -> str:
    marker = "WEBSITE RUNTIME"
    if marker not in legacy:
        return scoped[:80_000]
    tail = legacy.split(marker, 1)[1]
    return (scoped + "\n\n" + marker + tail)[:80_000]


def _flatten_production_html(row, solution_id: str, source_agent) -> str:
    """Render one safe self-contained HTML artifact from validated local source.

    Local JS is retained rather than stripped. Local ES-module imports are converted
    to integrity-preserving data URLs, so governed vendored dependencies work without
    allowing runtime CDN fetches.
    """
    records = source_agent.file_map(row)
    html = records.get("index.html", "")
    if not html:
        raise source_agent.CodingHarnessError("Website source has no index.html")

    def inline_css(match: re.Match[str]) -> str:
        tag = match.group(0)
        href_match = re.search(r"href=[\"']([^\"']+)[\"']", tag, re.I)
        if not href_match:
            return tag
        href = href_match.group(1).split("?", 1)[0].lstrip("./")
        if href in records and href.lower().endswith(".css"):
            return "<style>" + records[href].replace("</style", "<\\/style") + "</style>"
        return tag

    def module_data_url(path: str) -> str:
        clean = path.split("?", 1)[0].lstrip("./")
        text = records.get(clean)
        if text is None:
            raise source_agent.CodingHarnessError(
                f"Production module import is not a local validated source file: {path}"
            )
        # Recursively replace relative imports. Governed dependency files are local
        # source at this point and inherit the same no-network validation policy.
        def replace_import(match: re.Match[str]) -> str:
            prefix, target, suffix = match.group(1), match.group(2), match.group(3)
            if target.startswith(("http://", "https://", "//")):
                raise source_agent.CodingHarnessError("Remote production module imports are not allowed")
            if target.startswith(("./", "../")):
                # Studio's bounded source shape normally uses project-root relative
                # imports. Normalize simple ./ paths; reject traversal rather than
                # guessing a deployment path.
                if target.startswith("../"):
                    raise source_agent.CodingHarnessError("Parent-directory module imports are not supported")
                target = module_data_url(target)
            return prefix + target + suffix

        text = re.sub(
            r"(?m)(\b(?:from\s*|import\s*\(\s*)[\"'])([^\"']+)([\"'])",
            replace_import,
            text,
        )
        encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
        return "data:text/javascript;base64," + encoded

    def inline_script(match: re.Match[str]) -> str:
        tag = match.group(0)
        src_match = re.search(r"\bsrc=[\"']([^\"']+)[\"']", tag, re.I)
        if not src_match:
            return tag
        src = src_match.group(1)
        if src.startswith(("http://", "https://", "//")):
            raise source_agent.CodingHarnessError("Remote production scripts are not allowed")
        clean = src.split("?", 1)[0].lstrip("./")
        body = records.get(clean)
        if body is None:
            raise source_agent.CodingHarnessError(
                f"Production script is not a local validated source file: {src}"
            )
        is_module = bool(re.search(r"\btype=[\"']module[\"']", tag, re.I))
        if is_module:
            body_url = module_data_url(clean)
            return f'<script type="module">import {body_url!r};</script>'
        safe = body.replace("</script", "<\\/script")
        return "<script>" + safe + "</script>"

    html = re.sub(
        r"<link\b[^>]*rel=[\"']stylesheet[\"'][^>]*>",
        inline_css,
        html,
        flags=re.I,
    )
    html = re.sub(
        r"<script\b[^>]*\bsrc=[\"'][^\"']+[\"'][^>]*>\s*</script\s*>",
        inline_script,
        html,
        flags=re.I | re.S,
    )
    html = html.replace(
        "__OPERLY_FORM_ACTION__",
        f"/api/public/presence/{solution_id}/forms/contact",
    )
    if not html.lstrip().lower().startswith("<!doctype html>"):
        html = "<!doctype html>\n" + html
    return html


def apply_studio_hardening_policy() -> None:
    global _APPLIED
    if _APPLIED:
        return

    import apps.api.studio_source_router as source_router
    from packages.solutions import production
    from packages.studio import agent_runs, runtime_policy, source_agent

    install_telemetry()

    base_validator = runtime_policy.validate_studio_website

    def hardened_validator(files, specification: str = ""):
        return validate_hardened(files, specification, base_validator)

    runtime_policy.validate_studio_website = hardened_validator
    install_tool_policy(runtime_policy, hardened_validator)

    # The outer persist wrapper supplies the authoritative specification. Keep the
    # legacy _ensure_static check structural-only to avoid validating facts without
    # their approved context.
    def structural_static(result) -> str:
        base_validator(result.files)
        return "static-web-js"

    source_agent._ensure_static = structural_static

    runtime_context = source_agent.project_context

    async def scoped_project_context(
        db,
        tenant_id: str,
        project,
        *,
        editor_context: dict[str, Any] | None = None,
    ) -> str:
        approved = await approved_solution_context(
            db,
            tenant_id,
            project,
            editor_context=editor_context,
        )
        assert_context_consistent(approved)
        scoped = specification_text(approved)
        legacy = await runtime_context(
            db,
            tenant_id,
            project,
            editor_context=editor_context,
        )
        return _append_runtime_tail(scoped, legacy)

    source_agent.project_context = scoped_project_context
    agent_runs.project_context = scoped_project_context

    original_persist = source_agent._persist

    async def persist_with_validation(
        db,
        tenant_id: str,
        user_id: str,
        project,
        result,
        *,
        instruction: str,
        parent,
        editor_context,
        operation: str,
    ):
        approved = await approved_solution_context(
            db,
            tenant_id,
            project,
            editor_context=editor_context,
        )
        assert_context_consistent(approved)
        spec = await scoped_project_context(
            db,
            tenant_id,
            project,
            editor_context=editor_context,
        )
        report = hardened_validator(result.files, spec)
        row = await original_persist(
            db,
            tenant_id,
            user_id,
            project,
            result,
            instruction=instruction,
            parent=parent,
            editor_context=editor_context,
            operation=operation,
        )
        try:
            provenance = json.loads(row.provenance_json or "{}")
        except Exception:
            provenance = {}
        provenance["validationEvidence"] = report
        provenance["ownerObjective"] = approved.get("ownerObjective")
        provenance["contextScope"] = "solution"
        provenance["contextPrecedence"] = approved.get("precedence")
        provenance["suppressedContextConflicts"] = approved.get("suppressedConflicts")
        row.provenance_json = json.dumps(
            provenance,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        await db.flush()
        return row

    source_agent._persist = persist_with_validation
    agent_runs._persist = persist_with_validation

    original_run_agent = agent_runs._run_source_agent

    async def run_with_scope(db, run, project, context, progress):
        approved = await approved_solution_context(
            db,
            run.tenant_id,
            project,
            editor_context=context,
        )
        assert_context_consistent(approved)
        await snapshot_context(
            db,
            run.tenant_id,
            project,
            run_id=run.id,
            created_by=run.created_by,
            context=approved,
        )
        token = begin_provenance(run.id, run.tenant_id)
        try:
            return await original_run_agent(db, run, project, context, progress)
        finally:
            end_provenance(token)

    agent_runs._run_source_agent = run_with_scope

    agent_runs.coding_model_client = wrap_client_factory(agent_runs.coding_model_client)
    source_agent.coding_model_client = wrap_client_factory(source_agent.coding_model_client)

    original_create_run = agent_runs.create_run

    async def create_run_with_intent(
        db,
        tenant_id: str,
        user_id: str,
        project,
        *,
        operation: str,
        instruction: str = "",
        context: dict[str, Any] | None = None,
    ):
        task = str(instruction or "").strip()
        if operation == "generate" and not task:
            approved = await approved_solution_context(
                db,
                tenant_id,
                project,
                editor_context=context,
            )
            assert_context_consistent(approved)
            task = str(approved.get("ownerObjective") or "").strip()
        return await original_create_run(
            db,
            tenant_id,
            user_id,
            project,
            operation=operation,
            instruction=task,
            context=context,
        )

    agent_runs.create_run = create_run_with_intent
    source_router.create_run = create_run_with_intent

    original_run_json = agent_runs.run_json

    async def run_json_with_provenance(db, run):
        payload = await original_run_json(db, run)
        payload.update(await provenance_summary(db, run.id, run.tenant_id))
        return payload

    agent_runs.run_json = run_json_with_provenance
    source_router.run_json = run_json_with_provenance

    production_html = lambda row, solution_id: _flatten_production_html(
        row,
        solution_id,
        source_agent,
    )
    source_agent.production_html = production_html
    production.production_html = production_html

    _APPLIED = True
