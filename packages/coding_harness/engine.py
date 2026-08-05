"""Deterministic foundation planner. Model-produced IRs can use the same strict contracts."""
from __future__ import annotations

import re
from uuid import uuid4

from .contracts import *


def _features(prompt: str) -> set[str]:
    text = prompt.lower()
    checks = {"auth": ("user", "login", "role", "manager"), "files": ("upload", "file", "document", "detector"),
              "realtime": ("realtime", "collaborative", "live"), "analysis": ("python", "analysis", "scientific"),
              "comments": ("comment", "review"), "publish": ("publish", "approved"), "search": ("search",),
              "payments": ("payment", "checkout", "subscription"), "existing_repo": ("existing repository", "repair", "bug")}
    return {key for key, words in checks.items() if any(word in text for word in words)}


def requirements(prompt: str) -> RequirementGraph:
    features = _features(prompt)
    descriptions = [("Deliver the primary user workflow", ["The described critical path completes end-to-end"])]
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
    descriptions += [mapping[x] for x in sorted(features) if x in mapping]
    rows = [Requirement(id=f"REQ-{i:03}", description=d, source="user_prompt", acceptanceCriteria=a,
                        risk="high" if any(w in d.lower() for w in ("access", "file", "isolated", "payment")) else "medium")
            for i, (d, a) in enumerate(descriptions, 1)]
    return RequirementGraph(objective=re.sub(r"\s+", " ", prompt).strip(), requirements=rows)


def capabilities(graph: RequirementGraph) -> CapabilityGraph:
    caps=[]
    for req in graph.requirements:
        text=req.description.lower(); category="engineering" if "repository" in text else "data" if "file" in text else "backend" if any(x in text for x in ("access","analysis","payment","publication")) else "interface"
        slug=re.sub(r"[^a-z0-9]+","-",req.description.lower()).strip("-")[:32]
        implementations=[]
        if "analysis" in text: implementations=["isolated Python worker", "container job"]
        elif "access" in text: implementations=["OIDC", "session authentication with policy checks"]
        elif "file" in text: implementations=["object storage with signed uploads"]
        caps.append(Capability(id=f"CAP-{slug}", category=category, purpose=req.description, requirementIds=[req.id], knownImplementations=implementations))
    return CapabilityGraph(capabilities=caps)


def tool_registry() -> ToolRegistry:
    def tool(id,purpose,risk,side,network=[],approval=False):
        return ToolDefinition(id=id,purpose=purpose,inputSchema={"type":"object"},outputSchema={"type":"object"},permissions=[id],networkAccess=network,filesystemAccess=["project-workspace"] if id in {"filesystem","terminal","code-search"} else [],sideEffects=side,riskLevel=risk,timeoutSeconds=900 if id=="terminal" else 120,resourceLimits={"maxOutputKb":1024},approvalRequired=approval,rollbackBehavior="workspace snapshot and git revision",auditBehavior="record inputs, outputs, actor, duration, and result")
    return ToolRegistry(tools=[tool("filesystem","Read and edit project files","medium",["writes files"]),tool("code-search","Search project source","low",[]),tool("terminal","Run allowlisted commands in isolated runner","high",["starts isolated processes"]),tool("browser","Inspect preview DOM, console and network","medium",["interacts with preview"]),tool("deployment","Create controlled preview or release","critical",["changes external runtime"],[],True)])


def architectures(req: RequirementGraph, cap: CapabilityGraph) -> ArchitecturePlan:
    features=_features(req.objective); ids=[x.id for x in cap.capabilities]
    specialized=bool(features & {"analysis","realtime","files"})
    primary=ArchitectureCandidate(id="candidate-modular",frontend="React + TypeScript",backend="FastAPI + Python" if "analysis" in features else "FastAPI + Python",database="PostgreSQL",objectStorage="S3-compatible storage" if "files" in features else None,queue="isolated worker queue" if "analysis" in features else None,realtime="WebSocket collaboration service" if "realtime" in features else None,apiStyle="versioned JSON HTTP API",authentication="OIDC-compatible sessions",authorization="server-enforced RBAC and resource policies",hostingModel="separate web, API, database, and isolated runner boundaries",testingStrategy=["unit","integration","browser acceptance","security isolation"],observability=["structured logs","health checks","trace IDs"],securityControls=["deny-by-default network","tenant scoping","secret references","CSP"],estimatedCost="medium" if specialized else "low",complexity="medium" if specialized else "low",risks=["Realtime conflict semantics require explicit acceptance tests"] if "realtime" in features else [],rationale="A modular monolith keeps operational cost proportional while preserving an isolated workload boundary.",capabilityCoverage=ids,score=.9 if specialized else .88)
    alternative=primary.model_copy(update={"id":"candidate-typescript","backend":"NestJS + TypeScript","rationale":"A single-language alternative reduces context switching but is less direct for Python analysis workloads.","score":.76 if "analysis" in features else .86})
    chosen=max((primary,alternative),key=lambda x:x.score)
    return ArchitecturePlan(candidates=[primary,alternative],recommendedCandidateId=chosen.id,recommendationRationale=chosen.rationale)


def build_harness_plan(prompt: str) -> dict:
    req=requirements(prompt);cap=capabilities(req);tools=tool_registry();arch=architectures(req,cap)
    steps=[PlanStep(id=f"STEP-{i:03}",description=f"Implement and verify {r.description.lower()}",requirementIds=[r.id],capabilityIds=[cap.capabilities[i-1].id],toolIds=["filesystem","terminal","browser"],acceptanceChecks=r.acceptanceCriteria) for i,r in enumerate(req.requirements,1)]
    plan=ImplementationPlan(id=str(uuid4()),version=1,architectureCandidateId=arch.recommendedCandidateId,steps=steps,testPlan=["Run unit, integration, browser acceptance, and isolation suites"],deploymentPlan=["Build immutable artifact","Deploy preview","Require approval","Run health verification","Rollback on failure"])
    return {"requirementGraph":req.model_dump(mode="json"),"capabilityGraph":cap.model_dump(mode="json"),"toolRegistry":tools.model_dump(mode="json"),"architecturePlan":arch.model_dump(mode="json"),"implementationPlan":plan.model_dump(mode="json")}
