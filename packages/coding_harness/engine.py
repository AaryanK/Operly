"""Coding-harness IR construction with model-driven runtime capability resolution."""
from __future__ import annotations

import re
from uuid import uuid4

from .contracts import *
from .model_resolution import CapabilityResolution, ModelCapabilityResolver


def _legacy_features(prompt: str) -> set[str]:
    """Compatibility-only deterministic resolver used by direct foundation tests.

    Runtime API requests use ``build_harness_plan_with_model`` instead.
    """
    text = prompt.lower()
    checks = {"auth": ("user", "login", "role", "manager"), "files": ("upload", "file", "document", "detector"),
              "realtime": ("realtime", "collaborative", "live"), "analysis": ("python", "analysis", "scientific"),
              "comments": ("comment", "review"), "publish": ("publish", "approved"), "search": ("search",),
              "payments": ("payment", "checkout", "subscription"), "existing_repo": ("existing repository", "repair", "bug")}
    return {key for key, words in checks.items() if any(word in text for word in words)}


def requirements(
    prompt: str,
    *,
    feature_ids: set[str] | None = None,
    unknown_requirements=(),
) -> RequirementGraph:
    model_resolved = feature_ids is not None
    features = set(feature_ids) if feature_ids is not None else _legacy_features(prompt)
    mapping = {
        "auth": ("Enforce authenticated role-based access", ["Unauthorized access is rejected", "Authorized roles can complete their workflows"]),
        "files": ("Accept and securely process user files", ["Allowed files can be uploaded", "Unsafe or oversized files are rejected"]),
        "realtime": ("Synchronize collaborative state", ["Two clients observe updates without refresh", "Conflicts have deterministic behavior"]),
        "analysis": ("Run isolated analysis workloads", ["Analysis produces a versioned result", "Workloads cannot access the OPERLY process"]),
        "comments": ("Support contextual discussion", ["Users can create and retrieve comments on a result"]),
        "publish": ("Gate publication on approval", ["Unapproved findings cannot be published", "Approval is audited"]),
        "search": ("Provide relevant search", ["Search returns authorized matching records"]),
        "payments": ("Process payments without storing raw card data", ["Payment state is verified by signed provider events"]),
        "existing_repo": ("Repair the existing repository without unrelated regressions", ["The reported defect test passes", "Existing tests remain passing"]),
    }

    rows = [
        Requirement(
            id="REQ-001",
            description="Deliver the primary user workflow",
            source="user_prompt",
            acceptanceCriteria=["The described critical path completes end-to-end"],
            risk="medium",
        )
    ]
    for feature in sorted(features):
        if feature not in mapping:
            continue
        description, acceptance = mapping[feature]
        rows.append(
            Requirement(
                id=f"REQ-{len(rows)+1:03}",
                description=description,
                source="model_capability_resolution" if model_resolved else "user_prompt",
                acceptanceCriteria=acceptance,
                risk="high" if any(w in description.lower() for w in ("access", "file", "isolated", "payment")) else "medium",
            )
        )

    for unknown in unknown_requirements:
        description = " ".join(str(unknown.description).split()).strip()
        if not description:
            continue
        rows.append(
            Requirement(
                id=f"REQ-{len(rows)+1:03}",
                description=description,
                source="model_unmatched_requirement",
                acceptanceCriteria=[f"The requested behavior is implemented and verified end-to-end: {description}"],
                risk="medium",
            )
        )

    return RequirementGraph(objective=re.sub(r"\s+", " ", prompt).strip(), requirements=rows)


def capabilities(graph: RequirementGraph) -> CapabilityGraph:
    caps=[]
    for req in graph.requirements:
        text=req.description.lower()
        if req.source == "model_unmatched_requirement":
            category="engineering"
            implementations=[]
        else:
            category="engineering" if "repository" in text else "data" if "file" in text else "backend" if any(x in text for x in ("access","analysis","payment","publication")) else "interface"
            implementations=[]
            if "analysis" in text: implementations=["isolated Python worker", "container job"]
            elif "access" in text: implementations=["OIDC", "session authentication with policy checks"]
            elif "file" in text: implementations=["object storage with signed uploads"]
        slug=re.sub(r"[^a-z0-9]+","-",req.description.lower()).strip("-")[:32] or "requirement"
        caps.append(Capability(id=f"CAP-{slug}", category=category, purpose=req.description, requirementIds=[req.id], knownImplementations=implementations))
    return CapabilityGraph(capabilities=caps)


def tool_registry() -> ToolRegistry:
    def tool(id,purpose,risk,side,network=[],approval=False):
        return ToolDefinition(id=id,purpose=purpose,inputSchema={"type":"object"},outputSchema={"type":"object"},permissions=[id],networkAccess=network,filesystemAccess=["project-workspace"] if id in {"filesystem","terminal","code-search"} else [],sideEffects=side,riskLevel=risk,timeoutSeconds=900 if id=="terminal" else 120,resourceLimits={"maxOutputKb":1024},approvalRequired=approval,rollbackBehavior="workspace snapshot and git revision",auditBehavior="record inputs, outputs, actor, duration, and result")
    return ToolRegistry(tools=[tool("filesystem","Read and edit project files","medium",["writes files"]),tool("code-search","Search project source","low",[]),tool("terminal","Run allowlisted commands in isolated runner","high",["starts isolated processes"]),tool("browser","Inspect preview DOM, console and network","medium",["interacts with preview"]),tool("deployment","Create controlled preview or release","critical",["changes external runtime"],[],True)])


def architectures(
    req: RequirementGraph,
    cap: CapabilityGraph,
    *,
    feature_ids: set[str] | None = None,
) -> ArchitecturePlan:
    features=set(feature_ids) if feature_ids is not None else _legacy_features(req.objective)
    ids=[x.id for x in cap.capabilities]
    specialized=bool(features & {"analysis","realtime","files"})
    primary=ArchitectureCandidate(id="candidate-modular",frontend="React + TypeScript",backend="FastAPI + Python",database="PostgreSQL",objectStorage="S3-compatible storage" if "files" in features else None,queue="isolated worker queue" if "analysis" in features else None,realtime="WebSocket collaboration service" if "realtime" in features else None,apiStyle="versioned JSON HTTP API",authentication="OIDC-compatible sessions",authorization="server-enforced RBAC and resource policies",hostingModel="separate web, API, database, and isolated runner boundaries",testingStrategy=["unit","integration","browser acceptance","security isolation"],observability=["structured logs","health checks","trace IDs"],securityControls=["deny-by-default network","tenant scoping","secret references","CSP"],estimatedCost="medium" if specialized else "low",complexity="medium" if specialized else "low",risks=["Realtime conflict semantics require explicit acceptance tests"] if "realtime" in features else [],rationale="A modular monolith keeps operational cost proportional while preserving an isolated workload boundary.",capabilityCoverage=ids,score=.9 if specialized else .88)
    alternative=primary.model_copy(update={"id":"candidate-typescript","backend":"NestJS + TypeScript","rationale":"A single-language alternative reduces context switching but is less direct for Python analysis workloads.","score":.76 if "analysis" in features else .86})
    chosen=max((primary,alternative),key=lambda x:x.score)
    return ArchitecturePlan(candidates=[primary,alternative],recommendedCandidateId=chosen.id,recommendationRationale=chosen.rationale)


def _build_from_resolution(prompt: str, resolution: CapabilityResolution | None = None) -> dict:
    feature_ids=set(resolution.known_feature_ids) if resolution is not None else None
    unknown_requirements=resolution.unknown_requirements if resolution is not None else ()
    req=requirements(prompt,feature_ids=feature_ids,unknown_requirements=unknown_requirements)
    cap=capabilities(req);tools=tool_registry();arch=architectures(req,cap,feature_ids=feature_ids)
    steps=[PlanStep(id=f"STEP-{i:03}",description=f"Implement and verify {r.description.lower()}",requirementIds=[r.id],capabilityIds=[cap.capabilities[i-1].id],toolIds=["filesystem","terminal","browser"],acceptanceChecks=r.acceptanceCriteria) for i,r in enumerate(req.requirements,1)]
    plan=ImplementationPlan(id=str(uuid4()),version=1,architectureCandidateId=arch.recommendedCandidateId,steps=steps,testPlan=["Run unit, integration, browser acceptance, and isolation suites"],deploymentPlan=["Build immutable artifact","Deploy preview","Require approval","Run health verification","Rollback on failure"])
    result={"requirementGraph":req.model_dump(mode="json"),"capabilityGraph":cap.model_dump(mode="json"),"toolRegistry":tools.model_dump(mode="json"),"architecturePlan":arch.model_dump(mode="json"),"implementationPlan":plan.model_dump(mode="json")}
    if resolution is not None:
        result["knowledgeResolution"]={
            "authority":"model",
            "knownCapabilityIds":list(resolution.known_feature_ids),
            "unknownRequirements":[{"description":item.description,"reason":item.reason} for item in resolution.unknown_requirements],
            "reason":resolution.reason,
        }
    return result


def build_harness_plan(prompt: str) -> dict:
    """Compatibility/test planner. Runtime requests must use the model-backed path."""
    return _build_from_resolution(prompt)


async def build_harness_plan_with_model(prompt: str, client=None) -> dict:
    resolution=await ModelCapabilityResolver(client).resolve(prompt)
    return _build_from_resolution(prompt,resolution)
