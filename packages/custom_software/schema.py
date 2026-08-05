"""Strict, non-executable contracts for architecture-first software generation."""
from typing import Literal, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


Architecture = str
ImplementationMode = Literal["managed_runtime","architecture_pack","sandbox_generated","hybrid"]


class RolePlan(Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: str
    description: str
    permissions: list[str]
    access: Literal["public","authenticated"]
    dataScope: Literal["own","assigned","tenant","all"]


class FieldPlan(Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    type: Literal["string","text","integer","decimal","boolean","date","datetime","email","phone","enum","json"]
    required: bool = False
    sensitive: bool = False
    options: list[str] = []


class RelationshipPlan(Strict):
    id: str
    sourceEntity: str
    targetEntity: str
    cardinality: Literal["one_to_one","one_to_many","many_to_many","belongs_to"]
    implementationSupport: Literal["managed_runtime","architecture_pack","sandbox_required"]


class EntityPlan(Strict):
    id: str
    name: str
    purpose: str
    fields: list[FieldPlan]
    relationshipIds: list[str] = []
    ownership: str
    visibility: list[str]
    lifecycle: list[str] = []


class TransitionPlan(Strict):
    id: str
    fromState: str
    toState: str
    actors: list[str]
    guards: list[str] = []
    sideEffects: list[str] = []
    approvalRequired: bool = False


class WorkflowPlan(Strict):
    id: str
    name: str
    trigger: str
    states: list[str]
    transitions: list[TransitionPlan]
    failureBehavior: str

    @model_validator(mode="after")
    def transitions_reference_states(self):
        states=set(self.states)
        if any(t.fromState not in states or t.toState not in states for t in self.transitions):
            raise ValueError("Workflow transitions must reference declared states")
        return self


class SurfacePlan(Strict):
    id: str
    name: str
    route: str
    audience: list[str]
    purpose: str
    majorComponents: list[str]
    relatedEntities: list[str] = []
    relatedWorkflows: list[str] = []
    access: Literal["public","authenticated"]


class DesignPlan(Strict):
    family: Literal["editorial","utility","dashboard_led","conversion_focused","image_led","minimal","modular_grid","asymmetric"]
    visualPersonality: str
    navigationFamily: str
    heroFamily: str
    typographyPairing: str
    typeScale: str
    contentDensity: str
    spacingSystem: str
    gridSystem: str
    surfaceStyle: str
    cardStyle: str
    ctaStrategy: str
    mediaStrategy: str
    motionStrategy: str
    responsiveBehavior: str
    accessibilityGoals: list[str]


class RuntimePlan(Strict):
    strategy: ImplementationMode
    reason: str
    primaryPack: str | None = None
    secondaryPacks: list[str] = []


class CapabilityPlan(Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    category: str
    description: str
    requirement: str
    implementation: Literal["reuse_primitive","generate_component","generate_engine","integration_adapter"]
    status: Literal["planned","implemented","verified","blocked"] = "planned"


class ArchitectureNode(Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    nodeType: str
    name: str
    inputs: list[str] = []
    outputs: list[str] = []
    invariants: list[str] = []
    implementationRequired: bool = True


class StackDecision(Strict):
    frontend: str
    backend: str
    database: str
    runtime: str
    reasons: list[str]
    dependencies: list[str] = []


class RequirementEvidence(Strict):
    requirementId: str
    requirement: str
    artifactIds: list[str]
    testIds: list[str]
    status: Literal["planned","implemented","verified","partial","failed","blocked"] = "planned"


class RequirementLedgerItem(Strict):
    id: str
    originalSource: str
    exactText: str
    normalizedMeaning: str
    mandatory: bool = True
    category: str
    acceptanceCriteria: list[str]
    relatedPlanNodeIds: list[str] = []
    relatedArtifactIds: list[str] = []
    relatedTestIds: list[str] = []
    coverageStatus: Literal["unplanned","planned","partially_planned","implementation_ready","implemented","tested","verified","conflicted","waived_by_user"] = "unplanned"
    verificationStatus: str = "unverified"
    planningMode: Literal["live_llm","deterministic_test","unavailable"] = "deterministic_test"
    explicitTerms: list[str] = []
    exclusions: list[str] = []
    ambiguities: list[str] = []
    conflicts: list[str] = []
    assumptions: list[str] = []


class PlanNodeValidation(Strict):
    readyForImplementation: bool
    missingInformation: list[str] = []
    ambiguousBehavior: list[str] = []
    missingInputs: list[str] = []
    missingOutputs: list[str] = []
    missingInvariants: list[str] = []
    missingDependencies: list[str] = []
    missingFailureHandling: list[str] = []
    missingSecurityRules: list[str] = []
    missingPersistenceBehavior: list[str] = []
    missingTests: list[str] = []
    conflicts: list[str] = []
    recommendedDecompositionAreas: list[str] = []


class RecursivePlanNode(Strict):
    id: str
    parentId: str | None = None
    originalRequirementIds: list[str]
    title: str
    objective: str
    description: str
    nodeType: str
    inputs: list[str]
    outputs: list[str]
    dependencies: list[str]
    constraints: list[str]
    securityRequirements: list[str]
    failureCases: list[str]
    acceptanceCriteria: list[str]
    requiredArtifacts: list[str]
    requiredTests: list[str]
    status: Literal["created","planning","awaiting_validation","validation_failed","decomposition_required","implementation_ready","blocked","generating","generated","testing","test_failed","repairing","verified","integrated","completed"]
    validation: PlanNodeValidation
    implementationEvidence: list[str] = []
    childIds: list[str] = []
    version: int = 1
    provenance: dict = {}
    planningMode: Literal["live_llm","deterministic_test","unavailable"] = "deterministic_test"
    responsibilities: list[str] = []
    stateEffects: list[str] = []
    invariants: list[str] = []
    persistenceBehavior: list[str] = []
    refinementCount: int = 0


class PlanningMetrics(Strict):
    mandatoryRequirementsMapped: int
    mandatoryRequirementsTotal: int
    planNodesReady: int
    planNodesTotal: int
    executableTestsMapped: int
    unresolvedValidatorFindings: int
    dependencyComplete: bool
    globalValidationPassed: bool
    approvalBlockedReasons: list[str] = []
    planningMode: Literal["live_llm","deterministic_test","unavailable"] = "deterministic_test"
    planningCallsUsed: int = 0
    inputTokensUsed: int = 0
    outputTokensUsed: int = 0
    blockedNodes: int = 0
    nodesRequiringDecomposition: int = 0
    testSpecificationCoverage: int = 0


class SemanticPlanDiff(Strict):
    addedRequirementIds: list[str] = []
    modifiedRequirementIds: list[str] = []
    removedRequirementIds: list[str] = []
    addedNodeIds: list[str] = []
    invalidatedNodeIds: list[str] = []
    preservedNodeIds: list[str] = []
    structuralChange: bool


class SoftwarePlan(Strict):
    schemaVersion: Literal[1] = 1
    projectName: str
    summary: str
    productCategory: str
    targetUsers: list[str]
    businessDomain: str
    primaryGoal: str
    successCriteria: list[str]
    primaryArchitecture: Architecture
    secondaryArchitectures: list[Architecture] = []
    implementationMode: ImplementationMode
    confidence: float = Field(ge=0, le=1)
    rationale: str
    roles: list[RolePlan]
    entities: list[EntityPlan]
    relationships: list[RelationshipPlan]
    workflows: list[WorkflowPlan]
    surfaces: list[SurfacePlan]
    backendCapabilities: list[str]
    integrations: list[str] = []
    design: DesignPlan
    runtime: RuntimePlan
    securityConstraints: list[str]
    unsupportedRequirements: list[str] = []
    risks: list[str] = []
    testRequirements: list[str]
    deploymentRequirements: list[str]
    effectiveRequirements: list[str] = []
    capabilities: list[CapabilityPlan] = []
    architectureNodes: list[ArchitectureNode] = []
    stack: StackDecision | None = None
    requirementEvidence: list[RequirementEvidence] = []
    reusedPrimitives: list[str] = []
    generatedComponents: list[str] = []
    provenance: dict = {}
    requirementLedger: list[RequirementLedgerItem] = []
    planTree: list[RecursivePlanNode] = []
    planningMetrics: PlanningMetrics | None = None
    semanticDiff: SemanticPlanDiff | None = None
    globalValidation: dict = {}
    planningMode: Literal["live_llm","deterministic_test","unavailable"] = "deterministic_test"
    planningBudget: dict[str, Any] = {}

    @model_validator(mode="after")
    def graph_integrity(self):
        def unique(rows, label):
            ids=[x.id for x in rows]
            if len(ids)!=len(set(ids)): raise ValueError(f"Duplicate {label} IDs")
            return set(ids)
        roles=unique(self.roles,"role");entities=unique(self.entities,"entity");relationships=unique(self.relationships,"relationship");workflows=unique(self.workflows,"workflow");unique(self.surfaces,"surface")
        for rel in self.relationships:
            if rel.sourceEntity not in entities or rel.targetEntity not in entities: raise ValueError("Relationship references unknown entity")
        for entity in self.entities:
            if not set(entity.relationshipIds)<=relationships or not set(entity.visibility)<=roles: raise ValueError("Entity references unknown relationship or role")
        for workflow in self.workflows:
            for transition in workflow.transitions:
                if not set(transition.actors)<=roles: raise ValueError("Workflow transition references unknown role")
        for surface in self.surfaces:
            if not set(surface.audience)<=roles or not set(surface.relatedEntities)<=entities or not set(surface.relatedWorkflows)<=workflows: raise ValueError("Surface references unknown plan item")
        forbidden=("<script","javascript:","import os","subprocess","eval(","exec(")
        if any(token in self.model_dump_json().lower() for token in forbidden): raise ValueError("Executable content is not allowed in a SoftwarePlan")
        return self


class PlanRequestInput(Strict):
    prompt: str = Field(min_length=20, max_length=8000)


class PlanRevisionInput(Strict):
    request: str = Field(min_length=3, max_length=2000)
    expectedVersion: int = Field(ge=1)


class PlanApprovalInput(Strict):
    expectedVersion: int = Field(ge=1)


class GenerateApprovedPlanInput(Strict):
    planId: str
    approvedVersion: int = Field(ge=1)


class GenerateProjectInput(Strict):
    prompt: str = Field(min_length=20, max_length=4000)


class VisualChangeInput(Strict):
    request: str = Field(min_length=3, max_length=2000)
    selected_artifact_ids: list[str] = Field(min_length=1, max_length=20)
    viewport: str = Field(default="desktop", pattern="^(desktop|tablet|mobile)$")


class AgenticProjectInput(Strict):
    prompt: str = Field(min_length=20, max_length=8000)

class RunnerBuildInput(Strict):
    planId: str
    approvedVersion: int = Field(ge=1)
    idempotencyKey: str = Field(min_length=8,max_length=120)

class RunnerRepairInput(Strict):
    idempotencyKey: str = Field(min_length=8,max_length=120)


class ServiceRequestInput(Strict):
    name: str = Field(min_length=2, max_length=160)
    phone: str = Field(min_length=7, max_length=40)
    email: str | None = Field(default=None, max_length=320)
    issue_category: str = Field(min_length=2, max_length=80)
    description: str = Field(default="", max_length=2000)
    address: str = Field(min_length=5, max_length=500)
    asset_details: str = Field(default="", max_length=500)
    idempotency_key: str = Field(min_length=8, max_length=120)

    @field_validator("name", "phone", "issue_category", "address")
    @classmethod
    def not_blank(cls, value: str) -> str:
        value=value.strip()
        if not value: raise ValueError("Value cannot be blank")
        return value

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str | None) -> str | None:
        if not value:return None
        value=value.strip().lower()
        if "@" not in value or value.startswith("@") or value.endswith("@"):raise ValueError("Enter a valid email address")
        return value


class TransitionInput(Strict):
    status: str
    assigned_to: str | None = Field(default=None, max_length=160)
    note: str = Field(default="", max_length=500)
    expected_version: int = Field(ge=1)
