"""Deterministic architecture-first planner. Model output must validate through SoftwarePlan."""
from copy import deepcopy
import re

from packages.custom_software.architectures import architecture_plan
from packages.custom_software.schema import SoftwarePlan
from packages.custom_software.synthesis import identify_domain, synthesize

DESIGNS=["editorial","utility","dashboard_led","conversion_focused","image_led","minimal","modular_grid","asymmetric"]

def _role(id,name,permissions,access="authenticated",scope="tenant"):
    return {"id":id,"name":name,"description":f"{name} capabilities", "permissions":permissions,"access":access,"dataScope":scope}
def _field(id,type="string",required=True):return {"id":id,"type":type,"required":required,"sensitive":False,"options":[]}
def _entity(id,purpose,roles,lifecycle=[]):return {"id":id,"name":id.replace("_"," ").title(),"purpose":purpose,"fields":[_field("name")],"relationshipIds":[],"ownership":"tenant","visibility":roles,"lifecycle":lifecycle}
def _transition(id,a,b,actors,approval=False):return {"id":id,"fromState":a,"toState":b,"actors":actors,"guards":[],"sideEffects":["append_audit_event"],"approvalRequired":approval}
def _surface(id,route,audience,entities,workflows=[],access="authenticated",components=[]):return {"id":id,"name":id.replace("_"," ").title(),"route":route,"audience":audience,"purpose":id.replace("_"," "),"majorComponents":components or ["navigation","content","status"],"relatedEntities":entities,"relatedWorkflows":workflows,"access":access}

def _design(prompt,architecture):
    text=prompt.lower();family=next((x for x in DESIGNS if x.replace("_"," ") in text),None)
    if not family:family={"field_service":"conversion_focused","quotation":"editorial","inventory":"dashboard_led"}.get(architecture,"asymmetric")
    return {"family":family,"visualPersonality":f"{family.replace('_',' ')} and domain-specific","navigationFamily":"sidebar" if family in {"utility","dashboard_led"} else "topbar","heroFamily":"split" if family in {"conversion_focused","image_led"} else "typographic","typographyPairing":"distinct display with accessible grotesk body","typeScale":"fluid modular","contentDensity":"compact" if family in {"utility","dashboard_led"} else "comfortable","spacingSystem":"8px responsive","gridSystem":"12-column adaptive","surfaceStyle":"tonal sections","cardStyle":"integrated panels","ctaStrategy":"task-priority","mediaStrategy":"domain imagery with functional diagrams","motionStrategy":"reduced-motion-safe feedback","responsiveBehavior":"navigation and grids recompose; no horizontal workflow loss","accessibilityGoals":["WCAG 2.2 AA","keyboard operation","visible focus","semantic landmarks"]}

def _field_service():
    roles=[_role("customer","Customer",["request:create","status:read"],"public","own"),_role("dispatcher","Dispatcher",["request:read","request:assign","request:transition"]),_role("technician","Technician",["request:read_assigned","request:transition"],scope="assigned"),_role("administrator","Administrator",["project:manage"],scope="all")]
    entities=[_entity("customer","Requester identity",["customer","dispatcher","administrator"]),_entity("service_request","A request for mobile service",["customer","dispatcher","technician","administrator"],["submitted","assigned","en_route","completed"]),_entity("status_event","Immutable lifecycle audit",["customer","dispatcher","technician","administrator"])]
    states=["submitted","assigned","en_route","completed"]
    workflows=[{"id":"service_lifecycle","name":"Service lifecycle","trigger":"public request","states":states,"transitions":[_transition("assign","submitted","assigned",["dispatcher"]),_transition("depart","assigned","en_route",["dispatcher","technician"]),_transition("complete","en_route","completed",["dispatcher","technician"])],"failureBehavior":"reject invalid or stale transitions"}]
    surfaces=[_surface("public_request","/generated/{slug}",["customer"],["customer","service_request"],["service_lifecycle"],"public",["hero","request_form","trust_strip"]),_surface("customer_status","/generated/{slug}/status/{reference}",["customer"],["service_request","status_event"],["service_lifecycle"],"public"),_surface("dispatch_queue","/generated/{slug}/dispatch",["dispatcher","administrator"],["service_request","status_event"],["service_lifecycle"],components=["queue","assignment","status_actions"])]
    relationships=[{"id":"customer_has_requests","sourceEntity":"customer","targetEntity":"service_request","cardinality":"one_to_many","implementationSupport":"architecture_pack"}]
    for entity in entities:entity["relationshipIds"]=[x["id"] for x in relationships if entity["id"] in {x["sourceEntity"],x["targetEntity"]}]
    return roles,entities,relationships,workflows,surfaces

def _quotation():
    roles=[_role("customer","Customer",["inquiry:create","quotation:read_own","revision:create"],"public","own"),_role("agent","Agent",["inquiry:read","quotation:write","quotation:send"]),_role("manager","Manager",["quotation:approve"]),_role("administrator","Administrator",["quotation:manage"],scope="all")]
    ids=["customer","inquiry","quotation","quotation_version","line_item","deliverable","approval","status_event"]
    entities=[_entity(x,{"inquiry":"Customer need","quotation":"Commercial offer","quotation_version":"Immutable revision","line_item":"Priced component","deliverable":"Itinerary or deliverable","approval":"Manager decision","status_event":"Audit history"}.get(x,"Customer record"),[r["id"] for r in roles], ["inquiry_received","quotation_draft","internal_review","approved_for_sending","sent_to_customer","revision_requested","revised","accepted","rejected"] if x=="quotation" else []) for x in ids]
    states=["inquiry_received","quotation_draft","internal_review","approved_for_sending","sent_to_customer","revision_requested","revised","accepted","rejected"]
    ts=[_transition("draft","inquiry_received","quotation_draft",["agent"]),_transition("review","quotation_draft","internal_review",["agent"]),_transition("approve","internal_review","approved_for_sending",["manager"],True),_transition("send","approved_for_sending","sent_to_customer",["agent"]),_transition("request_revision","sent_to_customer","revision_requested",["customer"]),_transition("revise","revision_requested","revised",["agent"]),_transition("resend","revised","sent_to_customer",["agent"]),_transition("accept","sent_to_customer","accepted",["customer"]),_transition("reject","sent_to_customer","rejected",["customer"])]
    wf=[{"id":"quotation_lifecycle","name":"Quotation lifecycle","trigger":"inquiry submitted","states":states,"transitions":ts,"failureBehavior":"preserve approved version and reject stale changes"}]
    surfaces=[_surface("public_inquiry","/quotes/{slug}",["customer"],["customer","inquiry"],["quotation_lifecycle"],"public",["editorial_hero","inquiry_form"]),_surface("inquiry_queue","/quotes/{slug}/staff",["agent","manager"],["inquiry","quotation"],["quotation_lifecycle"],components=["inquiry_queue","quotation_editor","version_history"]),_surface("approval_review","/quotes/{slug}/approvals",["manager"],["quotation","approval"],["quotation_lifecycle"],components=["approval_inbox","margin_review"]),_surface("customer_quotation","/quotes/{slug}/view/{token}",["customer"],["quotation","quotation_version","line_item","deliverable"],["quotation_lifecycle"],"public",["itinerary","price_sidebar","revision_action"])]
    relationships=[{"id":"customer_has_inquiries","sourceEntity":"customer","targetEntity":"inquiry","cardinality":"one_to_many","implementationSupport":"architecture_pack"},{"id":"quotation_has_versions","sourceEntity":"quotation","targetEntity":"quotation_version","cardinality":"one_to_many","implementationSupport":"architecture_pack"},{"id":"version_has_lines","sourceEntity":"quotation_version","targetEntity":"line_item","cardinality":"one_to_many","implementationSupport":"architecture_pack"}]
    for entity in entities:entity["relationshipIds"]=[x["id"] for x in relationships if entity["id"] in {x["sourceEntity"],x["targetEntity"]}]
    return roles,entities,relationships,wf,surfaces

def _inventory():
    roles=[_role("owner","Owner",["inventory:all"],scope="all"),_role("manager","Manager",["purchase_order:approve","inventory:adjust"]),_role("stock_employee","Stock employee",["stock:receive","stock:adjust"]),_role("purchasing_employee","Purchasing employee",["purchase_order:write"]),_role("administrator","Administrator",["inventory:admin"],scope="all")]
    ids=["product","supplier","inventory_location","stock_level","stock_movement","purchase_order","purchase_order_line_item","reorder_rule"]
    entities=[_entity(x,x.replace("_"," "),[r["id"] for r in roles],["draft","approved","ordered","partially_received","received","closed"] if x=="purchase_order" else []) for x in ids]
    states=["draft","approved","ordered","partially_received","received","closed"]
    wf=[{"id":"purchase_order_lifecycle","name":"Purchase order lifecycle","trigger":"reorder or manual draft","states":states,"transitions":[_transition("approve","draft","approved",["manager"]),_transition("order","approved","ordered",["purchasing_employee"]),_transition("partial_receive","ordered","partially_received",["stock_employee"]),_transition("continue_receive","partially_received","received",["stock_employee"]),_transition("receive_full","ordered","received",["stock_employee"]),_transition("close","received","closed",["manager"])],"failureBehavior":"rollback the entire stock transaction"}]
    surfaces=[_surface("inventory_dashboard","/inventory/{slug}",["owner","manager","stock_employee","purchasing_employee"],["product","stock_level","reorder_rule"],components=["stock_metrics","low_stock_queue","movement_chart"]),_surface("product_catalog","/inventory/{slug}/products",["owner","manager","stock_employee"],["product","supplier","stock_level"],components=["bounded_table","filters","product_detail"]),_surface("receiving","/inventory/{slug}/receiving",["stock_employee","manager"],["purchase_order","stock_movement","stock_level"],["purchase_order_lifecycle"],components=["purchase_order_lookup","receipt_lines"]),_surface("purchase_orders","/inventory/{slug}/purchase-orders",["manager","purchasing_employee"],["purchase_order","purchase_order_line_item"],["purchase_order_lifecycle"],components=["purchase_order_editor","approval_action"])]
    relationships=[{"id":"product_has_stock","sourceEntity":"product","targetEntity":"stock_level","cardinality":"one_to_many","implementationSupport":"architecture_pack"},{"id":"supplier_has_orders","sourceEntity":"supplier","targetEntity":"purchase_order","cardinality":"one_to_many","implementationSupport":"architecture_pack"},{"id":"order_has_lines","sourceEntity":"purchase_order","targetEntity":"purchase_order_line_item","cardinality":"one_to_many","implementationSupport":"architecture_pack"}]
    for entity in entities:entity["relationshipIds"]=[x["id"] for x in relationships if entity["id"] in {x["sourceEntity"],x["targetEntity"]}]
    return roles,entities,relationships,wf,surfaces

def build_software_plan(prompt:str)->SoftwarePlan:
    classification=architecture_plan(prompt)
    synthesized=synthesize(prompt)
    synthesized_domain,fixture,_=identify_domain(prompt)
    # Installed packs are selected only for an actual semantic match. A known
    # custom domain always wins over a coincidental keyword match.
    primary=synthesized_domain if fixture else classification["primaryArchitecture"]
    if primary=="field_service":roles,entities,relationships,workflows,surfaces=_field_service()
    elif primary=="quotation":roles,entities,relationships,workflows,surfaces=_quotation()
    elif primary=="inventory":roles,entities,relationships,workflows,surfaces=_inventory()
    else:
        role_ids=synthesized["roles"]
        roles=[_role(x,x.replace("_"," ").title(),["workspace:read","domain:operate"],"public" if x in {"viewer","buyer"} else "authenticated","own" if x in {"viewer","buyer","player"} else "tenant") for x in role_ids]
        entities=[_entity(x,f"{x.replace('_',' ').title()} domain record",role_ids) for x in synthesized["entities"]]
        relationships=[]
        workflows=[]
        for node in synthesized["architectureNodes"]:
            states=["draft","active","completed"]
            workflows.append({"id":node["id"]+"_workflow","name":node["name"],"trigger":"validated domain command","states":states,"transitions":[_transition("activate","draft","active",role_ids[:-1] or role_ids),_transition("complete","active","completed",role_ids[:-1] or role_ids)],"failureBehavior":"reject invalid transitions and preserve an audit event"})
        workflow_ids=[x["id"] for x in workflows]
        surfaces=[_surface(x,f"/generated/{{slug}}/{x.replace('_','-')}",role_ids,synthesized["entities"],workflow_ids,access="public" if x in {"standings","fruit_catalog"} else "authenticated",components=["domain_navigation",x,"validation_feedback"]) for x in synthesized["pages"]]
    mode=classification["implementationMode"] if primary in {"field_service","quotation","inventory"} else "sandbox_generated"
    confidence=classification["confidence"] if primary in {"field_service","quotation","inventory"} else synthesized["confidence"]
    name=re.sub(r"[^A-Za-z0-9 &-]+"," ",prompt).strip().split(".")[0][:80]
    effective=[prompt.strip()]
    capability_rows=synthesized["capabilities"] if mode=="sandbox_generated" else []
    generated=[x["id"] for x in capability_rows if x["implementation"]!="reuse_primitive"]
    tests=["tenant_isolation","authorization","persistence_after_reload","invalid_input","failure_paths","responsive_layout"]
    tests += ["invariant_"+re.sub(r"[^a-z0-9]+","_",x.lower()).strip("_")[:48] for x in synthesized["invariants"]]
    evidence=[{"requirementId":f"req_{i+1}","requirement":cap["requirement"],"artifactIds":[cap["id"]],"testIds":[f"test_{cap['id']}"],"status":"planned"} for i,cap in enumerate(capability_rows)]
    stack=synthesized["stack"]
    plan={"projectName":name or synthesized["domain"].replace("_"," ").title(),"summary":prompt.strip(),"productCategory":primary.replace("_"," "),"targetUsers":[x["name"] for x in roles],"businessDomain":primary.replace("_"," "),"primaryGoal":"Implement the requested domain without substituting an unrelated architecture","successCriteria":["approved workflows function end to end","tenant data remains isolated","mandatory requirements have executable evidence"],"primaryArchitecture":primary,"secondaryArchitectures":classification["secondaryArchitectures"] if mode!="sandbox_generated" else [],"implementationMode":mode,"confidence":confidence,"rationale":classification["rationale"] if mode!="sandbox_generated" else f"Synthesized a custom architecture from {len(capability_rows)} capabilities; no unrelated installed pack was selected.","roles":roles,"entities":entities,"relationships":relationships,"workflows":workflows,"surfaces":surfaces,"backendCapabilities":["CRUD","search","audit_history","optimistic_concurrency"]+generated,"integrations":[x["id"] for x in capability_rows if x["implementation"]=="integration_adapter"],"design":_design(prompt,primary),"runtime":{"strategy":mode,"reason":"Installed architecture pack selected" if mode=="architecture_pack" else "Custom domain requires isolated generated-software runtime","primaryPack":primary if mode=="architecture_pack" else None,"secondaryPacks":[]},"securityConstraints":["tenant isolation","least privilege","generated code outside control plane","deny-by-default network","secret isolation","audited state transitions"],"unsupportedRequirements":[] if mode=="sandbox_generated" else classification["compatibility"]["issues"],"risks":["external integrations remain sandbox adapters until configured"],"testRequirements":tests,"deploymentRequirements":["migration review","preview acceptance","human approval","atomic deployment","rollback"],"effectiveRequirements":effective,"capabilities":capability_rows,"architectureNodes":synthesized["architectureNodes"] if mode=="sandbox_generated" else [],"stack":{"frontend":stack[0],"backend":stack[1],"database":stack[2],"runtime":stack[3],"reasons":["selected from interaction, processing, persistence, and isolation requirements"],"dependencies":[]},"requirementEvidence":evidence,"reusedPrimitives":[x["id"] for x in capability_rows if x["implementation"]=="reuse_primitive"],"generatedComponents":generated,"provenance":{"originalPrompt":prompt.strip(),"revisions":[],"generatedPrompts":[],"redactionPolicy":"secrets and credentials are never persisted"}}
    lower_prompt=prompt.lower()
    if "whatsapp" in lower_prompt and "whatsapp" not in plan["integrations"]:plan["integrations"].append("whatsapp")
    if "email status" in lower_prompt and "email_status_updates" not in plan["backendCapabilities"]:plan["backendCapabilities"].append("email_status_updates")
    return SoftwarePlan.model_validate(plan)

def revise_plan(current:SoftwarePlan,request:str)->SoftwarePlan:
    provenance=deepcopy(current.provenance)
    original=provenance.get("originalPrompt",current.summary)
    revisions=[*provenance.get("revisions",[]),request.strip()]
    # Re-synthesis makes revisions structurally effective. The immutable prior
    # plan version remains in SoftwarePlanVersion.
    revision_domain,revision_fixture,_=identify_domain(request)
    effective_prompt=request if revision_fixture else (original+"\nRevision requirements:\n"+"\n".join(revisions))
    updated=build_software_plan(effective_prompt)
    data=updated.model_dump();data["summary"]=original;data["effectiveRequirements"]=[original,*revisions]
    data["provenance"]={**provenance,"originalPrompt":original,"revisions":revisions,"generatedPrompts":provenance.get("generatedPrompts",[]),"redactionPolicy":"secrets and credentials are never persisted"}
    data["rationale"] += f" Structurally regenerated for revision {len(revisions)}."
    return SoftwarePlan.model_validate(data)
