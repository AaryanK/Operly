"""Deterministic runtime-profile selection from harness-authored source trees.

The software planner owns semantics; the coding harness chooses finite execution
mechanics from the concrete source tree. Runtime selection never depends on a
model-invented stack label.
"""
from __future__ import annotations

import json
import posixpath

from packages.custom_software.source_bundles import SourceBundle


class RuntimeResolutionError(ValueError):
    pass


_JS_TEST_SUFFIXES = (".test.js", ".test.mjs", ".test.cjs", ".spec.js", ".spec.mjs", ".spec.cjs")
_JS_SUFFIXES = (".js", ".mjs", ".cjs")


def _paths(bundle: SourceBundle) -> set[str]:
    return {item.path.lower() for item in bundle.files}


def _is_js_test(path: str) -> bool:
    path = path.lower()
    return path.endswith(_JS_TEST_SUFFIXES) or (path.startswith("tests/") and path.endswith(_JS_SUFFIXES))


def infer_runtime_profile(bundle: SourceBundle) -> str:
    paths = _paths(bundle)
    if {"app.py", "build.py"} <= paths and any(path.startswith("test_") and path.endswith(".py") or path.startswith("tests/") and path.endswith(".py") for path in paths):
        return "python-stdlib-web"

    has_html_entrypoint = "index.html" in paths
    has_javascript = any(path.endswith(_JS_SUFFIXES) and not _is_js_test(path) for path in paths)
    has_js_test = any(_is_js_test(path) for path in paths)
    if has_html_entrypoint and has_javascript and has_js_test:
        return "static-web-js"

    raise RuntimeResolutionError(
        "No isolated runner profile matches the generated source tree; the coding harness must create a supported entrypoint and executable test shape first"
    )


def _validate_static_dependencies(bundle: SourceBundle) -> None:
    package = next((item for item in bundle.files if item.path.lower() == "package.json"), None)
    if not package:
        return
    try:
        data = json.loads(package.content.decode("utf-8"))
    except Exception as error:
        raise RuntimeResolutionError("package.json is invalid JSON") from error
    if data.get("dependencies") or data.get("devDependencies"):
        raise RuntimeResolutionError("static-web-js is dependency-free; third-party package dependencies require a different isolated runtime profile")


def _test_references_application_source(bundle: SourceBundle) -> bool:
    source_paths = [item.path for item in bundle.files if item.path.lower().endswith(_JS_SUFFIXES) and not _is_js_test(item.path)]
    tests = [item for item in bundle.files if _is_js_test(item.path)]
    for test in tests:
        text = test.content.decode("utf-8", errors="replace").lower()
        test_dir = posixpath.dirname(test.path)
        for source in source_paths:
            basename = posixpath.basename(source).lower()
            relative = posixpath.relpath(source, test_dir or ".").lower()
            if basename in text or relative in text or ("./" + relative) in text:
                return True
    return False


def validate_runtime_contract(bundle: SourceBundle) -> str:
    """Return a profile only when its executable quality gate is structurally real."""
    profile_id = infer_runtime_profile(bundle)
    if profile_id == "python-stdlib-web":
        return profile_id

    _validate_static_dependencies(bundle)
    test_sources = [item.content.decode("utf-8", errors="replace").lower() for item in bundle.files if _is_js_test(item.path)]
    if not test_sources or not any("node:test" in source for source in test_sources):
        raise RuntimeResolutionError(
            "Static-web source must include non-interactive tests using Node's built-in node:test runner; manual browser-console verification is not an executable test"
        )
    if not _test_references_application_source(bundle):
        raise RuntimeResolutionError(
            "Static-web tests must import or require generated application JavaScript; tautological tests that do not exercise the codebase are rejected"
        )
    return profile_id
