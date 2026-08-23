"""Dependency-light protocol shared by Operly's control plane and isolated runners."""

from .contracts import (
    BuildSubmission,
    Dependency,
    HealthCheck,
    NetworkPolicy,
    ResourcePolicy,
    RunnerEventContract,
    RunnerJobContract,
    RunnerResult,
    ServiceBindingRequest,
)
from .source_bundles import (
    BundlePolicyError,
    SourceBundle,
    SourceFile,
    build_bundle,
    normalized_path,
)

__all__ = [
    "BuildSubmission",
    "Dependency",
    "HealthCheck",
    "NetworkPolicy",
    "ResourcePolicy",
    "RunnerEventContract",
    "RunnerJobContract",
    "RunnerResult",
    "ServiceBindingRequest",
    "BundlePolicyError",
    "SourceBundle",
    "SourceFile",
    "build_bundle",
    "normalized_path",
]
