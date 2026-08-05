"""Deterministic architecture-first planner. Model output must validate through SoftwarePlan."""
from copy import deepcopy
import re

from packages.custom_software.architectures import architecture_plan
from packages.custom_software.schema import SoftwarePlan

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
    classification=architecture_plan(prompt);primary=classification["primaryArchitecture"]
    if primary=="field_service":roles,entities,relationships,workflows,surfaces=_field_service()
    elif primary=="quotation":roles,entities,relationships,workflows,surfaces=_quotation()
    elif primary=="inventory":roles,entities,relationships,workflows,surfaces=_inventory()
    else:
        roles=[_role("administrator","Administrator",["plan:review"],scope="all")];entities=[];relationships=[];workflows=[];surfaces=[]
    mode=classification["implementationMode"];name=re.sub(r"[^A-Za-z0-9 ]+"," ",prompt).strip().split(".")[0][:80]
    plan={"projectName":name or "Planned software","summary":prompt.strip(),"productCategory":primary.replace("_"," "),"targetUsers":[x["name"] for x in roles],"businessDomain":primary.replace("_"," "),"primaryGoal":"Implement the requested business loop without unrelated architecture assumptions","successCriteria":["approved workflows function end to end","tenant data remains isolated","planned surfaces are traceable"],"primaryArchitecture":primary,"secondaryArchitectures":classification["secondaryArchitectures"],"implementationMode":mode,"confidence":classification["confidence"],"rationale":classification["rationale"],"roles":roles,"entities":entities,"relationships":relationships,"workflows":workflows,"surfaces":surfaces,"backendCapabilities":["CRUD","search","audit_history","optimistic_concurrency"]+( ["signed_customer_access","notifications"] if primary in {"field_service","quotation"} else ["transactional_stock_changes","bounded_pagination"] if primary=="inventory" else []),"integrations":[],"design":_design(prompt,primary),"runtime":{"strategy":mode,"reason":"Installed architecture pack selected" if mode=="architecture_pack" else "No installed pack satisfies the approved plan","primaryPack":primary if mode=="architecture_pack" else None,"secondaryPacks":classification["secondaryArchitectures"]},"securityConstraints":["tenant isolation","least privilege","no executable plan content","audited state transitions"],"unsupportedRequirements":classification["compatibility"]["issues"],"risks":["external integrations require explicit configuration"] if classification["compatibility"]["issues"] else [],"testRequirements":["tenant isolation","authorization","state transitions","stale write rejection","browser business loop"],"deploymentRequirements":["migration review","preview acceptance","human approval","atomic deployment","rollback"]}
    return SoftwarePlan.model_validate(plan)

def revise_plan(current:SoftwarePlan,request:str)->SoftwarePlan:
    data=deepcopy(current.model_dump());text=request.lower()
    if "do not use payments" in text:data["backendCapabilities"]=[x for x in data["backendCapabilities"] if x!="payments"];data["integrations"]=[x for x in data["integrations"] if x!="payments"]
    if "whatsapp" in text and "whatsapp" not in data["integrations"]:data["integrations"].append("whatsapp")
    if "add another role" in text:data["roles"].append(_role("reviewer","Reviewer",["plan:review"]))
    if "email status" in text:data["backendCapabilities"].append("email_status_updates")
    data["rationale"] += f" Revision applied: {request.strip()}"
    return SoftwarePlan.model_validate(data)
