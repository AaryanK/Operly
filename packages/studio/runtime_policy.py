"""Studio-specific website runtime and agent policy.

The generic coding harness remains strict for arbitrary applications. Studio websites
get a lighter contract: browser-native HTML stays native, business claims must remain
grounded, and focused edits get resilient source tools rather than app-only ceremony.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from typing import Any

from packages.coding_harness import opencode_agent as coding


STUDIO_WEBSITE_SYSTEM = """
You are OPERLY's website source agent inside one persistent Studio project.
The owner is editing a public business website, not a generic JavaScript application.
Use project tools to inspect and modify real source files. Do not return a giant code
dump when tools can change the workspace.

WEBSITE RULES
- The owner instruction, current source, business facts, selected element, recent Studio conversation, and authorized capability summaries are your context.
- Business facts are authoritative data. Never invent testimonials, customer counts, years in business, destinations served, awards, ratings, prices, credentials, guarantees, partnerships, locations, or other concrete claims.
- If a factual marketing claim is unsupported, omit it or use non-quantitative copy. Missing facts are not permission to make plausible facts up.
- Ordinary anchors, semantic navigation, native GET/POST forms, fields inside those forms, and intrinsic browser controls do NOT need data-operly-interaction ids, JavaScript handlers, requirement IDs, state probes, domain operations, or operly.interactions.json.
- Only add JavaScript when the requested experience genuinely needs scripted behavior. Core navigation, reading, responsive layout, contact details, and server-bound contact forms must remain useful without JavaScript.
- A contact form may POST to __OPERLY_FORM_ACTION__ with name, email, message, and a hidden website honeypot. Do not call private Operly APIs from generated JavaScript.
- Do not create or repair operly.interactions.json for content, localization, layout, color, typography, navigation, or native-form edits.
- For visual/copy/localization work, inspect index.html and styles.css first. Do not read app.js, tests, or interaction artifacts unless the request actually touches scripted behavior.
- Preserve unrelated working source. A focused request should normally take a few reads/edits, not a site-wide rewrite.
- Before edit, read the current file. If exact edit fails, reread once and use corrected arguments, replace_range, or write the bounded file. Never repeat identical failed edit arguments.
- replace_range is the preferred fallback after a read when line numbers are stable but exact source text is not.
- Keep the site responsive and accessible: readable contrast, visible focus, semantic headings, and no horizontal overflow.
- Never write secrets, credentials, .env files, analytics beacons, trackers, or authentication code.
- Web search is optional evidence only when current public documentation is genuinely required; never use it to invent business facts.
- Finish as soon as the requested website change is coherent and the Studio website contract passes. Do not add application scaffolding merely to satisfy a generic app pattern.
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
    """Validate Studio website structure/safety/grounding without app-only rules."""
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
        compact_facts = re.sub(r"\s+", "", facts)

        # Catch claims such as "15k+ Happy Travelers" and "120+ Global
        # Destinations", while allowing a few descriptive words between value/noun.
        metric_pattern = re.compile(
            r"(?i)\b(\d[\d,.]*\s*[kKmM]?\+?%?)\s+"
            r"(?:[A-Za-z][A-Za-z&'-]*\s+){0,3}"
            r"(years?|travelers?|customers?|clients?|destinations?|countries?|projects?|"
            r"trips?|bookings?|reviews?|ratings?|awards?|locations?)\b"
        )
        for match in metric_pattern.finditer(visible):
            value = re.sub(r"\s+", "", match.group(1)).lower()
            if value and value not in compact_facts:
                raise StudioWebsiteContractError(
                    f"Unsupported business metric '{match.group(0)}'. Remove it or use a fact supplied in business context."
                )

        testimonial_markup = bool(
            re.search(r"(?i)<blockquote\b|class=[\"'][^\"']*(testimonial|review-quote)[^\"']*[\"']", html)
        )
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
    """Exact first; then tolerate only whitespace drift, including between tags."""
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

    # re.escape leaves angle brackets literal on modern Python. Permit whitespace
    # where old already had it and between adjacent tags where formatters commonly
    # insert indentation/newlines.
    parts = re.split(r"(\s+)", stripped)
    pattern = "".join(r"\s+" if part.isspace() else re.escape(part) for part in parts if part)
    pattern = pattern.replace("><", r">\s*<")
    matches = list(re.finditer(pattern, current, flags=re.S))
    if len(matches) != 1:
        raise coding.WorkspacePolicyError(
            f"Exact edit requires one match; found 0; whitespace-tolerant match found {len(matches)}"
        )
    match = matches[0]
    workspace.write(clean, current[: match.start()] + new + current[match.end() :])


def _replace_range(workspace: coding.VirtualWorkspace, path: str, start_line: int, end_line: int, content: str) -> None:
    clean = workspace._path(path)
    current = workspace.raw(clean)
    lines = current.splitlines(keepends=True)
    start = int(start_line)
    end = int(end_line)
    if start < 1 or end < start or end > len(lines):
        raise coding.WorkspacePolicyError(
            f"replace_range lines must satisfy 1 <= start <= end <= {len(lines)}"
        )
    if end - start + 1 > 500:
        raise coding.WorkspacePolicyError("replace_range is limited to 500 source lines")
    replacement = str(content)
    if replacement and not replacement.endswith("\n") and end < len(lines):
        replacement += "\n"
    updated = "".join(lines[: start - 1]) + replacement + "".join(lines[end:])
    workspace.write(clean, updated)


async def _noop_event(_event: dict[str, Any]) -> None:
    return None


class StudioWebsiteToolRegistry(coding.CodingToolRegistry):
    def __init__(self, event_callback=None, *, preloaded_paths: list[str] | None = None) -> None:
        super().__init__()
        self.event_callback = event_callback or _noop_event
        self.preloaded_paths = set(preloaded_paths or [])

    async def _finish(self, args: dict[str, Any], session: coding.CodingSession) -> dict[str, Any]:
        files = session.workspace.source_files()
        try:
            report = validate_studio_website(files, _approved_specification(session))
        except StudioWebsiteContractError as error:
            session.last_validation_error = str(error)[:4000]
            return {"ok": False, "error": "Cannot finish yet: " + str(error), "files": session.workspace.list()}
        session.summary = str(args.get("summary") or "Website source updated.").strip()[:4000] or "Website source updated."
        session.verification = [str(item).strip()[:500] for item in (args.get("verification") or []) if str(item).strip()][:30]
        session.finished = True
        return {"ok": True, **report, "changedPaths": session.changed_paths()}

    async def _range(self, args: dict[str, Any], session: coding.CodingSession) -> dict[str, Any]:
        path = str(args.get("path") or "")
        _replace_range(
            session.workspace,
            path,
            int(args.get("start_line") or 0),
            int(args.get("end_line") or 0),
            str(args.get("content") or ""),
        )
        return {"ok": True, "path": path}

    def for_mode(self, mode, *, visual: bool, web: bool):
        selected = super().for_mode(mode, visual=visual, web=web)
        if mode != "plan":
            selected["finish"] = coding.CodingTool(
                "finish",
                "Finish when the website change is coherent, safe, grounded in supplied business context, and ready to preview. Native HTML behavior does not need application interaction manifests.",
                {"summary": coding.TEXT, "verification": {"type": "array", "items": coding.TEXT}},
                ("summary",),
                frozenset({"build", "edit", "repair"}),
                self._finish,
            )
            selected["replace_range"] = coding.CodingTool(
                "replace_range",
                "Replace an inclusive line range in one file after reading it. Use this when exact edit text has drifted; keep replacements bounded and preserve unrelated source.",
                {
                    "path": coding.TEXT,
                    "start_line": coding.INTEGER,
                    "end_line": coding.INTEGER,
                    "content": coding.TEXT,
                },
                ("path", "start_line", "end_line", "content"),
                frozenset({"build", "edit", "repair"}),
                self._range,
            )

        wrapped = {}
        for name, tool in selected.items():
            async def execute(args, session, *, _tool=tool):
                path = str(args.get("path") or args.get("prefix") or args.get("pattern") or "") or None
                if _tool.name == "read" and path in self.preloaded_paths:
                    try:
                        current = session.workspace.raw(path)
                    except Exception:
                        current = None
                    if current is not None and current == session.before.get(path):
                        return {
                            "ok": True,
                            "path": path,
                            "preloaded": True,
                            "unchanged": True,
                            "note": (
                                "The complete current contents of this unchanged file are already attached in the "
                                "Studio source working set. Use that source and proceed to the edit instead of rereading it."
                            ),
                        }
                try:
                    if _tool.name == "edit":
                        _safe_fuzzy_edit(
                            session.workspace,
                            str(args.get("path") or ""),
                            str(args.get("old") or ""),
                            str(args.get("new") or ""),
                        )
                        result = {"ok": True, "path": str(args.get("path") or "")}
                    else:
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
                if bool(result.get("ok", False)) and _tool.name in {"write", "edit", "remove", "replace_range"} and path:
                    self.preloaded_paths.discard(path)
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

            description = tool.description
            if name == "edit":
                description = (
                    "Replace one current source fragment after reading it. Whitespace-only drift is tolerated. "
                    "If it still fails, reread and use corrected arguments or replace_range; never repeat identical failed arguments."
                )
            wrapped[name] = coding.CodingTool(
                name=tool.name,
                description=description,
                properties=tool.properties,
                required=tool.required,
                modes=tool.modes,
                execute=execute,
            )
        return wrapped


class StudioWebsiteCodingAgent(coding.CapabilityCodingAgent):
    """Lean website loop; arbitrary/custom software keeps the general agent."""

    def __init__(self, client=None, max_steps=None, registry=None, progress_callback=None) -> None:
        super().__init__(
            client=client,
            max_steps=max_steps,
            registry=registry or StudioWebsiteToolRegistry(progress_callback),
            progress_callback=progress_callback,
        )

    def _website_can_finish(self, session: coding.CodingSession, require_change: bool) -> bool:
        if not session.workspace.source_files() or (require_change and not session.changed_paths()):
            return False
        try:
            validate_studio_website(session.workspace.source_files(), _approved_specification(session))
        except StudioWebsiteContractError:
            return False
        return True

    async def _session(self, mode, specification, workspace, task, *, require_change, editor_context):
        spec = str(specification or "").strip()
        if not spec:
            raise coding.CodingHarnessError("Approved specification is empty")
        spec = spec[:80_000]
        session = coding.CodingSession(mode=mode, workspace=workspace, before=workspace.snapshot(), editor_context=editor_context)
        system = coding.PLAN_SYSTEM if mode == "plan" else STUDIO_WEBSITE_SYSTEM
        files = workspace.list()
        task_text = str(task or "")[:24_000]
        packet = {
            "approvedSpecification": spec,
            "task": task_text,
            "workspaceFiles": files,
            "mode": mode,
            "executionBoundary": "Generated website code is previewed by Operly; work only through project tools.",
        }
        if editor_context:
            packet["editorContextAvailable"] = True
        session.messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(packet, ensure_ascii=False)},
        ]

        web_enabled = bool(os.getenv("OLLAMA_API_KEY", "").strip()) and os.getenv("OPERLY_CODING_WEB_TOOLS", "1").strip() not in {"0", "false", "False"}
        tools = self.registry.for_mode(mode, visual=bool(editor_context), web=web_enabled)
        schemas = [tool.schema() for tool in tools.values()]
        initial_message_chars = len(json.dumps(session.messages, ensure_ascii=False, default=str))
        await self._progress(
            {
                "step": 0,
                "phase": "model_input",
                "summary": (
                    f"Model input prepared: {len(spec)} specification chars · {len(files)} workspace file"
                    f"{'s' if len(files) != 1 else ''} · {len(schemas)} tool{'s' if len(schemas) != 1 else ''}."
                ),
                "detail": {
                    "mode": mode,
                    "systemPrompt": "PLAN_SYSTEM" if mode == "plan" else "STUDIO_WEBSITE_SYSTEM",
                    "systemChars": len(system),
                    "specificationChars": len(spec),
                    "specificationDigest": hashlib.sha256(spec.encode("utf-8")).hexdigest(),
                    "taskChars": len(task_text),
                    "workspaceFileCount": len(files),
                    "workspaceFiles": files[:50],
                    "editorContextAvailable": bool(editor_context),
                    "toolNames": list(tools),
                    "initialMessageChars": initial_message_chars,
                },
            }
        )
        nudges = 0
        inspection_only_turns = 0
        started = time.monotonic()
        failed_signatures: dict[str, int] = {}

        for step in range(1, self.max_steps + 1):
            remaining = self.max_seconds - (time.monotonic() - started)
            if remaining <= 0:
                detail = f" Last validation issue: {session.last_validation_error}" if session.last_validation_error else ""
                raise coding.CodingHarnessError(f"Studio website agent did not converge within {self.max_seconds} seconds.{detail}"[:4000])
            await self._progress(
                {
                    "step": step,
                    "phase": "model",
                    "summary": "Choosing the next focused website action from current source and owner context.",
                    "detail": {
                        "messageCount": len(session.messages),
                        "messageChars": len(json.dumps(session.messages, ensure_ascii=False, default=str)),
                    },
                }
            )
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

            tool_names_this_turn: list[str] = []
            for call in calls:
                name, args = coding._arguments(call)
                tool_names_this_turn.append(name)
                signature = coding._tool_signature(name, args)
                tool = tools.get(name)
                path = str(args.get("path") or args.get("prefix") or args.get("pattern") or "") or None

                if failed_signatures.get(signature, 0) >= 2:
                    excerpt = ""
                    try:
                        excerpt = session.workspace.raw(path or "")[:6000]
                    except Exception:
                        pass
                    result = {
                        "ok": False,
                        "error": "These exact tool arguments already failed twice. Do not repeat them. Reread current source and use corrected arguments, replace_range, or write the bounded file.",
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
                        if path:
                            try:
                                result["currentSourceExcerpt"] = session.workspace.raw(path)[:6000]
                            except Exception:
                                pass

                ok = bool(result.get("ok", False))
                if ok:
                    failed_signatures.pop(signature, None)
                else:
                    failed_signatures[signature] = failed_signatures.get(signature, 0) + 1

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

            inspection_only = bool(tool_names_this_turn) and all(
                name in coding.SOURCE_INSPECTION_TOOLS for name in tool_names_this_turn
            )
            if require_change and mode in {"edit", "repair"} and not session.changed_paths() and inspection_only:
                inspection_only_turns += 1
            else:
                inspection_only_turns = 0
            if inspection_only_turns >= 2:
                session.messages.append(
                    {
                        "role": "user",
                        "content": (
                            "You have spent two model turns inspecting without changing the website source. "
                            "Use the current specification, preloaded source, and tool results already available and "
                            "make the requested source change now. Do not reread unchanged source. If a material blocker "
                            "truly prevents implementation, use the question tool."
                        ),
                    }
                )
                await self._progress(
                    {
                        "step": step,
                        "phase": "guardrail",
                        "summary": "No website source progress across two inspection turns; asking the coding model to act on existing context.",
                        "detail": {"inspectionTurns": 2, "tools": tool_names_this_turn},
                    }
                )
                inspection_only_turns = 0

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
    """Keep Studio website policy aligned with the context-rich shared harness budget."""
    if operation == "generate":
        return 20, 180, 90
    return 10, 120, 75


_APPLIED = False


def apply_studio_runtime_policy() -> None:
    """Install Studio-only policy after shared modules finish importing."""
    global _APPLIED
    if _APPLIED:
        return

    from packages.studio import agent_runs, source_agent

    original_project_context = source_agent.project_context

    async def website_project_context(*args, **kwargs):
        text = await original_project_context(*args, **kwargs)
        text = text.replace(
            "- Use the canonical source shape required by the Operly coding harness: index.html, separate application JavaScript, executable node:test coverage, and operly.interactions.json.",
            "- Use index.html and styles.css as the normal website source. Add separate JavaScript only when the requested experience genuinely needs scripted behavior.",
        )
        return text

    agent_runs.OpenCodeStyleCodingAgent = StudioWebsiteCodingAgent
    agent_runs.VisibleToolRegistry = StudioWebsiteToolRegistry
    agent_runs._studio_budget = _studio_budget
    agent_runs.project_context = website_project_context

    source_agent.OpenCodeStyleCodingAgent = StudioWebsiteCodingAgent
    source_agent._ensure_static = _studio_ensure_static
    source_agent.project_context = website_project_context

    _APPLIED = True
