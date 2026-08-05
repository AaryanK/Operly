"""Typed business-architecture blueprints used before choosing a runtime."""
from copy import deepcopy


ARCHITECTURES = {
    "field_service":{"signals":["dispatch","rescue","repair","technician","field service"],"entities":["customer","service_request","assignment","status_event"],"workflows":["request_lifecycle","assignment"],"routes":["public_intake","customer_status","dispatch_queue"],"executionMode":"source_backed_managed"},
    "booking":{"signals":["booking","appointment","reservation","schedule"],"entities":["customer","resource","availability","booking"],"workflows":["availability_hold","confirmation","cancellation"],"routes":["availability","booking_checkout","calendar"],"executionMode":"agentic_code"},
    "commerce":{"signals":["store","commerce","shop","checkout","orders"],"entities":["customer","product","cart","order","payment"],"workflows":["checkout","fulfillment","refund"],"routes":["catalog","cart","checkout","orders"],"executionMode":"agentic_code"},
    "membership":{"signals":["membership","members","subscription","club"],"entities":["member","plan","subscription","entitlement"],"workflows":["enrollment","renewal","access_control"],"routes":["plans","member_portal","administration"],"executionMode":"agentic_code"},
    "inventory":{"signals":["inventory","warehouse","stock","purchase order"],"entities":["item","location","stock_movement","supplier","purchase_order"],"workflows":["replenishment","receiving","transfer"],"routes":["stock","movements","suppliers"],"executionMode":"agentic_code"},
    "crm":{"signals":["crm","sales pipeline","leads","accounts"],"entities":["account","contact","lead","opportunity","activity"],"workflows":["lead_qualification","stage_progression","follow_up"],"routes":["pipeline","accounts","contacts"],"executionMode":"agentic_code"},
    "quotation":{"signals":["quotation","quote","estimate","proposal"],"entities":["customer","quote","line_item","approval","revision"],"workflows":["draft_quote","customer_acceptance","revision"],"routes":["quote_builder","customer_quote","quote_register"],"executionMode":"agentic_code"},
    "marketplace":{"signals":["marketplace","vendors","sellers","two-sided"],"entities":["buyer","seller","listing","transaction","payout"],"workflows":["seller_onboarding","transaction","dispute"],"routes":["discovery","seller_portal","marketplace_operations"],"executionMode":"agentic_code"},
    "approval":{"signals":["approval","requests and approvals","review workflow"],"entities":["requester","approval_request","decision","policy"],"workflows":["routing","decision","escalation"],"routes":["request_form","approval_inbox","policy_admin"],"executionMode":"agentic_code"},
}


def classify_architecture(prompt:str)->str:
    text=prompt.lower();matches=[]
    for key,spec in ARCHITECTURES.items():
        score=sum(1 for signal in spec["signals"] if signal in text)
        if score:matches.append((score,key))
    return max(matches)[1] if matches else "field_service"


def architecture_plan(prompt:str)->dict:
    family=classify_architecture(prompt);spec=deepcopy(ARCHITECTURES[family])
    return {"family":family,"prompt":prompt,"entities":spec["entities"],"workflows":spec["workflows"],"routes":spec["routes"],"executionMode":spec["executionMode"],"requiresSandbox":spec["executionMode"]=="agentic_code"}


def catalog()->list[dict]:
    return [{"id":key,**{name:value for name,value in spec.items() if name!="signals"}} for key,spec in ARCHITECTURES.items()]
