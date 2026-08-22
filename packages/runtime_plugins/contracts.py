"""Trusted runtime-plugin contracts for generated software execution."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from packages.custom_software.runner_contracts import NetworkPolicy, ResourcePolicy


@dataclass(frozen=True, slots=True)
class DependencyPolicy:
    mode: str = "none"
    registries: frozenset[str] = frozenset()
    allowed_packages: frozenset[str] = frozenset()
    denied_packages: frozenset[str] = frozenset()
    max_dependencies: int = 0


@dataclass(frozen=True, slots=True)
class RuntimePluginSpec:
    id: str
    version: str
    languages: frozenset[str]
    source_markers: tuple[str, ...]
    operations: tuple[str, ...]
    dependency_policy: DependencyPolicy
    network_policy: NetworkPolicy
    resource_policy: ResourcePolicy
    supports_preview: bool
    supports_deploy: bool
    service_binding_modes: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class RuntimeMatch:
    matched: bool
    score: int = 0
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RuntimeValidation:
    valid: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class RuntimePlugin(Protocol):
    spec: RuntimePluginSpec

    def detect(self, source: Any) -> RuntimeMatch: ...

    def validate(self, source: Any) -> RuntimeValidation: ...

    def build_submission(
        self,
        project: Any,
        source: Any,
        bindings: tuple[Any, ...],
    ) -> Any: ...
