"""Deterministic semantic/capability audit for generated Studio source.

Runner-green is necessary but not sufficient: a generated application must still
represent the approved requirement ledger and must actually consume capabilities it
declares.  This module intentionally uses conservative source evidence rather than
model judgement so the outer Studio controller can keep coding until the original
objective is materially represented.
"""
from __future__ import annotations

import json
import re
from typing import Any


_STOPWORDS = frozenset(
    {
        "able", "about", "after", "again", "against", "another", "because", "been",
        "before", "being", "between", "both", "build", "built", "could", "create",
        "each", "from", "have", "into", "make", "must", "need", "needs", "other",
        "should", "system", "that", "their", "them", "then", "there", "these", "they",
        "this", "through", "using", "user", "users", "when", "where", "which", "while",
        "with", "would", "code", "application", "app", "feature", "support", "allow",
    }
)
_TECHNICAL_ANCHORS = frozenset(
    {
        "qr", "camera", "barcode", "scanner", "scan", "gps", "map", "microphone",
        "audio", "video", "webgl", "3d", "csv", "json", "pdf", "email", "calendar",
        "payment", "crypto", "usdt", "usdc", "inventory", "checkout", "dispatch",
        "offline", "upload", "download", "websocket", "notification", "employee",
    }
)
_SOURCE_SUFFIXES = (".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".html", ".css")


def _plan_data(plan: Any) -> dict[str, Any]:
    if hasattr(plan, "model_dump"):
        value = plan.model_dump(mode="json")
        return value if isinstance(value, dict) else {}
    return dict(plan) if isinstance(plan, dict) else {}


def _source_files(source: Any) -> dict[str, str]:
    raw = getattr(source, "files_json", None)
    if raw is not None:
        try:
            records = json.loads(raw or "[]")
        except (TypeError, json.JSONDecodeError):
            records = []
        return {
            str(item.get("path")): str(item.get("content"))
            for item in records
            if isinstance(item, dict) and item.get("path") and isinstance(item.get("content"), str)
        }
    files = getattr(source, "files", source)
    if isinstance(files, dict):
        return {
            str(path): (content.decode("utf-8") if isinstance(content, bytes) else str(content))
            for path, content in files.items()
        }
    result: dict[str, str] = {}
    for item in files or ():
        path = str(getattr(item, "path", ""))
        content = getattr(item, "content", "")
        if path:
            result[path] = content.decode("utf-8") if isinstance(content, bytes) else str(content)
    return result


def _stem(word: str) -> str:
    value = word.lower().strip("_- ")
    if len(value) > 5 and value.endswith("ing"):
        value = value[:-3]
    elif len(value) > 4 and value.endswith("ed"):
        value = value[:-2]
    elif len(value) > 4 and value.endswith("es"):
        value = value[:-2]
    elif len(value) > 3 and value.endswith("s"):
        value = value[:-1]
    return value


def _tokens(text: str) -> set[str]:
    return {
        _stem(token)
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_+-]*", str(text or "").lower())
        if len(token) >= 2
    }


def _anchors(text: str) -> list[str]:
    words = []
    for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_+-]*", str(text or "").lower()):
        stem = _stem(token)
        if not stem or stem in _STOPWORDS:
            continue
        if len(stem) < 4 and stem not in _TECHNICAL_ANCHORS:
            continue
        if stem not in words:
            words.append(stem)
    return words[:24]


def _requirements(plan: Any) -> list[dict[str, Any]]:
    data = _plan_data(plan)
    rows = data.get("requirementLedger") or data.get("requirements") or []
    result = []
    for index, item in enumerate(rows, 1):
        if not isinstance(item, dict) or not bool(item.get("mandatory", True)):
            continue
        exact = str(item.get("exactText") or "").strip()
        meaning = str(item.get("normalizedMeaning") or item.get("requirement") or exact).strip()
        acceptance = [str(value) for value in (item.get("acceptanceCriteria") or item.get("acceptance") or [])]
        text = " ".join([meaning or exact, *acceptance]).strip()
        if text:
            result.append({"id": str(item.get("id") or f"R-{index:03d}"), "text": text})
    if result:
        return result
    objective = str(data.get("summary") or data.get("primaryGoal") or "").strip()
    return [{"id": "objective", "text": objective}] if objective else []


def _manifest(files: dict[str, str]) -> dict[str, Any]:
    payload = files.get("operly.solution.json")
    if not payload:
        return {}
    try:
        value = json.loads(payload)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def _code_corpus(files: dict[str, str]) -> str:
    included = []
    for path, content in files.items():
        lower = path.lower()
        if lower.endswith(_SOURCE_SUFFIXES) or lower.startswith("tests/"):
            included.append(content)
    return "\n".join(included).lower()


def _binding_gaps(manifest: dict[str, Any], files: dict[str, str]) -> list[dict[str, str]]:
    bindings = [item for item in (manifest.get("bindings") or []) if isinstance(item, dict)]
    runtime_source = "\n".join(
        content for path, content in files.items()
        if path.startswith(("backend/", "workers/")) and path.lower().endswith((".py", ".js", ".ts"))
    )
    lower = runtime_source.lower()
    has_binding_file = "operly_bindings_file" in lower
    gaps: list[dict[str, str]] = []
    for item in bindings:
        capability = str(item.get("capabilityId") or "").strip()
        semantic = str(item.get("semanticName") or "").strip().lower()
        if capability == "data.relational":
            operations = any(marker in lower for marker in ("/query", "/insert", "/update", "/delete"))
            if not has_binding_file or not operations:
                gaps.append({
                    "capabilityId": capability,
                    "semanticName": semantic,
                    "reason": "Declared relational data is not consumed through OPERLY_BINDINGS_FILE and a relational operation endpoint.",
                })
        elif capability == "data.workspace_entities":
            if not has_binding_file or (semantic and semantic not in lower):
                gaps.append({
                    "capabilityId": capability,
                    "semanticName": semantic,
                    "reason": "Declared workspace entity binding is not resolved and consumed by generated runtime source.",
                })
        elif capability == "identity.app_users":
            identity_ops = any(marker in lower for marker in ("/register", "/login", "/session", "/logout", "/invitations/accept"))
            if not has_binding_file or not identity_ops:
                gaps.append({
                    "capabilityId": capability,
                    "semanticName": semantic,
                    "reason": "Declared application identity is not consumed through the app-user binding API.",
                })
    return gaps


def _runtime_gaps(manifest: dict[str, Any], files: dict[str, str]) -> list[str]:
    if str(manifest.get("runtime") or "") != "operly-fullstack-v1":
        return []
    backend = files.get("backend/app.py", "").lower()
    gaps = []
    if backend and ("--host" not in backend or "--port" not in backend):
        gaps.append("backend/app.py must accept runner-owned --host and --port arguments instead of hard-coding its listen address.")
    health_path = str(((manifest.get("execution") or {}).get("healthPath") or "/health"))
    if backend and health_path.lower() not in backend:
        gaps.append(f"backend/app.py does not expose the declared health path {health_path}.")
    return gaps


def audit_generated_source(plan: Any, source: Any) -> dict[str, Any]:
    """Return deterministic evidence that generated source still satisfies its objective."""
    files = _source_files(source)
    manifest = _manifest(files)
    corpus_tokens = _tokens(_code_corpus(files))
    coverage = []
    unmet = []
    for requirement in _requirements(plan):
        anchors = _anchors(requirement["text"])
        matched = [anchor for anchor in anchors if anchor in corpus_tokens]
        strong = [anchor for anchor in anchors if anchor in _TECHNICAL_ANCHORS]
        missing_strong = [anchor for anchor in strong if anchor not in corpus_tokens]
        ratio = 1.0 if not anchors else len(matched) / len(anchors)
        # Strong product nouns (camera, QR, checkout, etc.) are not allowed to vanish
        # during runner repair.  Otherwise require broad lexical evidence, but keep the
        # threshold conservative to avoid dictating implementation vocabulary.
        verified = not missing_strong and (not anchors or ratio >= 0.55 or len(matched) >= min(4, len(anchors)))
        row = {
            "id": requirement["id"],
            "verified": verified,
            "anchors": anchors,
            "matched": matched,
            "missingStrongAnchors": missing_strong,
            "coverage": round(ratio, 3),
        }
        coverage.append(row)
        if not verified:
            unmet.append(row)

    capability_gaps = _binding_gaps(manifest, files)
    runtime_gaps = _runtime_gaps(manifest, files)
    verified = not unmet and not capability_gaps and not runtime_gaps
    parts = []
    if unmet:
        parts.append("mandatory requirement behavior is missing from executable source")
    if capability_gaps:
        parts.append("declared workspace capabilities are not actually consumed")
    if runtime_gaps:
        parts.append("generated runtime source violates deterministic runner mechanics")
    return {
        "verified": verified,
        "classification": "objective_verified" if verified else "objective_incomplete",
        "message": "Original objective remains materially represented." if verified else "; ".join(parts),
        "sourceVersion": getattr(source, "source_version", None),
        "requirementCoverage": coverage,
        "unmetRequirements": unmet,
        "capabilityUsageGaps": capability_gaps,
        "runtimeContractGaps": runtime_gaps,
    }


__all__ = ["audit_generated_source"]
