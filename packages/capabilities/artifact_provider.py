from __future__ import annotations

from pathlib import Path
from typing import Any

from packages.artifacts.service import ArtifactService, artifact_json, artifact_scope_from_context
from packages.capabilities.contracts import ApprovalPolicy, CapabilityDefinition, CapabilityResult
from packages.capabilities.providers import BaseProvider


# Source artifacts are inert downloads, not web responses. Potentially active browser
# formats such as HTML/JavaScript intentionally default to text/plain so persisting
# model-authored source can never turn artifact delivery into code execution.
_SOURCE_CONTENT_TYPES = {
    ".py": "text/x-python; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".markdown": "text/markdown; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".jsonl": "application/json",
    ".xml": "application/xml",
    ".csv": "text/csv; charset=utf-8",
    ".yaml": "text/plain; charset=utf-8",
    ".yml": "text/plain; charset=utf-8",
    ".toml": "text/plain; charset=utf-8",
    ".ini": "text/plain; charset=utf-8",
    ".cfg": "text/plain; charset=utf-8",
    ".sql": "text/plain; charset=utf-8",
    ".html": "text/plain; charset=utf-8",
    ".htm": "text/plain; charset=utf-8",
    ".js": "text/plain; charset=utf-8",
    ".mjs": "text/plain; charset=utf-8",
    ".cjs": "text/plain; charset=utf-8",
    ".ts": "text/plain; charset=utf-8",
    ".tsx": "text/plain; charset=utf-8",
    ".jsx": "text/plain; charset=utf-8",
    ".sh": "text/plain; charset=utf-8",
    ".bash": "text/plain; charset=utf-8",
    ".zsh": "text/plain; charset=utf-8",
    ".ps1": "text/plain; charset=utf-8",
}
_SAFE_TEXT_CONTENT_TYPES = frozenset(
    {
        "text/plain",
        "text/markdown",
        "text/css",
        "text/csv",
        "text/x-python",
        "application/json",
        "application/xml",
    }
)


def _artifact_text_content_type(filename: str, requested: Any) -> str:
    """Return an inert UTF-8 content type for model-authored text/source artifacts."""
    requested_type = str(requested or "").strip()[:200]
    if requested_type:
        base = requested_type.split(";", 1)[0].strip().lower()
        if base in _SAFE_TEXT_CONTENT_TYPES:
            if base.startswith("text/") and "charset=" not in requested_type.lower():
                return f"{base}; charset=utf-8"
            return requested_type
    suffix = Path(str(filename or "")).suffix.lower()
    return _SOURCE_CONTENT_TYPES.get(suffix, "text/plain; charset=utf-8")


class ArtifactProvider(BaseProvider):
    """Small durable artifact vocabulary shared by every Operly surface."""

    name = "operly_artifacts"
    capabilities = (
        CapabilityDefinition(
            "artifact.list",
            "artifact_list",
            "List durable files/artifacts available in the current Personal or Workspace execution scope.",
            {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                    "run_id": {"type": "string", "maxLength": 120},
                    "content_type_prefix": {"type": "string", "maxLength": 100},
                },
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="read_only",
            permissions=("files:process",),
            approval_policy=ApprovalPolicy.AUTO,
            category="files",
            display_name="List artifacts",
            tags=frozenset({"files", "artifacts", "workspace files", "agent outputs"}),
            semantic_operations=frozenset({"list files", "find generated files", "inspect artifacts"}),
        ),
        CapabilityDefinition(
            "artifact.inspect",
            "artifact_inspect",
            "Inspect metadata for one durable artifact without loading its bytes into the model context.",
            {
                "type": "object",
                "properties": {"artifact_id": {"type": "string", "minLength": 1, "maxLength": 36}},
                "required": ["artifact_id"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="read_only",
            permissions=("files:process",),
            approval_policy=ApprovalPolicy.AUTO,
            category="files",
            display_name="Inspect artifact",
            tags=frozenset({"files", "artifacts", "metadata"}),
            semantic_operations=frozenset({"inspect file metadata", "check artifact"}),
        ),
        CapabilityDefinition(
            "artifact.read_text",
            "artifact_read_text",
            "Read a bounded UTF-8 text artifact. Binary files should be handled with files.process instead.",
            {
                "type": "object",
                "properties": {
                    "artifact_id": {"type": "string", "minLength": 1, "maxLength": 36},
                    "max_chars": {"type": "integer", "minimum": 1, "maximum": 100000},
                },
                "required": ["artifact_id"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="read_only",
            permissions=("files:process",),
            approval_policy=ApprovalPolicy.AUTO,
            category="files",
            display_name="Read text artifact",
            tags=frozenset({"files", "artifacts", "text"}),
            semantic_operations=frozenset({"read file", "read text artifact"}),
        ),
        CapabilityDefinition(
            "artifact.create_text",
            "artifact_create_text",
            (
                "Save model/application-authored UTF-8 text or source code as a durable scoped artifact using the exact requested filename, "
                "including source extensions such as .py, .html, .js, .ts, .json, .sql and shell-script files. "
                "This capability only persists inert file bytes; it never executes the authored code."
            ),
            {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "minLength": 1, "maxLength": 255},
                    "content": {"type": "string", "maxLength": 250000},
                    "content_type": {"type": "string", "maxLength": 200},
                },
                "required": ["filename", "content"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="low",
            permissions=("files:process",),
            approval_policy=ApprovalPolicy.AUTO,
            reversible=True,
            category="files",
            display_name="Save text or source file",
            tags=frozenset({"files", "artifacts", "save", "outputs", "source", "code", "python", "html"}),
            semantic_operations=frozenset(
                {
                    "save file",
                    "create text file",
                    "persist result",
                    "create source file",
                    "save source code",
                    "create code file",
                    "write python file",
                    "create py file",
                    "write html file",
                    "create html file",
                    "download source file",
                }
            ),
        ),
    )

    async def execute(self, context, capability_name: str, arguments: dict[str, Any]) -> CapabilityResult:
        service = ArtifactService(context.db)
        scope = artifact_scope_from_context(context)
        if capability_name == "artifact.list":
            rows = await service.list(
                scope,
                limit=int(arguments.get("limit", 50)),
                run_id=arguments.get("run_id"),
                content_type_prefix=arguments.get("content_type_prefix"),
            )
            return CapabilityResult(True, False, {"artifacts": [artifact_json(row) for row in rows], "count": len(rows)})
        if capability_name == "artifact.inspect":
            row = await service.get(scope, arguments["artifact_id"])
            return CapabilityResult(True, False, artifact_json(row))
        if capability_name == "artifact.read_text":
            row = await service.get(scope, arguments["artifact_id"])
            raw = await service.read_bytes(scope, row.id)
            if not (row.content_type.startswith("text/") or row.content_type in {"application/json", "application/xml", "application/javascript"}):
                return CapabilityResult(False, False, {"reason": "binary_artifact_requires_files_process", "artifact_id": row.id})
            limit = max(1, min(int(arguments.get("max_chars", 50000)), 100000))
            decoded = raw.decode("utf-8", errors="replace")
            text = decoded[:limit]
            return CapabilityResult(
                True,
                False,
                {
                    "artifact_id": row.id,
                    "filename": row.filename,
                    "content_type": row.content_type,
                    "text": text,
                    "truncated": len(text) < len(decoded),
                },
            )
        if capability_name == "artifact.create_text":
            raw = str(arguments.get("content") or "").encode("utf-8")
            filename = str(arguments.get("filename") or "artifact.txt")
            row = await service.create_bytes(
                scope,
                filename=filename,
                content_type=_artifact_text_content_type(filename, arguments.get("content_type")),
                content=raw,
                source="agent",
                created_by=context.actor_id,
                run_id=(context.invocation or {}).get("metadata", {}).get("runtime_run_id") if isinstance(context.invocation, dict) else None,
                metadata={"artifact_kind": "text_source", "inert": True, "executed": False},
            )
            artifact = artifact_json(row)
            return CapabilityResult(
                True,
                True,
                {
                    "artifact_id": row.id,
                    "artifact_ids": [row.id],
                    "artifacts": [artifact],
                    "artifact_kind": "text_source",
                    "filename": row.filename,
                    "content_type": row.content_type,
                    "persisted": True,
                    "inert": True,
                    "executed": False,
                },
                row.id,
            )
        return CapabilityResult(False, False, {"reason": "unsupported_artifact_capability"})

    async def verify(self, context, capability_name: str, arguments: dict[str, Any], result: CapabilityResult) -> CapabilityResult:
        del arguments
        if not result.success:
            return CapabilityResult(False, result.changed, result.evidence)
        if capability_name == "artifact.create_text":
            artifact_id = result.evidence.get("artifact_id")
            if not artifact_id:
                return CapabilityResult(False, result.changed, {"reason": "artifact_id_missing"})
            try:
                row = await ArtifactService(context.db).get(artifact_scope_from_context(context), artifact_id)
            except LookupError:
                return CapabilityResult(False, result.changed, {"reason": "artifact_not_persisted"})
            artifact = artifact_json(row)
            return CapabilityResult(
                True,
                True,
                {
                    "artifact_id": row.id,
                    "artifact_ids": [row.id],
                    "artifacts": [artifact],
                    "artifact_kind": "text_source",
                    "filename": row.filename,
                    "content_type": row.content_type,
                    "persisted": True,
                    "inert": True,
                    "executed": False,
                },
                row.id,
            )
        return CapabilityResult(True, False, {"observed": True, **result.evidence})
