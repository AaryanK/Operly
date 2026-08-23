"""Controlled source contract for the first real full-stack Solution runtime.

The contract deliberately exposes *execution modes*, not arbitrary shell commands.
The coding model authors application source and a typed manifest; a trusted runner
owns dependency installation, process commands, ports, networking and resource
policy.  A source tree can be submitted only when the remote isolated runner
advertises the matching ``operly-fullstack-v1`` profile/version.
"""
from __future__ import annotations

import json
import posixpath
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from packages.runtime_plugins.contracts import RuntimeValidation

FULLSTACK_RUNTIME_ID = "operly-fullstack-v1"
FULLSTACK_PROFILE_VERSION = 1
FULLSTACK_SCHEMA_VERSION = "operly.solution/v1"
FULLSTACK_MANIFEST = "operly.solution.json"
# Control-plane execution support is enabled. Production still fails closed unless
# the configured isolated runner advertises this exact runtime profile/version.
FULLSTACK_EXECUTION_ENABLED = True

_REQUIRED_LAYOUT = {
    "frontend": "frontend",
    "backend": "backend",
    "workers": "workers",
    "tests": "tests",
    "migrations": "migrations",
}
_ALLOWED_ROOT_FILES = {FULLSTACK_MANIFEST, "README.md"}
_FORBIDDEN_SEGMENTS = {".env", ".npmrc", ".pypirc", "credentials.json", "secrets.json"}
_PACKAGE = re.compile(r"^[A-Za-z0-9@][A-Za-z0-9@/_.-]{0,119}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9*_.+!<>=~^|-]{0,79}$")
_CAPABILITY = re.compile(r"^[a-z][a-z0-9_.:-]{1,159}$")
_SEMANTIC_NAME = re.compile(r"^[a-z][a-z0-9_.-]{1,79}$")
_HEALTH_PATH = re.compile(r"^/[A-Za-z0-9_./-]*$")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _safe_relative_path(value: str) -> str:
    if not value or "\\" in value or value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        raise ValueError("Project paths must be relative POSIX paths")
    normalized = posixpath.normpath(value)
    if normalized in {".", ".."} or normalized.startswith("../") or "/../" in normalized:
        raise ValueError("Project path traversal is forbidden")
    return normalized.rstrip("/")


class FullStackLayout(_StrictModel):
    frontend: str = "frontend"
    backend: str = "backend"
    workers: str = "workers"
    tests: str = "tests"
    migrations: str = "migrations"

    @field_validator("frontend", "backend", "workers", "tests", "migrations")
    @classmethod
    def safe_path(cls, value: str) -> str:
        return _safe_relative_path(value)

    @model_validator(mode="after")
    def canonical_layout(self):
        actual = {name: getattr(self, name) for name in _REQUIRED_LAYOUT}
        if actual != _REQUIRED_LAYOUT:
            raise ValueError(
                "operly-fullstack-v1 uses the canonical frontend/backend/workers/tests/migrations layout"
            )
        return self


class FullStackExecution(_StrictModel):
    """Runner-owned execution modes; no model-authored commands are accepted."""

    frontend: Literal["static", "npm-build"] = "static"
    backend: Literal["python-cli"] = "python-cli"
    worker: Literal["none", "python-cli"] = "none"
    healthPath: str = "/health"

    @field_validator("healthPath")
    @classmethod
    def health_path(cls, value: str) -> str:
        if not _HEALTH_PATH.fullmatch(value):
            raise ValueError("healthPath must be a safe absolute HTTP path")
        return value


class FullStackDependency(_StrictModel):
    ecosystem: Literal["python", "npm"]
    name: str
    version: str

    @field_validator("name")
    @classmethod
    def package_name(cls, value: str) -> str:
        if not _PACKAGE.fullmatch(value) or value.startswith((".", "/")) or ".." in value:
            raise ValueError("Dependency name is not a registry package name")
        return value

    @field_validator("version")
    @classmethod
    def package_version(cls, value: str) -> str:
        if not _VERSION.fullmatch(value):
            raise ValueError("Dependency version must be a bounded registry version/range")
        return value

    @model_validator(mode="after")
    def ecosystem_name(self):
        if self.ecosystem == "python" and "/" in self.name:
            raise ValueError("Python dependency names cannot contain registry path separators")
        if self.ecosystem == "npm" and self.name.startswith("@") and self.name.count("/") != 1:
            raise ValueError("Scoped npm packages must use @scope/package form")
        return self


class FullStackBindingRequest(_StrictModel):
    """A semantic capability request, never a provider credential."""

    semanticName: str
    capabilityId: str
    required: bool = True

    @field_validator("semanticName")
    @classmethod
    def semantic_name(cls, value: str) -> str:
        if not _SEMANTIC_NAME.fullmatch(value):
            raise ValueError("Binding semanticName must be a stable lowercase identifier")
        return value

    @field_validator("capabilityId")
    @classmethod
    def capability_id(cls, value: str) -> str:
        if not _CAPABILITY.fullmatch(value):
            raise ValueError("Binding capabilityId is invalid")
        return value


class FullStackSolutionManifest(_StrictModel):
    schemaVersion: Literal["operly.solution/v1"] = FULLSTACK_SCHEMA_VERSION
    runtime: Literal["operly-fullstack-v1"] = FULLSTACK_RUNTIME_ID
    runtimeVersion: Literal[1] = FULLSTACK_PROFILE_VERSION
    layout: FullStackLayout = Field(default_factory=FullStackLayout)
    execution: FullStackExecution = Field(default_factory=FullStackExecution)
    dependencies: tuple[FullStackDependency, ...] = Field(default=(), max_length=100)
    bindings: tuple[FullStackBindingRequest, ...] = Field(default=(), max_length=100)

    @model_validator(mode="after")
    def unique_declarations(self):
        dependency_keys = [(item.ecosystem, item.name.lower()) for item in self.dependencies]
        if len(dependency_keys) != len(set(dependency_keys)):
            raise ValueError("Dependencies must be unique by ecosystem and package")
        semantic_names = [item.semanticName for item in self.bindings]
        if len(semantic_names) != len(set(semantic_names)):
            raise ValueError("Binding semanticName values must be unique")
        if any(item.ecosystem == "npm" for item in self.dependencies) and self.execution.frontend != "npm-build":
            raise ValueError("npm dependencies require execution.frontend=npm-build")
        return self


def _source_files(source) -> dict[str, bytes]:
    files = getattr(source, "files", source)
    if isinstance(files, dict):
        rows = files.items()
    else:
        rows = ((getattr(item, "path", ""), getattr(item, "content", b"")) for item in files)
    result: dict[str, bytes] = {}
    for raw_path, raw_content in rows:
        path = _safe_relative_path(str(raw_path))
        content = raw_content if isinstance(raw_content, bytes) else str(raw_content).encode()
        if path in result:
            raise ValueError(f"Duplicate source path: {path}")
        result[path] = content
    return result


def parse_fullstack_manifest(source) -> FullStackSolutionManifest:
    files = _source_files(source)
    payload = files.get(FULLSTACK_MANIFEST)
    if payload is None:
        raise ValueError(f"Missing {FULLSTACK_MANIFEST}")
    try:
        raw = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{FULLSTACK_MANIFEST} must contain UTF-8 JSON") from error
    return FullStackSolutionManifest.model_validate(raw)


def _json_file(files: dict[str, bytes], path: str, errors: list[str]) -> dict | None:
    payload = files.get(path)
    if payload is None:
        return None
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        errors.append(f"{path} must contain UTF-8 JSON")
        return None
    if not isinstance(value, dict):
        errors.append(f"{path} must contain a JSON object")
        return None
    return value


def validate_fullstack_source(source) -> RuntimeValidation:
    """Validate the complete source-tree boundary before any runner can execute it."""

    errors: list[str] = []
    warnings: list[str] = []
    try:
        files = _source_files(source)
        manifest = parse_fullstack_manifest(files)
    except (TypeError, ValueError) as error:
        return RuntimeValidation(False, (str(error),))

    roots = set(_REQUIRED_LAYOUT.values())
    for path in files:
        segments = tuple(segment.lower() for segment in path.split("/"))
        if any(segment in _FORBIDDEN_SEGMENTS for segment in segments):
            errors.append(f"Credential/config secret file is forbidden: {path}")
            continue
        if "/" not in path and path not in _ALLOWED_ROOT_FILES:
            errors.append(f"Unexpected root file for full-stack Solution: {path}")
            continue
        if "/" in path and path.split("/", 1)[0] not in roots:
            errors.append(f"Source must live under the declared full-stack layout: {path}")

    for required in (manifest.layout.frontend, manifest.layout.backend, manifest.layout.tests):
        if not any(path.startswith(required + "/") for path in files):
            errors.append(f"Full-stack Solution requires source under {required}/")

    if "backend/app.py" not in files:
        errors.append("operly-fullstack-v1 requires backend/app.py as the trusted Python CLI entrypoint")
    if manifest.execution.worker == "python-cli" and "workers/worker.py" not in files:
        errors.append("execution.worker=python-cli requires workers/worker.py")

    test_paths = [path for path in files if path.startswith("tests/")]
    if not any(path.endswith((".py", ".js", ".mjs", ".cjs")) for path in test_paths):
        errors.append("Full-stack Solution requires executable Python or Node tests under tests/")

    ecosystems = {dependency.ecosystem for dependency in manifest.dependencies}
    if "python" in ecosystems and "backend/requirements.lock" not in files:
        errors.append("Python dependencies require backend/requirements.lock")

    package_json = _json_file(files, "frontend/package.json", errors)
    package_lock = _json_file(files, "frontend/package-lock.json", errors)
    if manifest.execution.frontend == "npm-build":
        if package_json is None or package_lock is None:
            errors.append("npm-build frontend requires frontend/package.json and frontend/package-lock.json")
        elif not isinstance(package_json.get("scripts"), dict) or not package_json["scripts"].get("build"):
            errors.append("npm-build frontend requires a package.json build script")
    elif "frontend/index.html" not in files:
        errors.append("static frontend requires frontend/index.html")

    if "npm" in ecosystems:
        if package_json is None or package_lock is None:
            errors.append("npm dependencies require frontend/package.json and frontend/package-lock.json")
        elif package_json is not None:
            declared = {}
            for key in ("dependencies", "devDependencies"):
                values = package_json.get(key) or {}
                if isinstance(values, dict):
                    declared.update({str(name).lower(): str(version) for name, version in values.items()})
            for dependency in (item for item in manifest.dependencies if item.ecosystem == "npm"):
                if dependency.name.lower() not in declared:
                    errors.append(f"Manifest npm dependency is absent from package.json: {dependency.name}")

    if not manifest.dependencies:
        warnings.append("No third-party dependencies requested")
    if not manifest.bindings:
        warnings.append("No Operly service bindings requested")

    return RuntimeValidation(not errors, tuple(dict.fromkeys(errors)), tuple(warnings))


__all__ = [
    "FULLSTACK_RUNTIME_ID",
    "FULLSTACK_PROFILE_VERSION",
    "FULLSTACK_SCHEMA_VERSION",
    "FULLSTACK_MANIFEST",
    "FULLSTACK_EXECUTION_ENABLED",
    "FullStackLayout",
    "FullStackExecution",
    "FullStackDependency",
    "FullStackBindingRequest",
    "FullStackSolutionManifest",
    "parse_fullstack_manifest",
    "validate_fullstack_source",
]
