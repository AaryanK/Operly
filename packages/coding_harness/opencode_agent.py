"""OpenCode-style source authoring loop for OPERLY.

The model receives project-scoped read/edit tools and writes into an in-memory
workspace. OPERLY itself never executes generated code here; execution remains
an isolated-runner responsibility.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

from packages.custom_software.source_bundles import MAX_BYTES, MAX_FILES, SourceFile, normalized_path
from packages.model_runtime import OllamaClient


class CodingHarnessError(RuntimeError):
    pass


class WorkspacePolicyError(CodingHarnessError):
    pass


@dataclass
class AgentTrace:
    step: int
    tool: str
    path: str | None = None
    ok: bool = True
    detail: str = ""


@dataclass
class CodingHarnessResult:
    files: list[SourceFile]
    plan: str
    summary: str
    verification: list[str] = field(default_factory=list)
    trace: list[AgentTrace] = field(default_factory=list)


class VirtualWorkspace:
    """Project-scoped editable source tree with the same path/size policy as bundles."""

    def __init__(self) -> None:
        self._files: dict[str, str] = {}

    def _path(self, value: str) -> str:
        try:
            return normalized_path(str(value or ""))
        except Exception as error:
            raise WorkspacePolicyError(str(error)) from error

    def _check_budget(self, candidate: dict[str, str]) -> None:
        if len(candidate) > MAX_FILES:
            raise WorkspacePolicyError("Workspace file-count limit exceeded")
        size = sum(len(value.encode("utf-8")) for value in candidate.values())
        if size > MAX_BYTES:
            raise WorkspacePolicyError("Workspace size limit exceeded")

    def list(self, prefix: str = "") -> list[str]:
        if not prefix:
            return sorted(self._files)
        clean = self._path(prefix)
        return sorted(path for path in self._files if path == clean or path.startswith(clean.rstrip("/") + "/"))

    def read(self, path: str) -> str:
        clean = self._path(path)
        if clean not in self._files:
            raise WorkspacePolicyError(f"File not found: {clean}")
        return self._files[clean]

    def write(self, path: str, content: str) -> None:
        clean = self._path(path)
        text = str(content)
        if "BEGIN PRIVATE KEY" in text or "OPERLY_SANDBOX_RUNNER_TOKEN" in text:
            raise WorkspacePolicyError("Secrets are forbidden in generated source")
        candidate = dict(self._files)
        candidate[clean] = text
        self._check_budget(candidate)
        self._files = candidate

    def edit(self, path: str, old: str, new: str) -> None:
        clean = self._path(path)
        current = self.read(clean)
        if not old:
            raise WorkspacePolicyError("edit.old must not be empty")
        count = current.count(old)
        if count != 1:
            raise WorkspacePolicyError(f"Exact edit requires one match; found {count}")
        self.write(clean, current.replace(old, new, 1))

    def remove(self, path: str) -> None:
        clean = self._path(path)
        if clean not in self._files:
            raise WorkspacePolicyError(f"File not found: {clean}")
        del self._files[clean]

    def grep(self, query: str, prefix: str = "") -> list[dict[str, Any]]:
        needle = str(query or "")
        if not needle:
            raise WorkspacePolicyError("grep query is required")
        paths = self.list(prefix)
        rows: list[dict[str, Any]] = []
        for path in paths:
            for number, line in enumerate(self._files[path].splitlines(), 1):
                if needle.lower() in line.lower():
                    rows.append({"path": path, "line": number, "text": line[:500]})
                    if len(rows) >= 100:
                        return rows
        return rows

    def source_files(self) -> list[SourceFile]:
        return [SourceFile(path, content.encode("utf-8"), "opencode_style_agent") for path, content in sorted(self._files.items())]


PLAN_SYSTEM = """
You are the read-only PLAN agent in OPERLY's coding harness.
Produce a concise implementation plan for the approved software specification.
Do not invent product requirements. Identify the minimum coherent source tree,
interfaces, persistence needs, and executable tests. Generated code will later be
written by a separate BUILD agent and executed only in an isolated runner.
""".strip()

BUILD_SYSTEM = """
You are the BUILD agent in OPERLY's coding harness. Work like a disciplined coding
agent: inspect the project workspace with list/read/grep, create files with write,
make narrow changes with edit, remove only files you intentionally replace, and call
finish when the source tree is complete.

Rules:
- Implement the approved specification, not a generic template.
- Do not add features that are not required by the specification.
- Include executable tests for the critical behavior.
- Never write secrets, credentials, private keys, tokens, or .env files.
- Do not claim code was executed. This agent has no terminal access in the OPERLY
  control plane. Build/test execution is delegated to the isolated runner.
- Keep paths relative to the project root and do not use hidden paths.
- Prefer simple, maintainable technology consistent with the approved plan.
- Use tools to create the actual files; do not merely paste a proposed codebase in
  the assistant response.
""".strip()


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required or [],
                "additionalProperties": False,
            },
        },
    }


def build_tools() -> list[dict[str, Any]]:
    text = {"type": "string"}
    return [
        _tool("list", "List files in the project workspace", {"prefix": text}),
        _tool("read", "Read one project file", {"path": text}, ["path"]),
        _tool("grep", "Search project files for text", {"query": text, "prefix": text}, ["query"]),
        _tool("write", "Create or overwrite one project file", {"path": text, "content": text}, ["path", "content"]),
        _tool("edit", "Replace exactly one matching string in an existing file", {"path": text, "old": text, "new": text}, ["path", "old", "new"]),
        _tool("remove", "Remove one project file", {"path": text}, ["path"]),
        _tool(
            "finish",
            "Finish after the complete source tree and tests have been written",
            {
                "summary": text,
                "verification": {"type": "array", "items": {"type": "string"}},
            },
            ["summary"],
        ),
    ]


def _arguments(call: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    function = call.get("function") or {}
    name = str(function.get("name") or "")
    raw = function.get("arguments") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = {}
    return name, raw if isinstance(raw, dict) else {}


def _has_test(files: list[SourceFile]) -> bool:
    for item in files:
        path = item.path.lower()
        name = path.rsplit("/", 1)[-1]
        if name.startswith("test_") or name.endswith((".test.js", ".test.ts", ".spec.js", ".spec.ts")) or "/tests/" in f"/{path}/":
            return True
    return False


class OpenCodeStyleCodingAgent:
    """Provider-agnostic agent controller over OPERLY's chat-client interface."""

    def __init__(self, client=None, max_steps: int | None = None) -> None:
        self.client = client or OllamaClient()
        configured = int(os.getenv("OPERLY_CODING_AGENT_MAX_STEPS", "48"))
        self.max_steps = max(4, min(max_steps or configured, 96))

    async def build(self, specification: str) -> CodingHarnessResult:
        spec = " ".join(str(specification or "").split())
        if not spec:
            raise CodingHarnessError("Approved specification is empty")
        spec = spec[:80_000]

        plan_reply = await self.client.chat(
            [
                {"role": "system", "content": PLAN_SYSTEM},
                {"role": "user", "content": spec},
            ]
        )
        plan = str(plan_reply.get("content") or "Implement the approved specification with the smallest coherent source tree and executable tests.").strip()
        if not plan:
            plan = "Implement the approved specification with the smallest coherent source tree and executable tests."

        workspace = VirtualWorkspace()
        trace: list[AgentTrace] = []
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": BUILD_SYSTEM},
            {"role": "system", "content": "READ-ONLY PLAN AGENT OUTPUT:\n" + plan[:20_000]},
            {"role": "user", "content": "APPROVED SOFTWARE SPECIFICATION:\n" + spec},
        ]
        tools = build_tools()
        summary = ""
        verification: list[str] = []
        finished = False
        nudged = False

        for step in range(1, self.max_steps + 1):
            assistant = await self.client.chat(messages, tools)
            messages.append(assistant)
            calls = assistant.get("tool_calls") or []
            if not calls:
                if workspace.list():
                    summary = str(assistant.get("content") or "Source tree authored.").strip() or "Source tree authored."
                    finished = True
                    break
                if nudged:
                    raise CodingHarnessError("BUILD agent stopped without creating source files")
                nudged = True
                messages.append({"role": "user", "content": "No files exist yet. Use the write tool to create the implementation and its tests."})
                continue

            for call in calls:
                name, args = _arguments(call)
                path = str(args.get("path") or args.get("prefix") or "") or None
                try:
                    if name == "list":
                        result: Any = {"ok": True, "files": workspace.list(str(args.get("prefix") or ""))}
                    elif name == "read":
                        result = {"ok": True, "path": args.get("path"), "content": workspace.read(str(args.get("path") or ""))}
                    elif name == "grep":
                        result = {"ok": True, "matches": workspace.grep(str(args.get("query") or ""), str(args.get("prefix") or ""))}
                    elif name == "write":
                        workspace.write(str(args.get("path") or ""), str(args.get("content") or ""))
                        result = {"ok": True, "path": args.get("path")}
                    elif name == "edit":
                        workspace.edit(str(args.get("path") or ""), str(args.get("old") or ""), str(args.get("new") or ""))
                        result = {"ok": True, "path": args.get("path")}
                    elif name == "remove":
                        workspace.remove(str(args.get("path") or ""))
                        result = {"ok": True, "path": args.get("path")}
                    elif name == "finish":
                        summary = str(args.get("summary") or "Source tree authored.").strip()
                        verification = [str(item)[:500] for item in (args.get("verification") or []) if str(item).strip()][:30]
                        result = {"ok": True, "files": workspace.list(), "message": "Source authoring complete; execution has not run yet."}
                        finished = True
                    else:
                        result = {"ok": False, "error": f"Unknown coding tool: {name}"}

                    trace.append(AgentTrace(step=step, tool=name or "unknown", path=path, ok=bool(result.get("ok", False)), detail=str(result.get("error") or result.get("message") or "")[:300]))
                except WorkspacePolicyError as error:
                    result = {"ok": False, "error": str(error)}
                    trace.append(AgentTrace(step=step, tool=name or "unknown", path=path, ok=False, detail=str(error)[:300]))

                messages.append({"role": "tool", "tool_name": name, "content": json.dumps(result, ensure_ascii=False, default=str)[:60_000]})
                if finished:
                    break
            if finished:
                break

        if not finished:
            raise CodingHarnessError("BUILD agent exhausted its safe tool-step budget")

        files = workspace.source_files()
        if not files:
            raise CodingHarnessError("BUILD agent finished without source files")
        if not _has_test(files):
            raise CodingHarnessError("BUILD agent did not create an executable test file")

        return CodingHarnessResult(files=files, plan=plan, summary=summary or "Source tree authored.", verification=verification, trace=trace)
