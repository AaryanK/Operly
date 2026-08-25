"""Trusted runtime plugin for ``operly-fullstack-v1`` source bundles."""
from __future__ import annotations

from typing import Any

from packages.runtime_plugins.runner_contracts import (
    BuildSubmission,
    Dependency,
    HealthCheck,
    NetworkPolicy,
    ResourcePolicy,
    ServiceBindingRequest,
)
from packages.runtime_plugins.app_identity_source_validation import validate_app_identity_source
from packages.runtime_plugins.contracts import (
    DependencyPolicy,
    RuntimeMatch,
    RuntimePluginSpec,
    RuntimeValidation,
)
from packages.runtime_plugins.fullstack_contract import (
    FULLSTACK_MANIFEST,
    FULLSTACK_PROFILE_VERSION,
    FULLSTACK_RUNTIME_ID,
    parse_fullstack_manifest,
    validate_fullstack_source,
)
from packages.runtime_plugins.relational_source_validation import validate_relational_source
from packages.workspace_entities.manifest import validate_workspace_entity_source


_FULLSTACK_OPERATIONS = (
    "stage_source",
    "resolve_dependencies",
    "static_analysis",
    "build",
    "test",
    "start",
    "health_check",
    "acceptance_test",
)
_FULLSTACK_RESOURCES = ResourcePolicy(
    cpu=2,
    memoryMb=1536,
    processes=64,
    openFiles=512,
    diskMb=1024,
    durationSeconds=600,
    idleSeconds=120,
    logBytes=2_000_000,
    artifactBytes=50_000_000,
    previewSeconds=1800,
)


def _paths(source: Any) -> set[str]:
    files = getattr(source, "files", source)
    if isinstance(files, dict):
        return {str(path) for path in files}
    return {str(getattr(item, "path", "")) for item in (files or ())}


class FullStackRuntime:
    """Translate the typed Solution manifest into a runner-owned submission."""

    spec = RuntimePluginSpec(
        id=FULLSTACK_RUNTIME_ID,
        version=str(FULLSTACK_PROFILE_VERSION),
        languages=frozenset({"python", "javascript"}),
        source_markers=(FULLSTACK_MANIFEST, "backend/app.py", "tests/"),
        operations=_FULLSTACK_OPERATIONS,
        dependency_policy=DependencyPolicy(
            mode="lockfile_registry_only",
            registries=frozenset({"pypi", "npm"}),
            max_dependencies=100,
        ),
        network_policy=NetworkPolicy(mode="loopback_only"),
        resource_policy=_FULLSTACK_RESOURCES,
        supports_preview=True,
        supports_deploy=False,
        service_binding_modes=frozenset({"capability_gateway"}),
    )

    def detect(self, source: Any) -> RuntimeMatch:
        paths = _paths(source)
        matched = FULLSTACK_MANIFEST in paths
        return RuntimeMatch(matched, 1000 if matched else 0, ("operly full-stack manifest",) if matched else ())

    def validate(self, source: Any) -> RuntimeValidation:
        base = validate_fullstack_source(source)
        if not base.valid:
            return base
        relational = validate_relational_source(source)
        entities = validate_workspace_entity_source(source)
        identity = validate_app_identity_source(source)
        return RuntimeValidation(
            base.valid and relational.valid and entities.valid and identity.valid,
            tuple(dict.fromkeys((*base.errors, *relational.errors, *entities.errors, *identity.errors))),
            tuple(dict.fromkeys((*base.warnings, *relational.warnings, *entities.warnings, *identity.warnings))),
        )

    def build_submission_from_record(self, source_record, source_bundle, idempotency_key: str) -> BuildSubmission:
        validation = self.validate(source_bundle)
        if not validation.valid:
            raise ValueError("; ".join(validation.errors))
        manifest = parse_fullstack_manifest(source_bundle)
        dependencies = [
            Dependency(ecosystem=item.ecosystem, name=item.name, version=item.version, registry="npm" if item.ecosystem == "npm" else "pypi")
            for item in manifest.dependencies
        ]
        bindings = [
            ServiceBindingRequest(semanticName=item.semanticName, capabilityId=item.capabilityId, required=item.required)
            for item in manifest.bindings
        ]
        artifacts = ["artifacts"]
        if manifest.execution.frontend == "npm-build":
            artifacts.append("frontend/dist")
        return BuildSubmission(
            workspaceId=source_record.tenant_id,
            applicationId=source_record.application_id,
            planVersion=source_record.plan_version,
            sourceVersion=source_record.source_version,
            stackId=FULLSTACK_RUNTIME_ID,
            stackVersion=FULLSTACK_PROFILE_VERSION,
            sourceBundleDigest=source_record.bundle_digest,
            dependencies=dependencies,
            operations=list(_FULLSTACK_OPERATIONS),
            healthCheck=HealthCheck(path=manifest.execution.healthPath, expectedStatus=200, timeoutSeconds=45),
            resources=self.spec.resource_policy,
            installNetwork=NetworkPolicy(mode="dependency_registry_only" if dependencies else "none"),
            network=self.spec.network_policy,
            serviceBindings=bindings,
            requiredPorts=[8080],
            artifactPaths=artifacts,
            maxDurationSeconds=self.spec.resource_policy.durationSeconds,
            idempotencyKey=idempotency_key,
        )

    def build_submission(self, project, source, bindings):
        raise NotImplementedError("Use the canonical persisted source build service; project-native submissions are a later migration")


__all__ = ["FullStackRuntime"]
