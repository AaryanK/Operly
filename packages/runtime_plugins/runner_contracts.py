"""Typed contracts shared by the OPERLY control plane and isolated runners."""
from __future__ import annotations

import ipaddress
import re
from datetime import datetime
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_PYTHON_PACKAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
_NPM_PACKAGE = re.compile(
    r"^(?:@[A-Za-z0-9][A-Za-z0-9_.-]{0,49}/)?[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$"
)
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9*_.+!<>=~^|-]{0,79}$")
_CAPABILITY = re.compile(r"^[a-z][a-z0-9_.:-]{1,159}$")
_SEMANTIC_NAME = re.compile(r"^[a-z][a-z0-9_.-]{1,79}$")
_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$"
)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Dependency(Strict):
    ecosystem: Literal["python", "npm"] = "python"
    name: str
    version: str
    registry: Literal["pypi", "npm"] | None = None

    @model_validator(mode="before")
    @classmethod
    def default_registry(cls, value):
        if isinstance(value, dict) and value.get("registry") is None:
            value = dict(value)
            value["registry"] = "npm" if value.get("ecosystem") == "npm" else "pypi"
        return value

    @model_validator(mode="after")
    def validate_dependency(self):
        pattern = _NPM_PACKAGE if self.ecosystem == "npm" else _PYTHON_PACKAGE
        if not pattern.fullmatch(self.name) or ".." in self.name:
            raise ValueError("Dependency name is not a safe registry package name")
        if not _VERSION.fullmatch(self.version):
            raise ValueError("Dependency version must be a bounded registry version/range")
        expected_registry = "npm" if self.ecosystem == "npm" else "pypi"
        if self.registry != expected_registry:
            raise ValueError(f"{self.ecosystem} dependencies must use the {expected_registry} registry")
        return self


class ServiceBindingTransport(Strict):
    """Runner-only material used to create a credential-hiding sidecar.

    This object must never be persisted in generated source or the durable build
    submission record. The generated runtime receives only a local endpoint.
    """

    gatewayUrl: str = Field(min_length=8, max_length=1000)
    runtimeToken: str = Field(min_length=40, max_length=4096)
    migrationToken: str | None = Field(default=None, min_length=40, max_length=4096)

    @field_validator("gatewayUrl")
    @classmethod
    def gateway_url(cls, value: str) -> str:
        parsed = urlparse(value.strip())
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Service binding gateway URL is invalid")
        return value.rstrip("/")


class ServiceBindingRequest(Strict):
    semanticName: str
    capabilityId: str
    required: bool = True
    transport: ServiceBindingTransport | None = None

    @field_validator("semanticName")
    @classmethod
    def semantic_name(cls, value: str) -> str:
        if not _SEMANTIC_NAME.fullmatch(value):
            raise ValueError("Service binding semanticName is invalid")
        return value

    @field_validator("capabilityId")
    @classmethod
    def capability_id(cls, value: str) -> str:
        if not _CAPABILITY.fullmatch(value):
            raise ValueError("Service binding capabilityId is invalid")
        return value


class ResourcePolicy(Strict):
    cpu: float = Field(default=1, gt=0, le=4)
    memoryMb: int = Field(default=512, ge=128, le=4096)
    processes: int = Field(default=32, ge=1, le=128)
    openFiles: int = Field(default=256, ge=32, le=2048)
    diskMb: int = Field(default=256, ge=32, le=2048)
    durationSeconds: int = Field(default=300, ge=10, le=1800)
    idleSeconds: int = Field(default=60, ge=5, le=300)
    logBytes: int = Field(default=1_000_000, ge=1024, le=10_000_000)
    artifactBytes: int = Field(default=10_000_000, ge=1024, le=100_000_000)
    previewSeconds: int = Field(default=1800, ge=60, le=14400)


class NetworkPolicy(Strict):
    mode: Literal[
        "none",
        "loopback_only",
        "dependency_registry_only",
        "approved_hosts",
        "sandbox_integrations",
    ] = "none"
    approvedHosts: list[str] = Field(default_factory=list)

    @field_validator("approvedHosts")
    @classmethod
    def safe_hosts(cls, hosts):
        normalized: list[str] = []
        for raw in hosts:
            host = str(raw or "").strip().lower().rstrip(".")
            if not host:
                raise ValueError("Approved network hosts cannot be empty")
            if any(token in host for token in ("/", "@", "?", "#", " ", "\t", "\n")):
                raise ValueError("Approved network entries must be hostnames or IP addresses, not URLs")
            if host in {"localhost", "metadata.google.internal"} or host.endswith(".localhost"):
                raise ValueError("Private and metadata hosts are forbidden")
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                if not _HOSTNAME.fullmatch(host):
                    raise ValueError("Approved network host is not a valid hostname or IP address")
            else:
                if any(
                    (
                        address.is_private,
                        address.is_loopback,
                        address.is_link_local,
                        address.is_reserved,
                        address.is_multicast,
                        address.is_unspecified,
                    )
                ):
                    raise ValueError("Private, local, reserved, and metadata addresses are forbidden")
            normalized.append(host)
        if len(normalized) != len(set(normalized)):
            raise ValueError("Approved network hosts must be unique")
        return normalized


class HealthCheck(Strict):
    path: str = Field(default="/health", pattern=r"^/[A-Za-z0-9_./-]*$")
    expectedStatus: int = 200
    bodyMarker: str | None = None
    timeoutSeconds: int = 30


class BuildSubmission(Strict):
    workspaceId: str
    applicationId: str
    planVersion: int = Field(ge=1)
    sourceVersion: int = Field(ge=1)
    stackId: str
    stackVersion: int = Field(default=1, ge=1, le=20)
    sourceBundleDigest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    dependencies: list[Dependency] = Field(default_factory=list, max_length=100)
    operations: list[
        Literal[
            "stage_source",
            "resolve_dependencies",
            "static_analysis",
            "build",
            "test",
            "start",
            "health_check",
            "acceptance_test",
        ]
    ]
    healthCheck: HealthCheck
    resources: ResourcePolicy = Field(default_factory=ResourcePolicy)
    installNetwork: NetworkPolicy = Field(default_factory=NetworkPolicy)
    network: NetworkPolicy = Field(default_factory=NetworkPolicy)
    serviceBindings: list[ServiceBindingRequest] = Field(default_factory=list, max_length=100)
    secretAliases: list[str] = Field(default_factory=list, max_length=20)
    requiredPorts: list[int] = Field(default_factory=lambda: [8080], max_length=4)
    artifactPaths: list[str] = Field(default_factory=lambda: ["artifacts"], max_length=20)
    maxDurationSeconds: int = Field(default=300, ge=10, le=1800)
    idempotencyKey: str = Field(min_length=8, max_length=120)

    @model_validator(mode="after")
    def validate_submission(self):
        if any(port < 1024 or port > 65535 for port in self.requiredPorts):
            raise ValueError("Only unprivileged ports are permitted")
        if len(set(self.requiredPorts)) != len(self.requiredPorts):
            raise ValueError("Ports must be unique")

        dependency_keys = [(item.ecosystem, item.name.lower()) for item in self.dependencies]
        if len(dependency_keys) != len(set(dependency_keys)):
            raise ValueError("Dependencies must be unique by ecosystem and package")
        if self.dependencies:
            if "resolve_dependencies" not in self.operations:
                raise ValueError("Declared dependencies require the resolve_dependencies operation")
            if self.installNetwork.mode not in {"dependency_registry_only", "approved_hosts"}:
                raise ValueError("Dependency installation must use a registry-bounded network policy")
        elif self.installNetwork.mode != "none":
            raise ValueError("Install network access is forbidden when no dependencies are declared")

        semantic_names = [binding.semanticName for binding in self.serviceBindings]
        if len(semantic_names) != len(set(semantic_names)):
            raise ValueError("Service binding semanticName values must be unique")
        return self


class RunnerEventContract(Strict):
    sequence: int = Field(ge=1)
    timestamp: datetime
    eventType: str
    state: str
    message: str = Field(max_length=4000)
    commandLabel: str | None = None
    exitCode: int | None = None
    artifactReference: str | None = None
    logReference: str | None = None
    securityEvent: bool = False
    resourceEvent: bool = False


class RunnerResult(Strict):
    buildSuccess: bool = False
    testSuccess: bool = False
    processStartSuccess: bool = False
    healthCheckSuccess: bool = False
    acceptanceCheckSuccess: bool = False
    previewAvailable: bool = False
    artifacts: list[dict] = Field(default_factory=list)
    testReport: dict = Field(default_factory=dict)
    staticAnalysisReport: dict = Field(default_factory=dict)
    dependencyReport: dict = Field(default_factory=dict)
    resourceUsage: dict = Field(default_factory=dict)
    failureEvidence: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def preview_truth(self):
        if self.previewAvailable and not all(
            (
                self.buildSuccess,
                self.testSuccess,
                self.processStartSuccess,
                self.healthCheckSuccess,
                self.acceptanceCheckSuccess,
            )
        ):
            raise ValueError("Preview requires every execution quality gate")
        return self


class RunnerJobContract(Strict):
    jobId: str
    jobType: Literal["build", "repair", "cleanup"]
    state: str
    createdAt: datetime
    startedAt: datetime | None = None
    completedAt: datetime | None = None
    sourceDigest: str
    runnerImplementation: str
    isolationProfile: str
    resources: ResourcePolicy
    attempt: int = 1
    parentRepairAttempt: str | None = None
    failureClassification: str | None = None
    exitInformation: dict = Field(default_factory=dict)
