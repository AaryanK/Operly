"""Strict intermediate representations. These contain data, never executable code."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Requirement(Contract):
    id: str = Field(pattern=r"^REQ-[0-9]{3,}$")
    description: str
    priority: Literal["must", "should", "could"] = "must"
    risk: Literal["low", "medium", "high", "critical"] = "medium"
    source: str
    acceptanceCriteria: list[str] = Field(min_length=1)
    dependencies: list[str] = []
    implementationStatus: Literal["unplanned", "planned", "implemented", "blocked"] = "unplanned"
    testStatus: Literal["untested", "planned", "passing", "failing", "blocked"] = "untested"


class RequirementGraph(Contract):
    schemaVersion: Literal[1] = 1
    objective: str
    requirements: list[Requirement]
    unansweredQuestions: list[str] = []

    @model_validator(mode="after")
    def integrity(self):
        ids = [x.id for x in self.requirements]
        if len(ids) != len(set(ids)):
            raise ValueError("Requirement IDs must be unique")
        known = set(ids)
        if any(not set(x.dependencies) <= known for x in self.requirements):
            raise ValueError("Requirement dependency is unknown")
        return self


class Capability(Contract):
    id: str = Field(pattern=r"^CAP-[a-z0-9-]+$")
    category: Literal["data", "interface", "backend", "engineering"]
    purpose: str
    requirementIds: list[str]
    dependencies: list[str] = []
    compatibilityConstraints: list[str] = []
    risks: list[str] = []
    costClass: Literal["low", "medium", "high"] = "low"
    knownImplementations: list[str] = []


class CapabilityGraph(Contract):
    schemaVersion: Literal[1] = 1
    capabilities: list[Capability]
    edges: list[tuple[str, str]] = []

    @model_validator(mode="after")
    def integrity(self):
        ids = [x.id for x in self.capabilities]
        if len(ids) != len(set(ids)):
            raise ValueError("Capability IDs must be unique")
        known = set(ids)
        if any(a not in known or b not in known for a, b in self.edges):
            raise ValueError("Capability edge is unknown")
        return self


class ToolDefinition(Contract):
    id: str
    purpose: str
    inputSchema: dict[str, Any]
    outputSchema: dict[str, Any]
    permissions: list[str]
    networkAccess: list[str] = []
    filesystemAccess: list[str] = []
    sideEffects: list[str] = []
    riskLevel: Literal["low", "medium", "high", "critical"]
    costClass: Literal["free", "metered", "external"] = "free"
    timeoutSeconds: int = Field(gt=0, le=3600)
    resourceLimits: dict[str, int] = {}
    approvalRequired: bool = False
    rollbackBehavior: str
    auditBehavior: str


class ToolRegistry(Contract):
    schemaVersion: Literal[1] = 1
    tools: list[ToolDefinition]

    @model_validator(mode="after")
    def unique_tools(self):
        if len({x.id for x in self.tools}) != len(self.tools):
            raise ValueError("Tool IDs must be unique")
        return self


class ArchitectureCandidate(Contract):
    id: str
    frontend: str
    backend: str
    database: str
    objectStorage: str | None = None
    cache: str | None = None
    queue: str | None = None
    realtime: str | None = None
    apiStyle: str
    authentication: str
    authorization: str
    hostingModel: str
    testingStrategy: list[str]
    observability: list[str]
    securityControls: list[str]
    estimatedCost: Literal["low", "medium", "high"]
    complexity: Literal["low", "medium", "high"]
    risks: list[str]
    rationale: str
    capabilityCoverage: list[str]
    score: float = Field(ge=0, le=1)
    acceleratorPacks: list[str] = []


class ArchitecturePlan(Contract):
    schemaVersion: Literal[1] = 1
    candidates: list[ArchitectureCandidate] = Field(min_length=1)
    recommendedCandidateId: str
    recommendationRationale: str

    @model_validator(mode="after")
    def recommendation_exists(self):
        if self.recommendedCandidateId not in {x.id for x in self.candidates}:
            raise ValueError("Recommended architecture candidate is unknown")
        return self


class PlanStep(Contract):
    id: str
    description: str
    requirementIds: list[str]
    capabilityIds: list[str]
    toolIds: list[str]
    acceptanceChecks: list[str]


class ImplementationPlan(Contract):
    schemaVersion: Literal[1] = 1
    id: str
    version: int = Field(ge=1)
    status: Literal["draft", "approved", "rejected", "superseded"] = "draft"
    architectureCandidateId: str
    steps: list[PlanStep]
    testPlan: list[str]
    deploymentPlan: list[str]
    limitations: list[str] = []
    approvedBy: str | None = None
    approvedAt: datetime | None = None


class RunnerJob(Contract):
    schemaVersion: Literal[1] = 1
    id: str
    projectId: str
    planId: str
    planVersion: int
    sourceRevision: str | None = None
    workspace: Literal["ephemeral-isolated"] = "ephemeral-isolated"
    commands: list[list[str]]
    environmentKeys: list[str] = []
    secretRefs: list[str] = []
    networkAllowlist: list[str] = []
    cpuLimit: int = Field(gt=0, le=8)
    memoryMb: int = Field(ge=128, le=16384)
    diskMb: int = Field(ge=128, le=65536)
    timeoutSeconds: int = Field(gt=0, le=7200)
    executeInsideOperly: Literal[False] = False
    productionDeploymentAllowed: Literal[False] = False

    @model_validator(mode="after")
    def safe_commands(self):
        forbidden = {"rm", "format", "shutdown", "reboot", "powershell", "cmd"}
        if any(not cmd or cmd[0].lower() in forbidden for cmd in self.commands):
            raise ValueError("Runner command violates the job contract")
        return self


class ExecutionState(StrEnum):
    awaiting_approval = "awaiting_approval"
    queued = "queued"
    provisioning = "provisioning"
    implementing = "implementing"
    building = "building"
    testing = "testing"
    running = "running"
    inspecting = "inspecting"
    diagnosing = "diagnosing"
    repairing = "repairing"
    acceptance_passed = "acceptance_passed"
    blocked = "blocked"
    cancelled = "cancelled"
    exhausted = "exhausted"


class ExecutionTransition(Contract):
    fromState: ExecutionState
    toState: ExecutionState
    reason: str
    at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TestRepairRecord(Contract):
    iteration: int = Field(ge=1)
    planVersion: int
    sourceRevision: str
    filesChanged: list[str]
    commandsExecuted: list[list[str]]
    buildResult: Literal["passed", "failed", "not_run"]
    testResults: dict[str, Literal["passed", "failed", "skipped"]]
    diagnosis: str | None = None
    repairDecision: str | None = None
    remainingRequirementIds: list[str] = []
    tokenUsage: int = Field(ge=0)
    computeSeconds: float = Field(ge=0)


class BrowserObservation(Contract):
    iteration: int
    route: str
    viewport: str
    action: str
    domEvidence: list[str] = []
    consoleErrors: list[str] = []
    networkFailures: list[str] = []
    screenshotRef: str | None = None
    requirementIds: list[str] = []
    verdict: Literal["pass", "fail", "inconclusive"]


class ArtifactNode(Contract):
    id: str
    kind: Literal["visible", "page", "route", "component", "source", "style", "api", "entity", "field", "workflow", "permission", "test", "build", "deployment"]
    locator: str
    revision: str
    requirementIds: list[str]


class ArtifactEdge(Contract):
    source: str
    target: str
    relation: str


class ArtifactGraph(Contract):
    schemaVersion: Literal[2] = 2
    nodes: list[ArtifactNode]
    edges: list[ArtifactEdge]


class VisualEditImpact(Contract):
    selectedArtifactIds: list[str]
    request: str
    affectedArtifactIds: list[str]
    unaffectedArtifactIds: list[str]
    schemaImpact: list[str]
    workflowImpact: list[str]
    permissionImpact: list[str]
    migrationImpact: list[str]
    testImpact: list[str]
    risks: list[str]
    atomic: Literal[True] = True
    reversible: Literal[True] = True


class OutcomeMetrics(Contract):
    requirement: float = Field(ge=0, le=1)
    functional: float = Field(ge=0, le=1)
    build: float = Field(ge=0, le=1)
    test: float = Field(ge=0, le=1)
    security: float = Field(ge=0, le=1)
    architecture: float = Field(ge=0, le=1)
    visual: float = Field(ge=0, le=1)
    editability: float = Field(ge=0, le=1)
    traceability: float = Field(ge=0, le=1)
    regression: float = Field(ge=0, le=1)
    humanIntervention: float = Field(ge=0, le=1)
    efficiency: float = Field(ge=0, le=1)
    operability: float = Field(ge=0, le=1)
    criticalSecurityFailure: bool = False


class BenchmarkTask(Contract):
    schemaVersion: Literal[1] = 1
    id: str
    title: str
    kind: Literal["small_business", "specialized_custom", "repository_repair"]
    split: Literal["development", "held_out"]
    prompt: str
    repositoryRef: str | None = None
    constraints: list[str]
    acceptanceTests: list[str]
    securityTests: list[str]
    attempts: int = Field(default=1, ge=1, le=20)


class BaselineImport(Contract):
    schemaVersion: Literal[1] = 1
    taskId: str
    agent: str = "codex"
    agentVersion: str
    independentRunId: str
    sourceRevision: str
    metrics: OutcomeMetrics
    evidenceRefs: list[str]
    recordedAt: datetime


class ComparisonReport(Contract):
    taskId: str
    split: Literal["development", "held_out"]
    operlyLoss: float
    baselineLoss: float
    delta: float
    winner: Literal["operly", "baseline", "tie"]
    dimensions: dict[str, dict[str, float]]
    evidenceRefs: list[str]


class AggregateReport(Contract):
    development: dict[str, float]
    heldOut: dict[str, float]
    taskReports: list[ComparisonReport]
    parityClaimAllowed: bool
