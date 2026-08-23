"""Built-in adapters for the runtime profiles Operly already trusts today."""
from __future__ import annotations

import json
import posixpath
from typing import Any

from packages.coding_harness.interaction_contracts import (
    InteractionContractError,
    validate_interaction_contract,
)
from packages.custom_software.runner_contracts import (
    BuildSubmission,
    HealthCheck,
    NetworkPolicy,
    ResourcePolicy,
)
from packages.custom_software.runtime_profiles import runtime_profile
from packages.runtime_plugins.contracts import (
    DependencyPolicy,
    RuntimeMatch,
    RuntimePluginSpec,
    RuntimeValidation,
)
from packages.runtime_plugins.fullstack_runtime import FullStackRuntime
from packages.runtime_plugins.registry import RuntimeRegistry, default_runtime_registry


_JS_TEST_SUFFIXES = (
    ".test.js",
    ".test.mjs",
    ".test.cjs",
    ".spec.js",
    ".spec.mjs",
    ".spec.cjs",
)
_JS_SUFFIXES = (".js", ".mjs", ".cjs")


def _files(source: Any):
    return tuple(getattr(source, "files", ()) or ())


def _path(item: Any) -> str:
    return str(getattr(item, "path", "")).lower()


def _content(item: Any) -> str:
    raw = getattr(item, "content", b"")
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw or "")


def _paths(source: Any) -> set[str]:
    return {_path(item) for item in _files(source)}


def _is_js_test(path: str) -> bool:
    path = path.lower()
    return path.endswith(_JS_TEST_SUFFIXES) or (
        path.startswith("tests/") and path.endswith(_JS_SUFFIXES)
    )


def _test_references_application_source(source: Any) -> bool:
    source_paths = [
        _path(item)
        for item in _files(source)
        if _path(item).endswith(_JS_SUFFIXES) and not _is_js_test(_path(item))
    ]
    tests = [item for item in _files(source) if _is_js_test(_path(item))]
    for test in tests:
        text = _content(test).lower()
        test_dir = posixpath.dirname(_path(test))
        for app_source in source_paths:
            basename = posixpath.basename(app_source)
            relative = posixpath.relpath(app_source, test_dir or ".").lower()
            if basename in text or relative in text or ("./" + relative) in text:
                return True
    return False


def _spec(profile_id: str, *, language: str, markers: tuple[str, ...]) -> RuntimePluginSpec:
    profile = runtime_profile(profile_id)
    return RuntimePluginSpec(
        id=profile_id,
        version=str(profile.get("profileVersion", 1)),
        languages=frozenset({language}),
        source_markers=markers,
        operations=tuple(profile["operations"]),
        dependency_policy=DependencyPolicy(mode="none", max_dependencies=0),
        network_policy=NetworkPolicy(mode="none"),
        resource_policy=ResourcePolicy.model_validate(profile["resources"]),
        supports_preview=True,
        supports_deploy=False,
        service_binding_modes=frozenset(),
    )


class PythonStdlibWebRuntime:
    spec = _spec(
        "python-stdlib-web",
        language="python",
        markers=("app.py", "build.py"),
    )

    def detect(self, source: Any) -> RuntimeMatch:
        paths = _paths(source)
        has_tests = any(
            (path.startswith("test_") or path.startswith("tests/"))
            and path.endswith(".py")
            for path in paths
        )
        matched = {"app.py", "build.py"}.issubset(paths) and has_tests
        return RuntimeMatch(matched, 100 if matched else 0, ("python stdlib markers",) if matched else ())

    def validate(self, source: Any) -> RuntimeValidation:
        try:
            validate_interaction_contract(source)
        except InteractionContractError as error:
            return RuntimeValidation(False, (str(error),))
        return RuntimeValidation(True)

    def build_submission_from_record(self, source_record, source_bundle, idempotency_key: str) -> BuildSubmission:
        return _build_submission(self, source_record, source_bundle, idempotency_key)

    def build_submission(self, project, source, bindings):
        raise NotImplementedError("Use canonical project build service after SoftwareProject persistence migration")


class StaticWebRuntime:
    spec = _spec(
        "static-web-js",
        language="javascript",
        markers=("index.html",),
    )

    def detect(self, source: Any) -> RuntimeMatch:
        paths = _paths(source)
        has_html = "index.html" in paths
        has_javascript = any(
            path.endswith(_JS_SUFFIXES) and not _is_js_test(path) for path in paths
        )
        has_test = any(_is_js_test(path) for path in paths)
        matched = has_html and has_javascript and has_test
        return RuntimeMatch(matched, 100 if matched else 0, ("static web markers",) if matched else ())

    def validate(self, source: Any) -> RuntimeValidation:
        package = next((item for item in _files(source) if _path(item) == "package.json"), None)
        if package:
            try:
                data = json.loads(_content(package))
            except Exception:
                return RuntimeValidation(False, ("package.json is invalid JSON",))
            if data.get("dependencies") or data.get("devDependencies"):
                return RuntimeValidation(
                    False,
                    ("static-web-js is dependency-free; third-party dependencies require another runtime plugin",),
                )

        tests = [_content(item).lower() for item in _files(source) if _is_js_test(_path(item))]
        if not tests or not any("node:test" in text for text in tests):
            return RuntimeValidation(
                False,
                ("Static-web source must include tests using Node's built-in node:test runner",),
            )
        if not _test_references_application_source(source):
            return RuntimeValidation(
                False,
                ("Static-web tests must exercise generated application JavaScript",),
            )
        try:
            validate_interaction_contract(source)
        except InteractionContractError as error:
            return RuntimeValidation(False, (str(error),))
        return RuntimeValidation(True)

    def build_submission_from_record(self, source_record, source_bundle, idempotency_key: str) -> BuildSubmission:
        return _build_submission(self, source_record, source_bundle, idempotency_key)

    def build_submission(self, project, source, bindings):
        raise NotImplementedError("Use canonical project build service after SoftwareProject persistence migration")


def _build_submission(plugin, source_record, source_bundle, idempotency_key: str) -> BuildSubmission:
    validation = plugin.validate(source_bundle)
    if not validation.valid:
        raise ValueError("; ".join(validation.errors))
    profile = runtime_profile(plugin.spec.id)
    resources = plugin.spec.resource_policy
    return BuildSubmission(
        workspaceId=source_record.tenant_id,
        applicationId=source_record.application_id,
        planVersion=source_record.plan_version,
        sourceVersion=source_record.source_version,
        stackId=plugin.spec.id,
        stackVersion=int(plugin.spec.version),
        sourceBundleDigest=source_record.bundle_digest,
        operations=list(plugin.spec.operations),
        healthCheck=HealthCheck.model_validate(profile["health"]),
        resources=resources,
        network=plugin.spec.network_policy,
        requiredPorts=profile["ports"],
        artifactPaths=profile["artifactPaths"],
        maxDurationSeconds=resources.durationSeconds,
        idempotencyKey=idempotency_key,
    )


def register_builtin_runtimes(registry: RuntimeRegistry | None = None) -> RuntimeRegistry:
    registry = registry or default_runtime_registry()
    for plugin in (PythonStdlibWebRuntime(), StaticWebRuntime(), FullStackRuntime()):
        try:
            registry.register(plugin)
        except ValueError:
            # Idempotent bootstrap across import paths.
            registry.register(plugin, replace=True)
    return registry
