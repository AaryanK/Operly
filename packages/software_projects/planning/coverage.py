"""Fail-closed approved-plan to installed-pack implementation coverage."""
from packages.software_projects.planning.packs import PACKS

def implementation_coverage(plan,artifact_graph:dict)->dict:
    pack=PACKS.get(plan.primaryArchitecture)
    if not pack:return {"complete":False,"materialMissing":["architecture_pack"],"implemented":{},"deferred":plan.unsupportedRequirements}
    planned={"entities":{x.id for x in plan.entities},"relationships":{x.id for x in plan.relationships},"roles":{x.id for x in plan.roles},"workflows":{x.id for x in plan.workflows},"pages":{x.id for x in plan.surfaces},"permissions":{p for x in plan.roles for p in x.permissions},"tests":set(plan.testRequirements)}
    implemented={"entities":set(pack.entities),"relationships":set(pack.relationships),"roles":set(pack.roles),"workflows":set(pack.workflows),"pages":set(pack.public_surfaces)|set(pack.internal_surfaces),"permissions":set(pack.permissions),"tests":set(pack.tests)}
    missing={key:sorted(value-implemented[key]) for key,value in planned.items()};extra={key:sorted(implemented[key]-value) for key,value in planned.items() if key in {"roles","permissions","workflows","entities"}}
    mapped_pages={x.get("page") for x in artifact_graph.get("nodes",[])};missing_artifacts=sorted(planned["pages"]-mapped_pages)
    material=[f"{key}:{item}" for key,items in missing.items() for item in items if key not in {"tests"}] + [f"artifact:{x}" for x in missing_artifacts]
    return {"complete":not material,"materialMissing":material,"missing":missing,"unapproved":extra,"implemented":{k:sorted(v&planned[k]) for k,v in implemented.items()},"design":{"planned":plan.design.family,"implemented":plan.design.family},"backendCapabilities":{"planned":plan.backendCapabilities,"implemented":plan.backendCapabilities},"deferred":plan.unsupportedRequirements}
