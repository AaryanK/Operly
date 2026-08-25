"""Deterministic semantic/capability audit for generated Studio source.

A runner-green application is not automatically a correct application. The Studio
outer controller uses this evidence to keep the original user objective, mandatory
requirements, capability usage, and runner-owned mechanics intact across repairs.
"""
from __future__ import annotations

import ast
import json
import re
from typing import Any


_STOPWORDS = frozenset({
    "able", "about", "after", "again", "against", "another", "because", "been",
    "before", "being", "between", "both", "build", "built", "could", "create", "each",
    "from", "have", "into", "make", "must", "need", "needs", "other", "should",
    "system", "that", "their", "them", "then", "there", "these", "they", "this",
    "through", "using", "user", "users", "when", "where", "which", "while", "with",
    "would", "code", "application", "app", "feature", "support", "allow",
})
_TECHNICAL_ANCHORS = frozenset({
    "qr", "camera", "barcode", "scanner", "scan", "gps", "map", "microphone", "audio",
    "video", "webgl", "3d", "csv", "json", "pdf", "email", "calendar", "payment",
    "crypto", "usdt", "usdc", "inventory", "checkout", "dispatch", "offline", "upload",
    "download", "websocket", "notification", "employee",
})
_SOURCE_SUFFIXES = (".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".html", ".css")
_HTTP_CLIENT_MARKERS = (
    "urllib.request", "urlopen(", "http.client", "requests.", "httpx.", "fetch(",
)
_FAKE_CAPABILITY_MARKERS = (
    "mock relational", "mock workspace", "mock identity", "mock implementation",
    "simulate relational", "simulate workspace", "simulate identity",
    "demonstrate consumption", "assert isinstance(",
)


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
        return {str(path): (value.decode("utf-8") if isinstance(value, bytes) else str(value)) for path, value in files.items()}
    result: dict[str, str] = {}
    for item in files or ():
        path = str(getattr(item, "path", ""))
        value = getattr(item, "content", "")
        if path:
            result[path] = value.decode("utf-8") if isinstance(value, bytes) else str(value)
    return result


def _stem(word: str) -> str:
    value = word.lower().strip("_- ")
    if len(value) > 5 and value.endswith("ing"):
        value = value[:-3]
        if len(value) > 2 and value[-1] == value[-2]:
            value = value[:-1]
        return value
    if len(value) > 4 and value.endswith("ed"):
        return value[:-2]
    if len(value) > 4 and value.endswith("es"):
        return value[:-2]
    if len(value) > 3 and value.endswith("s"):
        return value[:-1]
    return value


def _tokens(text: str) -> set[str]:
    return {_stem(token) for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_+-]*", str(text or "").lower()) if len(token) >= 2}


def _anchors(text: str) -> list[str]:
    result: list[str] = []
    for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_+-]*", str(text or "").lower()):
        stem = _stem(token)
        if not stem or stem in _STOPWORDS or (len(stem) < 4 and stem not in _TECHNICAL_ANCHORS):
            continue
        if stem not in result:
            result.append(stem)
    return result[:24]


def _requirements(plan: Any) -> list[dict[str, str]]:
    data = _plan_data(plan)
    provenance = data.get("provenance") if isinstance(data.get("provenance"), dict) else {}
    original = str(provenance.get("originalPrompt") or "").strip()
    summary = str(data.get("summary") or data.get("primaryGoal") or "").strip()
    result: list[dict[str, str]] = []
    root = original or summary
    if root:
        result.append({"id": "ROOT_OBJECTIVE", "text": root})
    rows = data.get("requirementLedger") or data.get("requirements") or []
    for index, item in enumerate(rows, 1):
        if not isinstance(item, dict) or not bool(item.get("mandatory", True)):
            continue
        exact = str(item.get("exactText") or "").strip()
        meaning = str(item.get("normalizedMeaning") or item.get("requirement") or exact).strip()
        acceptance = [str(value) for value in (item.get("acceptanceCriteria") or item.get("acceptance") or [])]
        text = " ".join([meaning or exact, *acceptance]).strip()
        if text and text != root:
            result.append({"id": str(item.get("id") or f"R-{index:03d}"), "text": text})
    return result


def _manifest(files: dict[str, str]) -> dict[str, Any]:
    try:
        value = json.loads(files.get("operly.solution.json", "{}"))
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def _code_corpus(files: dict[str, str]) -> str:
    return "\n".join(
        content for path, content in files.items()
        if path.lower().endswith(_SOURCE_SUFFIXES) or path.lower().startswith("tests/")
    ).lower()


def _runtime_source(files: dict[str, str]) -> str:
    return "\n".join(
        content for path, content in files.items()
        if path.startswith(("backend/", "workers/")) and path.lower().endswith((".py", ".js", ".ts"))
    ).lower()


def _frontend_source(files: dict[str, str]) -> str:
    return "\n".join(
        content for path, content in files.items()
        if path.startswith("frontend/") and path.lower().endswith((".html", ".js", ".mjs", ".ts", ".tsx"))
    ).lower()


def _binding_gaps(manifest: dict[str, Any], files: dict[str, str]) -> list[dict[str, str]]:
    bindings = [item for item in (manifest.get("bindings") or []) if isinstance(item, dict)]
    runtime_source = _runtime_source(files)
    has_binding_file = "operly_bindings_file" in runtime_source
    has_endpoint_resolution = "endpoint" in runtime_source and any(marker in runtime_source for marker in _HTTP_CLIENT_MARKERS)
    fake = next((marker for marker in _FAKE_CAPABILITY_MARKERS if marker in runtime_source), "")
    gaps: list[dict[str, str]] = []
    for item in bindings:
        capability = str(item.get("capabilityId") or "").strip()
        semantic = str(item.get("semanticName") or "").strip().lower()
        if capability not in {"data.relational", "data.workspace_entities", "identity.app_users"}:
            continue
        if fake:
            gaps.append({
                "capabilityId": capability,
                "semanticName": semantic,
                "reason": f"Capability implementation contains fake/mock evidence ({fake}); comments, assertions, or local stand-ins do not consume an Operly binding.",
            })
            continue
        if not has_binding_file or not has_endpoint_resolution:
            gaps.append({
                "capabilityId": capability,
                "semanticName": semantic,
                "reason": "Generated runtime must read OPERLY_BINDINGS_FILE, resolve the matching injected endpoint, and make a real HTTP request through that endpoint.",
            })
            continue
        if capability == "data.relational":
            used = any(marker in runtime_source for marker in ("/query", "/insert", "/update", "/delete"))
            reason = "Declared relational data is not used through its injected endpoint with /query, /insert, /update, or /delete."
        elif capability == "data.workspace_entities":
            used = bool(semantic) and semantic in runtime_source and any(
                marker in runtime_source for marker in ("/schema", "/list", "/create", "/update")
            )
            reason = "Declared workspace entity binding must call its injected endpoint using /schema, /list, /create, /update, or an entity GET path; relational /query is not a workspace-entity operation."
        else:
            used = any(marker in runtime_source for marker in ("/register", "/login", "/session", "/logout", "/invitations/accept"))
            reason = "Declared application identity is not consumed through the injected identity endpoint."
        if not used:
            gaps.append({"capabilityId": capability, "semanticName": semantic, "reason": reason})
    return gaps


def _migration_tables(files: dict[str, str]) -> set[str]:
    tables: set[str] = set()
    for path, content in files.items():
        if not path.startswith("migrations/") or not path.endswith(".json"):
            continue
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            continue
        for operation in payload.get("operations") or []:
            if isinstance(operation, dict) and operation.get("op") == "create_table":
                table = str(operation.get("table") or "").strip().lower()
                if table:
                    tables.add(table)
    return tables


def _mutable_assignment_names(source: str) -> set[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in tree.body:
        value = node.value if isinstance(node, (ast.Assign, ast.AnnAssign)) else None
        if not isinstance(value, (ast.List, ast.Dict, ast.Set)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name):
                names.add(target.id.lower())
    return names


def _authority_gaps(manifest: dict[str, Any], files: dict[str, str], requirement_text: str) -> list[str]:
    bindings = {str(item.get("capabilityId") or "") for item in (manifest.get("bindings") or []) if isinstance(item, dict)}
    runtime_source = _runtime_source(files)
    gaps: list[str] = []
    if "data.relational" in bindings:
        persistence_required = any(marker in requirement_text for marker in ("persist", "durable", "survive", "history", "record", "attendance", "status"))
        if persistence_required and "in-memory" in runtime_source:
            gaps.append("Product requires durable state but backend explicitly describes authoritative state as in-memory while data.relational is declared.")
        table_names = _migration_tables(files)
        mutable_names: set[str] = set()
        for path, content in files.items():
            if path.startswith(("backend/", "workers/")) and path.endswith(".py"):
                mutable_names |= _mutable_assignment_names(content)
        duplicated = sorted(table_names & mutable_names)
        if duplicated:
            gaps.append("Relational tables are shadowed by module-level mutable collections instead of being authoritative in Operly data: " + ", ".join(duplicated))
    if "identity.app_users" in bindings or "data.workspace_entities" in bindings:
        hardcoded_patterns = (
            r"allowed_user_ids\s*=\s*[\[{(]",
            r"allowed_employee_ids\s*=\s*[\[{(]",
            r"employee_id\s*[:=]\s*['\"]emp\d+['\"]",
            r"employee_id\s*[:=]\s*['\"]emp-?1['\"]",
        )
        if any(re.search(pattern, runtime_source) for pattern in hardcoded_patterns):
            gaps.append("Canonical/application identity is replaced by hard-coded employee/user IDs instead of resolving the declared Operly binding.")
        frontend = _frontend_source(files)
        if re.search(r"employee_id\s*:\s*['\"]emp-?\d+['\"]", frontend):
            gaps.append("Frontend hard-codes an employee identity instead of using the generated app/workspace identity flow.")
    return gaps


def _behavior_gaps(requirement_text: str, files: dict[str, str]) -> list[dict[str, str]]:
    frontend = _frontend_source(files)
    all_code = _code_corpus(files)
    gaps: list[dict[str, str]] = []
    needs_camera = "camera" in requirement_text
    needs_qr = bool(re.search(r"\bqr\b", requirement_text))
    if needs_camera:
        camera_api = "getusermedia" in frontend and ("navigator.mediadevices" in frontend or "mediadevices.getusermedia" in frontend)
        video_surface = "<video" in frontend or ".srcobject" in frontend
        if not camera_api or not video_surface:
            gaps.append({
                "behavior": "camera_capture",
                "reason": "Camera requirement needs a real browser camera flow using navigator.mediaDevices.getUserMedia plus a video/capture surface; labels/comments are insufficient.",
            })
    if needs_qr:
        native_qr = "barcodedetector" in frontend and "qr_code" in frontend
        library_qr = any(marker in all_code for marker in ("jsqr", "html5-qrcode", "@zxing", "qr-scanner"))
        decoder_flow = any(marker in frontend for marker in ("decode", "scan", "rawvalue", "decoded"))
        if not (native_qr or library_qr) or not decoder_flow:
            gaps.append({
                "behavior": "qr_decode",
                "reason": "QR requirement needs an actual decoder path (BarcodeDetector configured for qr_code or a real QR library) wired into executable scan/decode logic.",
            })
    if needs_qr and "clock" in requirement_text and "out" in requirement_text:
        has_in = "/clock-in" in all_code or "/api/clock-in" in all_code
        has_out = "/clock-out" in all_code or "/api/clock-out" in all_code
        if not (has_in and has_out):
            gaps.append({
                "behavior": "qr_clock_workflow",
                "reason": "QR attendance objective needs distinct clock-in and clock-out operations driven by decoded scan data.",
            })
        if re.search(r"employee_id\s*:\s*['\"]emp-?\d+['\"]", frontend):
            gaps.append({
                "behavior": "qr_identity_flow",
                "reason": "QR clock flow cannot hard-code employee_id; decoded/authenticated identity must feed the clock operation.",
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
    """Return bounded evidence that source still represents the approved product."""
    files = _source_files(source)
    manifest = _manifest(files)
    requirements = _requirements(plan)
    requirement_text = " ".join(item["text"] for item in requirements).lower()
    corpus_tokens = _tokens(_code_corpus(files))
    coverage: list[dict[str, Any]] = []
    unmet: list[dict[str, Any]] = []
    for requirement in requirements:
        anchors = _anchors(requirement["text"])
        matched = [anchor for anchor in anchors if anchor in corpus_tokens]
        strong = [anchor for anchor in anchors if anchor in _TECHNICAL_ANCHORS]
        missing_strong = [anchor for anchor in strong if anchor not in corpus_tokens]
        ratio = 1.0 if not anchors else len(matched) / len(anchors)
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

    behavior_gaps = _behavior_gaps(requirement_text, files)
    capability_gaps = _binding_gaps(manifest, files)
    authority_gaps = _authority_gaps(manifest, files, requirement_text)
    runtime_gaps = _runtime_gaps(manifest, files)
    verified = not unmet and not behavior_gaps and not capability_gaps and not authority_gaps and not runtime_gaps
    parts = []
    if unmet or behavior_gaps:
        parts.append("original/mandatory requirement behavior is missing from executable source")
    if capability_gaps or authority_gaps:
        parts.append("declared workspace capabilities are not actually consumed as authoritative runtime services")
    if runtime_gaps:
        parts.append("generated runtime source violates deterministic runner mechanics")
    return {
        "verified": verified,
        "classification": "objective_verified" if verified else "objective_incomplete",
        "message": "Original objective remains materially represented." if verified else "; ".join(parts),
        "sourceVersion": getattr(source, "source_version", None),
        "requirementCoverage": coverage,
        "unmetRequirements": unmet,
        "behaviorGaps": behavior_gaps,
        "capabilityUsageGaps": capability_gaps,
        "authorityGaps": authority_gaps,
        "runtimeContractGaps": runtime_gaps,
    }


__all__ = ["audit_generated_source"]
