"""Deterministic runtime-profile selection from harness-authored source trees.

The software planner is semantic authority; it does not need to choose execution
mechanics. The coding harness selects one of OPERLY's finite isolated-runner
profiles from the concrete files it actually produced.
"""
from __future__ import annotations

from packages.custom_software.source_bundles import SourceBundle


class RuntimeResolutionError(ValueError):
    pass


def _paths(bundle: SourceBundle) -> set[str]:
    return {item.path.lower() for item in bundle.files}


def infer_runtime_profile(bundle: SourceBundle) -> str:
    paths = _paths(bundle)

    # A Python application with the existing OPERLY runner contract remains the
    # most specific match. This preserves the established profile for source
    # trees that actually contain its entrypoint/build files.
    if {"app.py", "build.py"} <= paths:
        return "python-stdlib-web"

    # Browser applications are executed with a dependency-free Node profile.
    # Node is runner infrastructure here, not a product dependency: the source
    # can remain ordinary HTML/CSS/vanilla JavaScript.
    has_html_entrypoint = "index.html" in paths
    has_javascript = any(path.endswith((".js", ".mjs", ".cjs")) for path in paths)
    has_js_test = any(
        path.endswith((".test.js", ".test.mjs", ".test.cjs", ".spec.js", ".spec.mjs", ".spec.cjs"))
        or path.startswith("tests/") and path.endswith((".js", ".mjs", ".cjs"))
        for path in paths
    )
    if has_html_entrypoint and has_javascript and has_js_test:
        return "static-web-js"

    raise RuntimeResolutionError(
        "No isolated runner profile matches the generated source tree; "
        "the coding harness must generate an executable test/runtime shape first"
    )


def validate_runtime_contract(bundle: SourceBundle) -> str:
    """Return the selected profile only when its executable quality gate exists."""
    profile_id = infer_runtime_profile(bundle)
    if profile_id != "static-web-js":
        return profile_id

    test_sources = []
    for item in bundle.files:
        path = item.path.lower()
        is_test = (
            path.endswith((".test.js", ".test.mjs", ".test.cjs", ".spec.js", ".spec.mjs", ".spec.cjs"))
            or path.startswith("tests/") and path.endswith((".js", ".mjs", ".cjs"))
        )
        if is_test:
            test_sources.append(item.content.decode("utf-8", errors="replace").lower())
    if not test_sources or not any("node:test" in source for source in test_sources):
        raise RuntimeResolutionError(
            "Static-web source must include non-interactive tests using Node's built-in node:test runner; "
            "manual browser-console verification does not satisfy the isolated-runner test gate"
        )
    return profile_id
