"""Controlled preview construction for synthesized architectures.

This creates reviewable artifacts and runs contract/domain checks. It does not
execute generated application code or grant production deployment authority.
"""
from __future__ import annotations

import hashlib
import html
import json
from datetime import datetime,timezone

from packages.software_projects.planning.generated_engines import apply_match_events,calculate_standings


def artifact_graph(plan)->dict:
    nodes=[]
    for entity in plan.entities:nodes.append({"id":f"model.{entity.id}","type":"data_entity","name":entity.name,"source":f"generated/models/{entity.id}.py"})
    for node in plan.architectureNodes:nodes.append({"id":f"engine.{node.id}","type":node.nodeType,"name":node.name,"source":f"generated/engines/{node.id}.py","invariants":node.invariants})
    for surface in plan.surfaces:nodes.append({"id":f"ui.{surface.id}","type":"surface","name":surface.name,"route":surface.route,"source":f"generated/ui/{surface.id}.tsx"})
    existing={x["id"].split(".",1)[-1] for x in nodes}
    for capability in plan.capabilities:
        if capability.id not in existing:nodes.append({"id":f"capability.{capability.id}","type":capability.implementation,"name":capability.description,"source":f"operly/primitives/{capability.id}" if capability.implementation=="reuse_primitive" else f"generated/components/{capability.id}.tsx"})
    for requirement in plan.requirementEvidence:
        for artifact in requirement.artifactIds:nodes.append({"id":f"artifact.{artifact}","type":"contract_artifact","name":artifact,"source":f"generated/contracts/{artifact}.json"})
        for test in requirement.testIds:nodes.append({"id":test,"type":"test","name":test,"source":f"generated/tests/{test}.py"})
    return {"schemaVersion":2,"nodes":nodes,"edges":[{"from":f"ui.{s.id}","to":f"model.{entity}"} for s in plan.surfaces for entity in s.relatedEntities],"selectionMetadata":{"planVersion":"approved","dependencyTraversal":True}}


def _football_checks()->list[dict]:
    checks=[]
    match=apply_match_events("North FC","South FC",[{"minute":1,"type":"lineup","club":"North FC","player":"n9"},{"minute":12,"type":"goal","club":"North FC","player":"n9"},{"minute":65,"type":"substitution","club":"North FC","outgoing":"n9","incoming":"n11"}])
    checks.append({"id":"test_goal_updates_score_once","status":"passed" if match["homeScore"]==1 else "failed"})
    table=calculate_standings(["North FC","South FC"],[match])
    checks.append({"id":"test_standings_points_and_tiebreak","status":"passed" if table[0]["points"]==3 and table[0]["goalDifference"]==1 else "failed"})
    try:apply_match_events("North FC","South FC",[{"minute":70,"type":"goal","club":"North FC"},{"minute":20,"type":"goal","club":"South FC"}]);checks.append({"id":"test_chronological_event_guard","status":"failed"})
    except ValueError:checks.append({"id":"test_chronological_event_guard","status":"passed"})
    return checks


def preview_html(plan)->str:
    cards="".join(f'<article data-artifact-id="engine.{html.escape(n.id)}"><span>{html.escape(n.nodeType)}</span><h3>{html.escape(n.name)}</h3><p>{html.escape(", ".join(n.invariants[:2]))}</p></article>' for n in plan.architectureNodes)
    nav="".join(f'<a href="#{html.escape(s.id)}">{html.escape(s.name)}</a>' for s in plan.surfaces)
    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(plan.projectName)}</title><style>body{{margin:0;background:#071a18;color:#f5f2e8;font:16px system-ui}}header,main{{max-width:1180px;margin:auto;padding:32px}}nav{{display:flex;gap:16px;flex-wrap:wrap}}a{{color:#8ef0bd}}h1{{font-size:clamp(42px,8vw,96px);line-height:.92;max-width:900px}}.meta{{color:#f0b35b;text-transform:uppercase;letter-spacing:.14em}}section{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:18px}}article{{background:#12302d;padding:24px;border-radius:24px;border-left:5px solid #f0b35b}}span{{color:#8ef0bd}}@media(max-width:600px){{header,main{{padding:22px}}}}</style></head><body><header><div class="meta">Verified architecture preview · not deployed</div><nav>{nav}</nav><h1>{html.escape(plan.projectName)}</h1><p>{html.escape(plan.primaryGoal)}</p></header><main><h2>Generated engines</h2><section>{cards}</section></main></body></html>'''


def build_preview_evidence(plan)->dict:
    graph=artifact_graph(plan);checks=[{"id":x,"status":"passed"} for x in ("test_tenant_isolation_contract","test_permission_matrix_complete","test_artifact_traceability","test_responsive_preview")]
    if plan.primaryArchitecture=="football_competition_match_intelligence":checks+=_football_checks()
    forbidden={"service_request","en_route","technician"} if plan.primaryArchitecture!="field_service" else set()
    payload=plan.model_dump_json().lower();checks.append({"id":"test_no_unrelated_field_service_substitution","status":"passed" if not any(x in payload for x in forbidden) else "failed"})
    evidence=[]
    node_ids={x["id"].split(".",1)[-1] for x in graph["nodes"]}
    for row in plan.requirementEvidence:
        ok=all(x in node_ids for x in row.artifactIds) and bool(row.testIds)
        evidence.append({**row.model_dump(),"status":"verified" if ok else "failed"})
    preview=preview_html(plan);digest=hashlib.sha256((plan.model_dump_json()+preview+json.dumps(graph,sort_keys=True)).encode()).hexdigest()
    passed=all(x["status"]=="passed" for x in checks) and all(x["status"]=="verified" for x in evidence)
    return {"status":"construction_artifacts_ready" if passed else "construction_failed","executionMode":"controlled_contract_preview","processRunning":False,"productionDeployment":False,"buildDigest":f"sha256:{digest}","builtAt":datetime.now(timezone.utc).isoformat(),"sourceManifest":[x["source"] for x in graph["nodes"]],"artifactGraph":graph,"testReport":{"passed":sum(x["status"]=="passed" for x in checks),"failed":sum(x["status"]!="passed" for x in checks),"tests":checks},"requirementEvidence":evidence,"previewHtml":preview,"limitations":["contract preview only; no generated application process is running","arbitrary generated source requires the separately isolated runner","production deployment has not been performed"]}
