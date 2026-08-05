"""Architecture classification and compatibility planning; packs are capabilities, not templates."""
from copy import deepcopy

ARCHITECTURES={
"field_service":{"signals":["dispatch","rescue","repair","technician","field service","locksmith","hvac","roadside"],"entities":["customer","service_request","assignment","status_event"],"workflows":["request_lifecycle","assignment"],"routes":["public_intake","customer_status","dispatch_queue"],"executionMode":"architecture_pack"},
"booking":{"signals":["booking","appointment","reservation","schedule"],"entities":["customer","resource","availability","booking"],"workflows":["availability_hold","confirmation","cancellation"],"routes":["availability","booking_checkout","calendar"],"executionMode":"sandbox_generated"},
"commerce":{"signals":["store","commerce","shop","checkout","orders"],"entities":["customer","product","cart","order","payment"],"workflows":["checkout","fulfillment","refund"],"routes":["catalog","cart","checkout","orders"],"executionMode":"sandbox_generated"},
"membership":{"signals":["membership","members","subscription","member-only","renewals"],"entities":["member","plan","subscription","entitlement","event"],"workflows":["enrollment","renewal","access_control"],"routes":["plans","member_portal","administration"],"executionMode":"sandbox_generated"},
"inventory":{"signals":["inventory","warehouse","stock","purchase order","suppliers","receiving"],"entities":["product","supplier","inventory_location","stock_movement","purchase_order","reorder_rule"],"workflows":["purchase_order_lifecycle","receiving","adjustment"],"routes":["inventory_dashboard","products","receiving","low_stock","purchase_orders"],"executionMode":"architecture_pack"},
"crm":{"signals":["crm","sales pipeline","leads","accounts","traveler crm"],"entities":["account","customer","lead","opportunity","activity"],"workflows":["lead_qualification","stage_progression","follow_up"],"routes":["pipeline","accounts","contacts"],"executionMode":"sandbox_generated"},
"quotation":{"signals":["quotation","quote","estimate","proposal","itinerary","travel agency"],"entities":["customer","inquiry","quotation","quotation_version","line_item","approval"],"workflows":["quotation_lifecycle","manager_approval","customer_revision"],"routes":["inquiry","quotation_editor","approval","customer_quote"],"executionMode":"architecture_pack"},
"marketplace":{"signals":["marketplace","vendors","sellers","two-sided"],"entities":["buyer","seller","listing","transaction","payout"],"workflows":["seller_onboarding","transaction","dispute"],"routes":["discovery","seller_portal","marketplace_operations"],"executionMode":"sandbox_generated"},
"approval":{"signals":["approval system","expense approval","requests and approvals","review workflow"],"entities":["requester","approval_request","decision","policy"],"workflows":["routing","decision","escalation"],"routes":["request_form","approval_inbox","policy_admin"],"executionMode":"sandbox_generated"},
"support_desk":{"signals":["support desk","help desk","tickets"],"entities":["customer","ticket","conversation","sla"],"workflows":["triage","resolution"],"routes":["ticket_portal","agent_queue"],"executionMode":"sandbox_generated"},
"project_management":{"signals":["project management","projects and tasks","kanban"],"entities":["project","task","milestone"],"workflows":["task_lifecycle"],"routes":["projects","board"],"executionMode":"sandbox_generated"},
"content_platform":{"signals":["content platform","publishing","articles","courses"],"entities":["author","content","publication"],"workflows":["editorial_review"],"routes":["library","editor"],"executionMode":"sandbox_generated"},
"custom":{"signals":[],"entities":[],"workflows":[],"routes":[],"executionMode":"sandbox_generated"},
}

PACKS={
"field_service":{"required":[],"optional":["payments","messaging"],"conflicts":[],"status":"installed"},
"quotation":{"required":[],"optional":["crm","messaging","document_generation"],"conflicts":["field_service"],"status":"installed"},
"inventory":{"required":[],"optional":["commerce"],"conflicts":["field_service"],"status":"installed"},
}

def classify_architectures(prompt:str)->tuple[str,list[str],float,str]:
    text=prompt.lower();matches=[]
    for key,spec in ARCHITECTURES.items():
        score=sum(1 for signal in spec["signals"] if signal in text)
        if score:matches.append((score,key))
    if not matches:return "custom",[],0.25,"No installed architecture has sufficient domain evidence; clarification or sandbox generation is required."
    matches.sort(reverse=True);primary=matches[0][1];secondary=[key for score,key in matches[1:] if key!=primary and score>0]
    confidence=min(.98,.58+.12*matches[0][0]);return primary,secondary,confidence,f"Matched domain capabilities for {primary.replace('_',' ')}; selection is independent of visual design."

def classify_architecture(prompt:str)->str:return classify_architectures(prompt)[0]

def compatibility(primary:str,secondary:list[str])->dict:
    installed=PACKS.get(primary);issues=[]
    if not installed:issues.append(f"{primary} has no installed architecture pack")
    for item in secondary:
        if item in (installed or {}).get("conflicts",[]):issues.append(f"{primary} conflicts with {item}")
        elif item not in (installed or {}).get("optional",[]) and item not in PACKS:issues.append(f"{item} requires sandbox generation")
    return {"compatible":not issues,"issues":issues,"sharedEntities":["customer"] if {primary,*secondary}>={"crm","quotation"} else []}

def architecture_plan(prompt:str)->dict:
    family,secondary,confidence,rationale=classify_architectures(prompt);spec=deepcopy(ARCHITECTURES[family]);check=compatibility(family,secondary)
    mode=spec["executionMode"] if check["compatible"] or spec["executionMode"]=="sandbox_generated" else "hybrid"
    return {"family":family,"primaryArchitecture":family,"secondaryArchitectures":secondary,"confidence":confidence,"rationale":rationale,"entities":spec["entities"],"workflows":spec["workflows"],"routes":spec["routes"],"executionMode":mode,"implementationMode":mode,"requiresSandbox":mode in {"sandbox_generated","hybrid"},"compatibility":check}

def catalog()->list[dict]:return [{"id":key,**{name:value for name,value in spec.items() if name!="signals"},"pack":PACKS.get(key)} for key,spec in ARCHITECTURES.items()]
