"""Studio-specific website runtime policy.

The general coding harness is intentionally strict enough for arbitrary business
applications. Studio websites need a narrower contract: native HTML behavior stays
native, business claims stay grounded in supplied context, and focused edits should
not spend model turns manufacturing application interaction manifests.

This module installs that policy at application startup without weakening the
shared custom-software harness.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Any

from packages.coding_harness import opencode_agent as coding


STUDIO_WEBSITE_SYSTEM = """
You are OPERLY's website source agent working inside one persistent Studio project.
The owner is editing a public business website, not a generic JavaScript application.
Use project tools to inspect and modify real source files; do not return a giant code
dump when tools can change the workspace.

WEBSITE RULES
- The owner instruction, current source, business facts, selected element, recent Studio conversation, and authorized capability summaries are your working context.
- Business facts are authoritative data. Never invent testimonials, customer counts, years in business, destinations served, awards, ratings, prices, credentials, guarantees, partnerships, locations, or other concrete claims.
- If a factual marketing claim is not supported by supplied business context, omit it or use non-quantitative copy. Do not turn missing facts into plausible-sounding facts.
- Ordinary anchors with href, semantic navigation, native GET/POST forms, inputs inside those forms, and intrinsic browser controls do NOT need data-operly-interaction ids, JavaScript handlers, requirement IDs, state probes, domain operations, or operly.interactions.json entries.
- Only add JavaScript when the requested experience actually needs scripted behavior. Keep core navigation, reading, responsive layout, contact details, and server-bound contact forms useful without JavaScript.
- A contact form may use POST action __OPERLY_FORM_ACTION__ with name, email, message, and a hidden website honeypot. Do not call private Operly APIs from generated JavaScript.
- Do not create or repair operly.interactions.json for a normal content, localization, layout, color, typography, navigation, or native-form edit.
- For visual/copy/localization edits, inspect index.html and styles.css first. Do not read app.js, tests, or interaction artifacts unless the requested change actually touches scripted behavior.
- Preserve unrelated working source. A focused request should normally need only a few reads/edits, not a site-wide rewrite.
- Before using edit, read the current file. If an exact edit is rejected, reread once and use corrected arguments or rewrite the bounded file; never repeat the identical failed edit call.
- Use write when the intended bounded file is small enough and an exact replacement is needlessly brittle.
- Keep the site responsive and accessible, with readable contrast, visible focus states, semantic headings, and no horizontal overflow.
- Never write secrets, credentials, .env files, analytics beacons, trackers, or authentication code.
- Web search is optional evidence only when current public documentation is genuinely required; never use it to invent business facts.
- Finish as soon as the requested website change is coherent and the Studio website contract passes. Do not add application-only scaffolding merely to satisfy yourself.
""".strip()


class StudioWebsiteContractError(ValueError):
    pass


def _source_map(files) -> dict[str, str]:
    return {item.path: item.content.decode("utf-8", errors="replace") for item in files}


def _approved_specification(session: coding.CodingSession) -> str:
    try:
        packet = json.loads(str(session.messages[1].get("content") or "{}"))
        return str(packet.get("approvedSpecification") or "")
    except Exception:
        return ""


def _business_facts_text(specification: str) -> str:
    match = re.search(r"(?m)^- Known facts:\s*(.+)$", specification or "")
    return (match.group(1) if match else "").lower()


def _visible_text(html: str) -> str:
    text = re.sub(r"<script\b[^>]*>.*?</script\s*>", " ", html, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style\s*>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def validate_studio_website(files, specification: str = "") -> dict[str, Any]:
    """Validate a static Studio website without generic app-only interaction rules."""
    records = _source_map(files)
    html = records.get("index.html", "")
    if not html.strip():
        raise StudioWebsiteContractError("Website source must include index.html")
    lowered = html.lower()
    if "<html" not in lowered:
        raise StudioWebsiteContractError("index.html must contain an html document")
    if len(html.encode("utf-8")) > 1_500_000:
        raise StudioWebsiteContractError("index.html exceeds the Studio website size limit")
    if re.search(r"(?i)<\s*(object|embed)\b|javascript\s*:", html):
        raise StudioWebsiteContractError("Website source contains unsafe embedded or javascript: content")
    if re.search(r"(?i)<script\b[^>]*\bsrc=[\"']https?://", html):
        raise StudioWebsiteContractError("Third-party script URLs are not allowed in Studio website source")

    facts = _business_facts_text(specification)
    if facts:
        visible = _visible_text(html)
        visible_lower = visible.lower()

        # Quantitative social-proof claims are particularly easy for design models to
        # hallucinate. Require their numeric value to be present in business context.
        metric_pattern = re.compile(
            r"(?i)\b(\d[\d,.]*\s*[kKmM]?\+?%?)\s+"
            r"(years?|travelers?|customers?|clients?|destinations?|countries?|projects?|"
            r"trips?|bookings?|reviews?|ratings?|awards?|locations?)\b"
        )
        for match in metric_pattern.finditer(visible):
            value = re.sub(r"\s+", "", match.group(1)).lower()
            if value and value not in facts.replace(" ", ""):
                raise StudioWebsiteContractError(
                    f"Unsupported business metric '{match.group(0)}'. Remove it or use a fact supplied in business context."
                )

        testimonial_markup = bool(re.search(r"(?i)<blockquote\b|class=[\"'][^\"']*testimonial", html))
        if testimonial_markup and not any(token in facts for token in ("testimonial", "review", "customer quote")):
            raise StudioWebsiteContractError(
                "Unsupported testimonial/social-proof quote. Remove fictional testimonials unless supplied in business context."
            )

        risky_claims = (
            "award-winning",
            "award winning",
            "certified",
            "licensed",
            "guaranteed",
            "24/7 concierge",
            "private jets",
            "exclusive access",
            "money alone cannot buy",
        )
        for phrase in risky_claims:
            if phrase in visible_lower and phrase not in facts:
                raise StudioWebsiteContractError(
                    f"Unsupported business claim '{phrase}'. Keep copy grounded in supplied business context."
                )

    return {
        "runtimeProfile": "static-web-js",
        "files": sorted(records),
        "groundingChecked": bool(facts),
        "nativeBrowserBehavior": True,
    }


def _safe_fuzzy_edit(workspace: coding.VirtualWorkspace, path: str, old: str, new: str) -> None:
    """Prefer exact replacement, then tolerate whitespace-only source drift once."""
    clean = workspace._path(path)
    current = workspace.raw(clean)
    if not old:
        raise coding.WorkspacePolicyError("edit.old must not be empty")
    count = current.count(old)
    if count == 1:
        workspace.write(clean, current.replace(old, new, 1))
        return
    if count > 1:
        raise coding.WorkspacePolicyError(f"Exact edit requires one match; found {count}")

    stripped = old.strip()
    if len(stripped) < 24 or len(re.findall(r"\S+", stripped)) < 3:
        raise coding.WorkspacePolicyError("Exact edit requires one match; found 0")
    parts = re.split(r"(\s+)", stripped)
    pattern = "".join(r"\s+" if part.isspace() else re.escape(part) for part in parts if part)
    matches = list(re.finditer(pattern, current, flags=re.S))
    if len(matches) != 1:
        raise coding.WorkspacePolicyError(f"Exact edit requires one match; found 0; whitespace-tolerant match found {len(matches)}")
    match = matches[0]
    workspace.write(clean, current[: match.start()] + new + current[match.end() :])


async def _noop_event(_event: dict[str, Any]) -> None:
    return None


class StudioWebsiteToolRegistry(coding.CodingToolRegistry):
    def __init__(self, event_callback=None) -> None:
        super().__init__()
        self.event_callback = event_callback or _noop_event

    async def _finish(self, args: dict[str, Any], session: coding.CodingSession) -> dict[str, Any]:
        files = session.workspace.source_files()
        try:
            report = validate_studio_website(files, _approved_specification(session))
        except StudioWebsiteContractError as error:
            session.last_validation_error = str(error)[:4000]
            return {
                "ok": False,
                "error": "Cannot finish yet: " + str(error),
                "files": session.workspace.list(),
            }
        session.summary = str(args.get("summary") or "Website source updated.").strip()[:4000] or "Website source updated."
        session.verification = [
            str(item).strip()[:500]
            for item in (args.get("verification") or [])
            if str(item).strip()
        ][:30]
        session.finished = True
        return {
            "ok": True,
            **report,
            "changedPaths": session.changed_paths(),
        }

    def for_mode(self, mode, *, visual: bool, web: bool):
        selected = super().for_mode(mode, visual=visual, web=web)
        if mode != "plan":
            selected["finish"] = coding.CodingTool(
                "finish",
                "Finish when the requested website change is coherent, safe, grounded in supplied business context, and ready to preview. Native HTML behavior does not need application interaction manifests.",
                {"summary": coding.TEXT, "verification": {"type": "array", "items": coding.TEXT}},
                ("summary",),
                frozenset({"build", "edit", "repair"}),
                self._finish,
            )

        wrapped = {}
        for name, tool in selected.items():
            async def execute(args, session, *, _tool=tool):
                path = str(args.get("path") or args.get("prefix") or args.get("pattern") or "") or None
                try:
                    result = await _tool.execute(args, session)
                except coding.CodingAgentNeedsUserInput:
                    raise
                except Exception as error:
                    await self.event_callback({
                        "phase": "tool_evidence",
                        "tool": _tool.name,
                        "path": path,
                        "ok": False,
                        "summary": f"{_tool.name} could not be applied; current source evidence will guide the next action.",
                        "detail": str(error)[:2000],
                    })
                    raise
                if not bool(result.get("ok", False)):
                    await self.event_callback({
                        "phase": "validation",
                        "tool": _tool.name,
                        "path": path,
                        "ok": False,
                        "summary": f"{_tool.name} was rejected; the evidence was fed back to the same model session.",
                        "detail": str(result.get("error") or result.get("message") or "Tool rejected")[:2000],
                    })
                return result

            wrapped[name] = coding.CodingTool(
                name=tool.name,
                description=(
                    "Replace one current source fragment. Read the file first. Whitespace-only drift is tolerated, but after a failed replacement reread or use write; never repeat identical failed arguments."
                    if name == "edit"
                    else tool.description
                ),
                properties=tool.properties,
                required=tool.required,
                modes=tool.modes,
                execute=execute,
            )
        return wrapped


class StudioWebsiteCodingAgent(coding.CapabilityCodingAgent):
    """A lean tool loop for websites; arbitrary software keeps the general agent."""

    def __init__(self, client=None, max_steps=None, registry=None, progress_callback=None) -> None:
        super().__init__(
            client=client,
            max_steps=max_steps,
            registry=registry or StudioWebsiteToolRegistry(progress_callback),
            progress_callback=progress_callback,
        )
        self.doom_loop_threshold = 3

    def _website_can_finish(self, session: coding.CodingSession, require_change: bool) -> bool:
        if not session.workspace.source_files():
            return False
        if require_change and not session.changed_paths():
            return False
        try:
            validate_studio_website(session.workspace.source_files(), _approved_specification(session))
        except StudioWebsiteContractError:
            return False
        return True

    async def _session(
        self,
        mode,
        specification,
        workspace,
        task,
        *,
        require_change,
        editor_context,
    ):
        spec = str(specification or "").strip()
        if not spec:
            raise coding.CodingHarnessError("Approved specification is empty")
        spec = spec[:80_000]
        session = coding.CodingSession(
            mode=mode,
            workspace=workspace,
            before=workspace.snapshot(),
            editor_context=editor_context,
        )
        system = coding.PLAN_SYSTEM if mode == "plan" else STUDIO_WEBSITE_SYSTEM
        user_packet = {
            "approvedSpecification": spec,
            "task": str(task or "")[:24_000],
            "workspaceFiles": workspace.list(),
            "mode": mode,
            "executionBoundary": "Generated website code is previewed by Operly; work only through project tools.",
        }
        if editor_context:
            user_packet["editorContextAvailable"] = True
        session.messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user_packet, ensure_ascii=False)},
        ]

        web_enabled = bool(os.getenv("OLLAMA_API_KEY", "").strip()) and os.getenv("OPERLY_CODING_WEB_TOOLS", "1").strip() not in {"0", "false", "False"}
        tools = self.registry.for_mode(mode, visual=bool(editor_context), web=web_enabled)
        schemas = [tool.schema() for tool in tools.values()]
        nudges = 0
        started = time.monotonic()
        last_failed_signature = ""
        repeated_failed = 0

        for step in range(1, self.max_steps + 1):
            remaining = self.max_seconds - (time.monotonic() - started)
            if remaining <= 0:
                detail = f" Last validation issue: {session.last_validation_error}" if session.last_validation_error else ""
                raise coding.CodingHarnessError(f"Studio website agent did not converge within {self.max_seconds} seconds.{detail}"[:4000])
            await self._progress({"step": step, "phase": "model", "summary": "Choosing the next focused website action from current source and owner context."})
            try:
                assistant = await asyncio.wait_for(
                    self.client.chat(session.messages, schemas),
                    timeout=min(self.model_slice_seconds, max(1, remaining)),
                )
            except asyncio.TimeoutError as error:
                detail = f" Last validation issue: {session.last_validation_error}" if session.last_validation_error else ""
                raise coding.CodingHarnessError(f"Coding model did not respond within the bounded website-edit window.{detail}"[:4000]) from error
            session.messages.append(assistant)
            content = str(assistant.get("content") or "").strip()
            if content:
                session.notes.append(content)
            calls = assistant.get("tool_calls") or []

            if not calls:
                if session.finished:
                    break
                if mode != "plan" and self._website_can_finish(session, require_change):
                    session.summary = content or "Website source updated."
                    session.finished = True
                    break
                if nudges >= 1:
                    raise coding.CodingHarnessError("Studio website agent stopped before completing the requested source change")
                nudges += 1
                finish_name = "finish_plan" if mode == "plan" else "finish"
                session.messages.append({"role": "user", "content": f"Continue with the smallest relevant project-tool action, then call {finish_name}."})
                continue

            for call in calls:
                name, args = coding._arguments(call)
                signature = coding._tool_signature(name, args)
                tool = tools.get(name)
                path = str(args.get("path") or args.get("prefix") or args.get("pattern") or "") or None

                # Do not spend a third model action on exactly the same failed edit.
                if name == "edit" and signature == last_failed_signature and repeated_failed >= 2:
                    excerpt = ""
                    try:
                        excerpt = session.workspace.raw(path or "")[:6000]
                    except Exception:
                        pass
                    result = {
                        "ok": False,
                        "error": "This exact edit already failed twice. Reread current source and use corrected edit arguments or write the bounded file instead.",
                        "currentSourceExcerpt": excerpt,
                    }
                elif tool is None:
                    result = {"ok": False, "error": f"Tool {name or 'unknown'} is not permitted in {mode} mode"}
                else:
                    try:
                        result = await tool.execute(args, session)
                    except coding.CodingAgentNeedsUserInput:
                        raise
                    except (coding.WorkspacePolicyError, coding.CodingWebToolError, ValueError, TypeError) as error:
                        result = {"ok": False, "error": str(error)[:2000]}
                        if name == "edit" and path:
                            try:
                                result["currentSourceExcerpt"] = session.workspace.raw(path)[:6000]
                            except Exception:
                                pass

                ok = bool(result.get("ok", False))
                if name == "edit" and not ok:
                    if signature == last_failed_signature:
                        repeated_failed += 1
                    else:
                        last_failed_signature = signature
                        repeated_failed = 1
                elif ok:
                    last_failed_signature = ""
                    repeated_failed = 0

                session.call_signatures.append(signature)
                session.trace.append(
                    coding.AgentTrace(
                        step=step,
                        tool=name or "unknown",
                        path=path,
                        ok=ok,
                        detail=str(result.get("error") or result.get("message") or "")[:500],
                        input_digest=signature[:16],
                    )
                )
                await self._progress({
                    "step": step,
                    "phase": "tool",
                    "tool": name or "unknown",
                    "path": path,
                    "ok": ok,
                    "summary": coding._progress_summary(name or "unknown", path, ok),
                    "detail": str(result.get("error") or "")[:1200] if not ok else "",
                })
                session.messages.append(coding._tool_result_message(name, result))
                if session.finished:
                    break
            if session.finished:
                break

        if not session.finished:
            detail = f" Last validation issue: {session.last_validation_error}" if session.last_validation_error else ""
            raise coding.CodingHarnessError(f"Studio website agent exhausted its bounded model-turn budget.{detail}"[:4000])
        if mode != "plan":
            if not workspace.source_files():
                raise coding.CodingHarnessError("Website source tree is empty")
            if require_change and not session.changed_paths():
                raise coding.CodingHarnessError("The requested Studio edit did not change any source files")
            validate_studio_website(workspace.source_files(), _approved_specification(session))
        return session


def _studio_ensure_static(result) -> str:
    validate_studio_website(result.files)
    return "static-web-js"


def _studio_budget(operation: str) -> tuple[int, int, int]:
    if operation == "generate":
        return 24, 120, 60
    return 12, 75, 45


_APPLIED = False
_ORIGINAL_EDIT = None


def apply_studio_runtime_policy() -> None:
    """Install the Studio-only policy after shared modules have finished importing."""
    global _APPLIED, _ORIGINAL_EDIT
    if _APPLIED:
        return

    from packages.studio import agent_runs, source_agent

    if _ORIGINAL_EDIT is None:
        _ORIGINAL_EDIT = coding.VirtualWorkspace.edit

    def resilient_edit(self, path: str, old: str, new: str) -> None:
        return _safe_fuzzy_edit(self, path, old, new)

    # Whitespace-tolerant exact edits are a general reliability improvement; Studio
    # receives the domain-specific agent/finish contract below.
    coding.VirtualWorkspace.edit = resilient_edit

    agent_runs.OpenCodeStyleCodingAgent = StudioWebsiteCodingAgent
    agent_runs.VisibleToolRegistry = StudioWebsiteToolRegistry
    agent_runs._studio_budget = _studio_budget

    # Direct source-agent endpoints use the same Studio policy as durable runs.
    source_agent.OpenCodeStyleCodingAgent = StudioWebsiteCodingAgent
    source_agent._ensure_static = _studio_ensure_static

    _APPLIED = True
