from __future__ import annotations

import asyncio
import hashlib
import io
import json
import secrets
import zipfile
from datetime import datetime

import httpx

from packages.plugins import temp_app_suite as suite

PLUGIN_ID = "temp.operly-command-center"
PLUGIN_NAME = "Operly Command Center"
ACTIONS = [
    ("business-pulse", "Business pulse"),
    ("executive-brief", "Executive brief"),
    ("revenue-forecast", "Revenue forecast"),
    ("cash-forecast", "Cash forecast"),
    ("inventory-plan", "Inventory plan"),
    ("support-triage", "Support triage"),
    ("customer-health", "Customer health"),
    ("procurement-risk", "Procurement risk"),
    ("fulfillment-risk", "Fulfillment risk"),
    ("campaign-optimizer", "Campaign optimizer"),
    ("contract-risk", "Contract risk"),
    ("task-priority", "Task priority"),
    ("anomaly-scan", "Anomaly scan"),
    ("capacity-plan", "Capacity plan"),
    ("scenario-simulation", "Scenario simulation"),
]

SEED_STATE = {
    "requested_action": "business-pulse",
    "customers": [
        {"id": "CUS-101", "name": "Northstar Dental", "mrr": 4200, "health": 82, "nps": 9, "open_tickets": 1},
        {"id": "CUS-102", "name": "Apex Foods", "mrr": 7600, "health": 61, "nps": 6, "open_tickets": 3},
        {"id": "CUS-103", "name": "Harbor Studio", "mrr": 2800, "health": 91, "nps": 10, "open_tickets": 0},
        {"id": "CUS-104", "name": "Bluebird Labs", "mrr": 5100, "health": 48, "nps": 5, "open_tickets": 4},
    ],
    "deals": [
        {"id": "D-201", "company": "Everwell Clinics", "stage": "Proposal", "value": 18000, "probability": 65, "days_idle": 2, "owner": "Nina"},
        {"id": "D-202", "company": "Sunline Foods", "stage": "Qualified", "value": 9200, "probability": 40, "days_idle": 9, "owner": "Dev"},
        {"id": "D-203", "company": "Atlas Repair", "stage": "Negotiation", "value": 27000, "probability": 80, "days_idle": 1, "owner": "Nina"},
        {"id": "D-204", "company": "Bloom Events", "stage": "New", "value": 6400, "probability": 20, "days_idle": 12, "owner": "Dev"},
    ],
    "invoices": [
        {"id": "INV-2408", "customer": "Mason Retail", "amount": 8420, "days_due": 18, "status": "Overdue"},
        {"id": "INV-2411", "customer": "Luna Fitness", "amount": 3150, "days_due": 5, "status": "Overdue"},
        {"id": "INV-2420", "customer": "Redwood Cafe", "amount": 6725, "days_due": -8, "status": "Open"},
        {"id": "INV-2397", "customer": "Vertex Build", "amount": 11900, "days_due": 31, "status": "Promise to pay"},
    ],
    "inventory": [
        {"id": "SKU-100", "item": "12oz compostable cups", "stock": 380, "reorder_point": 420, "daily_demand": 55, "lead_days": 9, "supplier": "EcoServe"},
        {"id": "SKU-214", "item": "Paper carry bags", "stock": 980, "reorder_point": 600, "daily_demand": 72, "lead_days": 6, "supplier": "PackCo"},
        {"id": "SKU-330", "item": "PLA cutlery sets", "stock": 145, "reorder_point": 300, "daily_demand": 36, "lead_days": 12, "supplier": "GreenWare"},
        {"id": "SKU-501", "item": "Thermal labels", "stock": 860, "reorder_point": 400, "daily_demand": 28, "lead_days": 5, "supplier": "LabelWorks"},
    ],
    "orders": [
        {"id": "SO-5512", "customer": "Redwood Cafe", "stage": "Packing", "items": 18, "hours_open": 22, "promise_hours": 24, "carrier": "UPS"},
        {"id": "SO-5518", "customer": "Luna Fitness", "stage": "Queued", "items": 7, "hours_open": 5, "promise_hours": 18, "carrier": "FedEx"},
        {"id": "SO-5509", "customer": "Mason Retail", "stage": "Ready", "items": 32, "hours_open": 29, "promise_hours": 24, "carrier": "UPS"},
        {"id": "SO-5501", "customer": "Atlas Repair", "stage": "Shipped", "items": 4, "hours_open": 11, "promise_hours": 18, "carrier": "USPS"},
    ],
    "suppliers": [
        {"id": "SUP-1", "name": "EcoServe", "open_po": 12800, "on_time": 92, "lead_days": 9, "risk": "Medium"},
        {"id": "SUP-2", "name": "GreenWare", "open_po": 7600, "on_time": 71, "lead_days": 14, "risk": "High"},
        {"id": "SUP-3", "name": "PackCo", "open_po": 4200, "on_time": 97, "lead_days": 6, "risk": "Low"},
        {"id": "SUP-4", "name": "LabelWorks", "open_po": 2900, "on_time": 95, "lead_days": 5, "risk": "Low"},
    ],
    "tickets": [
        {"id": "T-1048", "subject": "Checkout fails on mobile", "customer": "Northstar Dental", "priority": "Urgent", "status": "New", "owner": "Maya", "sla_hours": 4, "age_hours": 3.2},
        {"id": "T-1047", "subject": "Export missing March records", "customer": "Apex Foods", "priority": "High", "status": "In progress", "owner": "Jon", "sla_hours": 12, "age_hours": 8},
        {"id": "T-1044", "subject": "Need teammate role changed", "customer": "Harbor Studio", "priority": "Normal", "status": "Waiting", "owner": "Rina", "sla_hours": 24, "age_hours": 19},
        {"id": "T-1039", "subject": "Invoice receipt duplicate", "customer": "Bluebird Labs", "priority": "Low", "status": "Resolved", "owner": "Maya", "sla_hours": 48, "age_hours": 10},
    ],
    "campaigns": [
        {"id": "C-21", "campaign": "Fall wholesale push", "channel": "Email", "budget": 4500, "spent": 1900, "progress": 68, "status": "Live", "blocker": ""},
        {"id": "C-22", "campaign": "Local restaurant demo", "channel": "Events", "budget": 8200, "spent": 3100, "progress": 52, "status": "Planning", "blocker": "Venue creative pending"},
        {"id": "C-23", "campaign": "Eco packaging search", "channel": "Search", "budget": 6200, "spent": 5900, "progress": 76, "status": "Live", "blocker": "CPA above target"},
    ],
    "contracts": [
        {"id": "CTR-81", "contract": "Northstar renewal", "counterparty": "Northstar Dental", "value": 50400, "risk_flags": 1, "days_open": 4, "status": "Review"},
        {"id": "CTR-82", "contract": "GreenWare supply MSA", "counterparty": "GreenWare", "value": 92000, "risk_flags": 4, "days_open": 12, "status": "Legal"},
        {"id": "CTR-83", "contract": "Apex expansion", "counterparty": "Apex Foods", "value": 68400, "risk_flags": 2, "days_open": 6, "status": "Approval"},
    ],
    "tasks": [
        {"id": "TASK-1", "title": "Resolve mobile checkout incident", "owner": "Maya", "priority": "Critical", "status": "Doing", "days_due": 0, "impact": 10, "effort": 4},
        {"id": "TASK-2", "title": "Approve GreenWare fallback supplier", "owner": "Sam", "priority": "High", "status": "Todo", "days_due": 1, "impact": 8, "effort": 3},
        {"id": "TASK-3", "title": "Launch customer referral loop", "owner": "Rina", "priority": "Normal", "status": "Todo", "days_due": 5, "impact": 6, "effort": 5},
        {"id": "TASK-4", "title": "Collect INV-2397", "owner": "Ari", "priority": "High", "status": "Doing", "days_due": -2, "impact": 9, "effort": 2},
    ],
    "cash": {"bank": 118000, "monthly_fixed": 54000, "monthly_variable": 26000, "expected_collections_30d": 59000, "expected_payments_30d": 72000},
    "capacity": {"team_hours_week": 320, "committed_hours_week": 278, "support_hours_week": 46},
    "scenario": {"revenue_growth_pct": 8, "collection_delay_days": 7, "demand_change_pct": 12, "cost_change_pct": 5},
}

RUNTIME = r'''from __future__ import annotations
import json, sys, math

def num(v):
    try: return float(v or 0)
    except (TypeError, ValueError): return 0.0

def money(v): return f"${v:,.0f}"
def rows(state, key):
    value=state.get(key,[]) if isinstance(state,dict) else []
    return value if isinstance(value,list) else []
def pack(summary, metrics, recommendations, findings=None):
    return {"summary":summary,"metrics":metrics,"recommendations":recommendations[:8],"findings":(findings or [])[:10]}

packet=json.load(sys.stdin)
args=packet.get("arguments") or {}
state=args.get("state") or {}
action=str(state.get("requested_action") or "business-pulse")
customers=rows(state,"customers"); deals=rows(state,"deals"); invoices=rows(state,"invoices")
inventory=rows(state,"inventory"); orders=rows(state,"orders"); suppliers=rows(state,"suppliers")
tickets=rows(state,"tickets"); campaigns=rows(state,"campaigns"); contracts=rows(state,"contracts"); tasks=rows(state,"tasks")
cash=state.get("cash") if isinstance(state.get("cash"),dict) else {}
capacity=state.get("capacity") if isinstance(state.get("capacity"),dict) else {}
scenario=state.get("scenario") if isinstance(state.get("scenario"),dict) else {}
active_deals=[x for x in deals if x.get("stage") not in {"Won","Lost"}]
weighted=sum(num(x.get("value"))*num(x.get("probability"))/100 for x in active_deals)
overdue=[x for x in invoices if num(x.get("days_due"))>0 and x.get("status")!="Paid"]
low_stock=[x for x in inventory if num(x.get("stock"))<=num(x.get("reorder_point"))]
open_tickets=[x for x in tickets if x.get("status")!="Resolved"]
sla_risk=[x for x in open_tickets if num(x.get("age_hours"))>=.75*max(1,num(x.get("sla_hours")))]
order_risk=[x for x in orders if x.get("stage")!="Shipped" and num(x.get("hours_open"))>=.8*max(1,num(x.get("promise_hours")))]
risky_suppliers=[x for x in suppliers if x.get("risk")=="High" or num(x.get("on_time"))<80]
risky_customers=[x for x in customers if num(x.get("health"))<65 or num(x.get("open_tickets"))>=3]
risky_contracts=[x for x in contracts if num(x.get("risk_flags"))>=3 and x.get("status")!="Signed"]
blocked_campaigns=[x for x in campaigns if str(x.get("blocker") or "").strip()]
open_tasks=[x for x in tasks if x.get("status")!="Done"]
result=None

if action=="business-pulse":
    risk_count=len(sla_risk)+len(low_stock)+len(overdue)+len(risky_suppliers)+len(order_risk)+len(risky_contracts)
    rec=[]
    if overdue: rec.append(f"Collections needs attention: {money(sum(num(x.get('amount')) for x in overdue))} is overdue.")
    if low_stock: rec.append(f"Replenish {len(low_stock)} SKU(s) that are at or below reorder point.")
    if sla_risk: rec.append(f"Escalate {len(sla_risk)} support ticket(s) approaching SLA.")
    if order_risk: rec.append(f"Prioritize {len(order_risk)} fulfillment order(s) at promise risk.")
    result=pack("Cross-functional pulse complete.",{"weighted_pipeline":money(weighted),"overdue":money(sum(num(x.get('amount')) for x in overdue)),"risk_signals":risk_count,"customer_mrr":money(sum(num(x.get('mrr')) for x in customers))},rec)
elif action=="executive-brief":
    rec=["Protect cash by working the oldest receivables first.","Resolve customer/SLA risks before expanding campaign spend.","Fund inventory reorders only where lead-time coverage justifies it."]
    result=pack("Executive brief synthesized across sales, finance, operations and customer health.",{"pipeline":money(sum(num(x.get('value')) for x in active_deals)),"weighted_pipeline":money(weighted),"bank":money(num(cash.get('bank'))),"open_tasks":len(open_tasks),"high_risk_customers":len(risky_customers)},rec)
elif action=="revenue-forecast":
    mrr=sum(num(x.get("mrr")) for x in customers); base=mrr*3+weighted
    rec=[]
    for x in sorted(active_deals,key=lambda y:(num(y.get("days_idle")),num(y.get("value"))),reverse=True)[:4]: rec.append(f"Advance {x.get('company')} ({money(num(x.get('value')))}); {int(num(x.get('days_idle')))} days idle.")
    result=pack("90-day revenue outlook combines recurring revenue and probability-weighted pipeline.",{"90d_recurring":money(mrr*3),"weighted_new_business":money(weighted),"forecast_90d":money(base),"active_deals":len(active_deals)},rec)
elif action=="cash-forecast":
    bank=num(cash.get("bank")); inflow=num(cash.get("expected_collections_30d")); out=num(cash.get("expected_payments_30d"))+num(cash.get("monthly_fixed"))+num(cash.get("monthly_variable")); end=bank+inflow-out
    runway=bank/max(1,num(cash.get("monthly_fixed"))+num(cash.get("monthly_variable")))
    result=pack("30-day liquidity forecast computed from expected collections, payments and operating costs.",{"starting_cash":money(bank),"projected_30d":money(end),"monthly_burn":money(num(cash.get('monthly_fixed'))+num(cash.get('monthly_variable'))),"runway_months":round(runway,1)},["Pull forward overdue collections." if overdue else "Collections timing is not the primary risk.","Delay non-critical cash outflows if projected 30-day cash approaches the operating buffer."])
elif action=="inventory-plan":
    rec=[]; exposure=0
    for x in low_stock:
        target=max(num(x.get("reorder_point"))*1.5,num(x.get("daily_demand"))*(num(x.get("lead_days"))+14)); qty=max(0,round(target-num(x.get("stock")))); exposure+=qty
        rec.append(f"Reorder ~{qty} of {x.get('item')} from {x.get('supplier')}.")
    result=pack("Inventory plan identifies items below policy and estimates replenishment quantities.",{"skus":len(inventory),"below_reorder":len(low_stock),"suggested_units":int(exposure)},rec)
elif action=="support-triage":
    rec=[f"Escalate {x.get('id')} — {x.get('subject')} ({x.get('priority')})." for x in sorted(sla_risk,key=lambda y:num(y.get("age_hours"))/max(1,num(y.get("sla_hours"))),reverse=True)]
    result=pack("Support queue triaged by SLA consumption and priority.",{"open":len(open_tickets),"sla_risk":len(sla_risk),"urgent":sum(1 for x in open_tickets if x.get('priority')=='Urgent')},rec)
elif action=="customer-health":
    rec=[f"Intervene with {x.get('name')}: health {int(num(x.get('health')))}, {int(num(x.get('open_tickets')))} open tickets." for x in sorted(risky_customers,key=lambda y:num(y.get("health")))]
    avg=sum(num(x.get("health")) for x in customers)/max(1,len(customers))
    result=pack("Customer health scan combines health score, NPS and support load.",{"customers":len(customers),"average_health":round(avg,1),"at_risk":len(risky_customers),"mrr_at_risk":money(sum(num(x.get('mrr')) for x in risky_customers))},rec)
elif action=="procurement-risk":
    rec=[f"Review {x.get('name')}: {int(num(x.get('on_time')))}% on-time, {x.get('risk')} risk." for x in risky_suppliers]
    result=pack("Supplier portfolio reviewed for delivery reliability and committed exposure.",{"suppliers":len(suppliers),"high_risk":len(risky_suppliers),"open_po":money(sum(num(x.get('open_po')) for x in suppliers))},rec)
elif action=="fulfillment-risk":
    rec=[f"Prioritize {x.get('id')} for {x.get('customer')}; {int(num(x.get('hours_open')))}h open vs {int(num(x.get('promise_hours')))}h promise." for x in order_risk]
    result=pack("Fulfillment queue scored against customer promise windows.",{"open_orders":sum(1 for x in orders if x.get('stage')!='Shipped'),"promise_risk":len(order_risk),"open_units":int(sum(num(x.get('items')) for x in orders if x.get('stage')!='Shipped'))},rec)
elif action=="campaign-optimizer":
    rec=[]
    for x in campaigns:
        budget=max(1,num(x.get("budget"))); pace=num(x.get("spent"))/budget; progress=num(x.get("progress"))/100
        if x in blocked_campaigns: rec.append(f"Clear blocker on {x.get('campaign')}: {x.get('blocker')}.")
        if pace>progress+.15: rec.append(f"Slow or review {x.get('campaign')}; spend is ahead of progress.")
    result=pack("Campaign portfolio checked for blockers and spend/progress mismatch.",{"campaigns":len(campaigns),"live":sum(1 for x in campaigns if x.get('status')=='Live'),"blocked":len(blocked_campaigns),"spend":money(sum(num(x.get('spent')) for x in campaigns))},rec)
elif action=="contract-risk":
    rec=[f"Escalate {x.get('contract')} ({x.get('counterparty')}): {int(num(x.get('risk_flags')))} risk flags." for x in sorted(risky_contracts,key=lambda y:num(y.get("risk_flags")),reverse=True)]
    result=pack("Contract queue reviewed for concentration of unresolved risk flags.",{"active":sum(1 for x in contracts if x.get('status')!='Signed'),"high_risk":len(risky_contracts),"value_under_review":money(sum(num(x.get('value')) for x in contracts if x.get('status')!='Signed'))},rec)
elif action=="task-priority":
    scored=[]
    for x in open_tasks:
        urgency=max(0,5-num(x.get("days_due"))); score=num(x.get("impact"))*2+urgency-num(x.get("effort"))*.5; scored.append((score,x))
    scored.sort(key=lambda z:z[0],reverse=True)
    rec=[f"#{i+1} {x.get('title')} — score {round(score,1)} ({x.get('owner')})." for i,(score,x) in enumerate(scored[:6])]
    result=pack("Open work ranked by impact, due-date urgency and effort.",{"open_tasks":len(open_tasks),"overdue":sum(1 for x in open_tasks if num(x.get('days_due'))<0),"critical":sum(1 for x in open_tasks if x.get('priority')=='Critical')},rec)
elif action=="anomaly-scan":
    findings=[]
    for x in invoices:
        if num(x.get("days_due"))>30: findings.append(f"Invoice {x.get('id')} is >30 days overdue.")
    for x in inventory:
        if num(x.get("daily_demand"))*max(1,num(x.get("lead_days")))>num(x.get("stock")): findings.append(f"{x.get('item')} cannot cover lead-time demand.")
    for x in customers:
        if num(x.get("health"))<55: findings.append(f"{x.get('name')} has critical customer-health score {int(num(x.get('health')))}.")
    for x in campaigns:
        if num(x.get("spent"))>num(x.get("budget")): findings.append(f"{x.get('campaign')} is over budget.")
    result=pack("Anomaly scan searched financial, inventory, customer and campaign state for outliers.",{"anomalies":len(findings),"domains_scanned":4},["Investigate the highest-impact anomalies before the next operating cycle."],findings)
elif action=="capacity-plan":
    total=num(capacity.get("team_hours_week")); committed=num(capacity.get("committed_hours_week")); support=num(capacity.get("support_hours_week")); free=total-committed-support
    result=pack("Weekly capacity plan compares available team hours with committed and reactive load.",{"team_hours":int(total),"committed":int(committed),"support":int(support),"free_capacity":int(free),"utilization_pct":round((committed+support)/max(1,total)*100,1)},["Reduce low-impact commitments or add capacity." if free<0 else f"Keep roughly {int(free)} hours available for unplanned work."])
elif action=="scenario-simulation":
    growth=num(scenario.get("revenue_growth_pct"))/100; delay=num(scenario.get("collection_delay_days")); demand=num(scenario.get("demand_change_pct"))/100; cost=num(scenario.get("cost_change_pct"))/100
    mrr=sum(num(x.get("mrr")) for x in customers); projected_mrr=mrr*(1+growth); base_cost=num(cash.get("monthly_fixed"))+num(cash.get("monthly_variable")); projected_cost=base_cost*(1+cost); liquidity=num(cash.get("bank"))+num(cash.get("expected_collections_30d"))*(max(0,30-delay)/30)-num(cash.get("expected_payments_30d"))-projected_cost
    result=pack("Scenario model applied revenue growth, collection delay, demand and cost assumptions.",{"projected_mrr":money(projected_mrr),"projected_monthly_cost":money(projected_cost),"projected_30d_cash":money(liquidity),"inventory_demand_multiplier":round(1+demand,2)},["Increase reorder quantities selectively." if demand>.1 else "Inventory demand assumption is moderate.","Tighten collections cadence." if delay>10 else "Collection delay remains inside the modeled tolerance."])
else:
    result=pack(f"Unknown requested function: {action}",{"available_functions":15},["Choose one of the Command Center functions exposed in the UI."])

print(json.dumps({"result":result},separators=(",",":"),sort_keys=True))
'''

APP_CSS = r'''*{box-sizing:border-box}html,body,#app{margin:0;min-height:100%;font-family:Inter,ui-sans-serif,system-ui,-apple-system,sans-serif;background:#080a0f;color:#f4f6fb}button,input,select,textarea{font:inherit}button{cursor:pointer}.shell{min-height:100vh;display:grid;grid-template-columns:220px minmax(0,1fr)}aside{background:#0d1016;border-right:1px solid #232833;padding:18px 12px;position:sticky;top:0;height:100vh;overflow:auto}.brand{padding:5px 9px 18px;border-bottom:1px solid #242a35;margin-bottom:12px}.brand b{display:block;font-size:12px;letter-spacing:.12em}.brand span{display:block;color:#8a93a4;font-size:10px;margin-top:4px}.nav{display:grid;gap:4px}.nav button{border:0;background:transparent;color:#9ca5b5;text-align:left;padding:9px 10px;border-radius:9px}.nav button.active{background:#1a202a;color:#fff}.nav small{display:block;color:#687283;margin-top:2px}.temp{margin-top:18px;padding:10px;border:1px dashed #52446f;border-radius:10px;color:#9f91bb;font-size:10px}main{min-width:0;padding:26px 30px 60px;background:radial-gradient(circle at 78% -10%,#3e1e6b55,transparent 33%),#090b10}.top{display:flex;justify-content:space-between;gap:18px;align-items:flex-start;max-width:1500px;margin:auto}.eyebrow{color:#a78bfa;font-size:10px;font-weight:800;letter-spacing:.14em}.top h1{font-size:32px;margin:6px 0}.top p{color:#929bab;max-width:780px;margin:0;line-height:1.5}.actions{display:flex;gap:8px;flex-wrap:wrap}.btn{border:1px solid #323947;background:#151a22;color:#e6e9ef;border-radius:9px;padding:9px 12px}.btn.primary{background:#7c3aed;border-color:#8b5cf6;color:#fff}.btn.danger{color:#ffb4b4;border-color:#65363c;background:#28171a}.kpis{max-width:1500px;margin:22px auto;display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px}.kpi{border:1px solid #252c37;background:#11151c;border-radius:13px;padding:14px}.kpi span{display:block;color:#818a99;font-size:9px;text-transform:uppercase;letter-spacing:.09em}.kpi strong{display:block;margin-top:7px;font-size:21px}.panel{max-width:1500px;margin:auto;border:1px solid #252c37;background:#0f131a;border-radius:15px;overflow:hidden}.panel-head{display:flex;justify-content:space-between;gap:16px;align-items:center;padding:16px 18px;border-bottom:1px solid #242a35}.panel-head h2{margin:0;font-size:17px}.panel-head p{margin:4px 0 0;color:#808998;font-size:12px}.tools{display:flex;gap:8px;flex-wrap:wrap}.search{background:#090d13;color:#fff;border:1px solid #303744;border-radius:8px;padding:8px 10px}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;min-width:760px}th{text-align:left;color:#768091;font-size:9px;letter-spacing:.08em;text-transform:uppercase;padding:11px 13px;border-bottom:1px solid #242a35}td{padding:12px 13px;border-bottom:1px solid #1d232d;color:#d7dce5;font-size:12px}tr:hover{background:#151a22}.functions{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;padding:16px}.function{border:1px solid #2b3240;background:#121720;border-radius:12px;padding:14px;text-align:left;color:#e8ebf2}.function b{display:block}.function span{display:block;color:#818b9b;font-size:10px;margin-top:4px}.function:hover{border-color:#7655aa;background:#181322}.result{margin:0 16px 16px;border:1px solid #2c3440;background:#0a0e14;border-radius:12px;padding:16px}.result h3{margin:0 0 8px}.metric-row{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}.metric-chip{border:1px solid #2d3541;background:#141923;border-radius:9px;padding:9px 11px}.metric-chip span{display:block;color:#7e8797;font-size:9px}.metric-chip b{display:block;margin-top:3px}.result li{margin:7px 0;color:#cdd3dd}.scenario{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;padding:18px}.scenario label span{display:block;color:#8a93a3;font-size:10px;margin-bottom:6px}.scenario input{width:100%;background:#090d13;color:#fff;border:1px solid #303744;border-radius:8px;padding:9px}.modal-bg{position:fixed;inset:0;background:#000b;display:grid;place-items:center;padding:16px;z-index:50}.modal{width:min(720px,100%);max-height:90vh;overflow:auto;background:#11161e;border:1px solid #343c49;border-radius:16px;padding:20px}.modal h2{margin-top:0}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:11px}.grid label span{display:block;color:#87909f;font-size:10px;margin-bottom:5px}.grid input,.grid select{width:100%;background:#090d13;color:#fff;border:1px solid #303744;border-radius:8px;padding:9px}.modal-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:16px}.toast{position:fixed;right:16px;bottom:16px;background:#17261e;border:1px solid #356649;color:#c5f6d4;padding:10px 13px;border-radius:9px;z-index:80}.toast.bad{background:#291718;border-color:#6c383c;color:#ffc1c1}@media(max-width:900px){.shell{display:block}aside{position:static;height:auto;display:flex;gap:8px;overflow:auto;padding:9px}.brand,.temp{display:none}.nav{display:flex;width:max-content}.nav button{white-space:nowrap}.nav small{display:none}main{padding:18px 12px 45px}.top{display:block}.actions{margin-top:14px}.kpis{grid-template-columns:1fr 1fr}.kpi:last-child{grid-column:1/-1}.functions{grid-template-columns:1fr}.scenario{grid-template-columns:1fr 1fr}.grid{grid-template-columns:1fr}.panel-head{align-items:flex-start;flex-direction:column}.search{width:100%}}'''

APP_JS = r'''(()=>{
const root=document.getElementById("app");
const ACTIONS=[
 ["business-pulse","Business pulse","Cross-functional KPI and risk scan"],["executive-brief","Executive brief","Condensed operating brief"],["revenue-forecast","Revenue forecast","Recurring + weighted pipeline outlook"],["cash-forecast","Cash forecast","30-day liquidity and runway"],["inventory-plan","Inventory plan","Reorder and coverage recommendations"],["support-triage","Support triage","SLA and urgency prioritization"],["customer-health","Customer health","Churn and intervention signals"],["procurement-risk","Procurement risk","Supplier reliability and PO exposure"],["fulfillment-risk","Fulfillment risk","Promise-window and shipping risk"],["campaign-optimizer","Campaign optimizer","Spend pacing and blockers"],["contract-risk","Contract risk","Review risk and value exposure"],["task-priority","Task priority","Impact/urgency/effort ranking"],["anomaly-scan","Anomaly scan","Cross-domain outlier detection"],["capacity-plan","Capacity plan","Team load and available capacity"],["scenario-simulation","Scenario simulation","What-if model across growth, cash and demand"]];
const SCHEMAS={
 customers:{label:"Customers",fields:[["name","Customer","text"],["mrr","MRR","number"],["health","Health","number"],["nps","NPS","number"],["open_tickets","Open tickets","number"]]},
 deals:{label:"Deals",fields:[["company","Company","text"],["stage","Stage","text"],["value","Value","number"],["probability","Probability %","number"],["days_idle","Days idle","number"],["owner","Owner","text"]]},
 invoices:{label:"Invoices",fields:[["customer","Customer","text"],["amount","Amount","number"],["days_due","Days past due","number"],["status","Status","text"]]},
 inventory:{label:"Inventory",fields:[["item","Item","text"],["stock","On hand","number"],["reorder_point","Reorder point","number"],["daily_demand","Daily demand","number"],["lead_days","Lead days","number"],["supplier","Supplier","text"]]},
 orders:{label:"Orders",fields:[["customer","Customer","text"],["stage","Stage","text"],["items","Items","number"],["hours_open","Hours open","number"],["promise_hours","Promise hours","number"],["carrier","Carrier","text"]]},
 suppliers:{label:"Suppliers",fields:[["name","Supplier","text"],["open_po","Open PO","number"],["on_time","On-time %","number"],["lead_days","Lead days","number"],["risk","Risk","text"]]},
 tickets:{label:"Support",fields:[["subject","Ticket","text"],["customer","Customer","text"],["priority","Priority","text"],["status","Status","text"],["owner","Owner","text"],["sla_hours","SLA hours","number"],["age_hours","Age hours","number"]]},
 campaigns:{label:"Campaigns",fields:[["campaign","Campaign","text"],["channel","Channel","text"],["budget","Budget","number"],["spent","Spent","number"],["progress","Progress %","number"],["status","Status","text"],["blocker","Blocker","text"]]},
 contracts:{label:"Contracts",fields:[["contract","Contract","text"],["counterparty","Counterparty","text"],["value","Value","number"],["risk_flags","Risk flags","number"],["days_open","Days open","number"],["status","Status","text"]]},
 tasks:{label:"Tasks",fields:[["title","Task","text"],["owner","Owner","text"],["priority","Priority","text"],["status","Status","text"],["days_due","Days to due","number"],["impact","Impact","number"],["effort","Effort","number"]]}
};
let state={}; let tab="dashboard"; let query=""; let analysis=null; let busy=false; let editing=null;
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
const rid=()=>"r_"+Math.random().toString(36).slice(2,10);
function req(action,payload={}){return new Promise((resolve,reject)=>{const requestId=crypto.randomUUID();const timer=setTimeout(()=>{window.removeEventListener("message",on);reject(new Error("Operly bridge timed out"));},240000);function on(e){const m=e.data||{};if(m.source!=="operly.temp-app.response"||m.requestId!==requestId)return;clearTimeout(timer);window.removeEventListener("message",on);m.ok?resolve(m.data):reject(new Error(m.error||"Operly action failed"));}window.addEventListener("message",on);parent.postMessage({source:"operly.temp-app",requestId,action,payload},"*");});}
function toast(msg,bad=false){const el=document.createElement("div");el.className="toast"+(bad?" bad":"");el.textContent=msg;document.body.appendChild(el);setTimeout(()=>el.remove(),2600);}
function money(v){return "$"+Number(v||0).toLocaleString(undefined,{maximumFractionDigits:0});}
async function save(){await req("state.put",{state});}
function nav(){return [{id:"dashboard",label:"Dashboard",sub:"Whole business"},...Object.entries(SCHEMAS).map(([id,s])=>({id,label:s.label,sub:`${(state[id]||[]).length} records`})),{id:"analysis",label:"Functions",sub:"15 Sandbox functions"},{id:"scenario",label:"Scenario",sub:"What-if model"}];}
function kpis(){const customers=state.customers||[],deals=state.deals||[],invoices=state.invoices||[],tickets=state.tickets||[];const mrr=customers.reduce((a,x)=>a+Number(x.mrr||0),0);const pipe=deals.filter(x=>!["Won","Lost"].includes(x.stage)).reduce((a,x)=>a+Number(x.value||0)*Number(x.probability||0)/100,0);const overdue=invoices.filter(x=>Number(x.days_due)>0&&x.status!=="Paid").reduce((a,x)=>a+Number(x.amount||0),0);const risk=customers.filter(x=>Number(x.health)<65).length;const open=tickets.filter(x=>x.status!=="Resolved").length;return [["MRR",money(mrr)],["Weighted pipeline",money(pipe)],["Overdue",money(overdue)],["Customers at risk",risk],["Open tickets",open]];}
function shell(content){root.innerHTML=`<div class="shell"><aside><div class="brand"><b>OPERLY COMMAND CENTER</b><span>Maximal temporary plugin</span></div><div class="nav">${nav().map(n=>`<button data-nav="${n.id}" class="${tab===n.id?"active":""}">${esc(n.label)}<small>${esc(n.sub)}</small></button>`).join("")}</div><div class="temp">TEMPORARY · one plugin · persisted Workspace state · isolated Sandbox compute</div></aside><main><div class="top"><div><span class="eyebrow">MAXIMAL OPERLY PLUGIN TEST</span><h1>Command Center</h1><p>Customers, revenue, cash, inventory, fulfillment, suppliers, support, campaigns, contracts, tasks, capacity and scenario planning inside one installed Workspace plugin.</p></div><div class="actions"><button class="btn" id="export">Export JSON</button><button class="btn" id="import">Import JSON</button><button class="btn" id="reset">Reset demo</button><input id="file" type="file" accept="application/json" hidden></div></div><section class="kpis">${kpis().map(([a,b])=>`<div class="kpi"><span>${esc(a)}</span><strong>${esc(b)}</strong></div>`).join("")}</section>${content}</main></div>`;document.querySelectorAll("[data-nav]").forEach(b=>b.onclick=()=>{tab=b.dataset.nav;query="";render();});document.getElementById("reset").onclick=reset;document.getElementById("export").onclick=exportState;document.getElementById("import").onclick=()=>document.getElementById("file").click();document.getElementById("file").onchange=importState;}
function dashboard(){const cards=Object.entries(SCHEMAS).map(([id,s])=>`<button class="function" data-open="${id}"><b>${esc(s.label)}</b><span>${(state[id]||[]).length} persisted records · open module</span></button>`).join("");shell(`<section class="panel"><div class="panel-head"><div><h2>Business modules</h2><p>One plugin owns all of these Workspace-scoped datasets.</p></div><button class="btn primary" id="pulse">Run business pulse</button></div><div class="functions">${cards}</div></section>${analysis?resultView():""}`);document.querySelectorAll("[data-open]").forEach(b=>b.onclick=()=>{tab=b.dataset.open;render();});document.getElementById("pulse").onclick=()=>run("business-pulse");}
function moduleView(key){const s=SCHEMAS[key],rows=(state[key]||[]).filter(r=>!query||JSON.stringify(r).toLowerCase().includes(query.toLowerCase()));const heads=[["id","ID","text"],...s.fields];shell(`<section class="panel"><div class="panel-head"><div><h2>${esc(s.label)}</h2><p>CRUD edits persist in this plugin's isolated Workspace storage.</p></div><div class="tools"><input class="search" id="search" placeholder="Search…" value="${esc(query)}"><button class="btn primary" id="new">+ New</button></div></div><div class="table-wrap"><table><thead><tr>${heads.map(x=>`<th>${esc(x[1])}</th>`).join("")}<th></th></tr></thead><tbody>${rows.map(r=>`<tr>${heads.map(x=>`<td>${esc(r[x[0]])}</td>`).join("")}<td><button class="btn" data-edit="${esc(r.id)}">Edit</button></td></tr>`).join("")}</tbody></table></div></section>`);document.getElementById("new").onclick=()=>editRow(key,null);document.querySelectorAll("[data-edit]").forEach(b=>b.onclick=()=>editRow(key,(state[key]||[]).find(x=>x.id===b.dataset.edit)));document.getElementById("search").oninput=e=>{query=e.target.value;render();const el=document.getElementById("search");el.focus();el.setSelectionRange(query.length,query.length);};}
function editRow(key,row){const s=SCHEMAS[key];editing={key,row:row?{...row}:{id:rid()}};const bg=document.createElement("div");bg.className="modal-bg";bg.innerHTML=`<div class="modal"><h2>${row?"Edit":"New"} ${esc(s.label)}</h2><div class="grid"><label><span>ID</span><input data-f="id" value="${esc(editing.row.id)}"></label>${s.fields.map(([f,l,t])=>`<label><span>${esc(l)}</span><input data-f="${f}" type="${t}" value="${esc(editing.row[f]??"")}"></label>`).join("")}</div><div class="modal-actions">${row?'<button class="btn danger" id="delete">Delete</button>':""}<button class="btn" id="cancel">Cancel</button><button class="btn primary" id="save">Save</button></div></div>`;document.body.appendChild(bg);bg.querySelector("#cancel").onclick=()=>bg.remove();bg.querySelector("#save").onclick=async()=>{const next={};bg.querySelectorAll("[data-f]").forEach(i=>next[i.dataset.f]=i.type==="number"?Number(i.value):i.value);const list=[...(state[key]||[])];const ix=list.findIndex(x=>x.id===row?.id);if(ix>=0)list[ix]=next;else list.push(next);state[key]=list;await save();bg.remove();toast("Saved to plugin storage");render();};const del=bg.querySelector("#delete");if(del)del.onclick=async()=>{state[key]=(state[key]||[]).filter(x=>x.id!==row.id);await save();bg.remove();toast("Deleted");render();};}
function functionsView(){shell(`<section class="panel"><div class="panel-head"><div><h2>15 governed functions</h2><p>Each button persists the requested function, then invokes the plugin's isolated Sandbox capability through Operly.</p></div></div><div class="functions">${ACTIONS.map(([id,name,desc])=>`<button class="function" data-act="${id}"><b>${esc(name)}</b><span>${esc(desc)}</span></button>`).join("")}</div>${analysis?resultView():""}</section>`);document.querySelectorAll("[data-act]").forEach(b=>b.onclick=()=>run(b.dataset.act));}
function scenarioView(){const s=state.scenario||{};shell(`<section class="panel"><div class="panel-head"><div><h2>Scenario simulator</h2><p>Change assumptions, persist them, then run the governed scenario function.</p></div><button class="btn primary" id="runs">Run simulation</button></div><div class="scenario">${[["revenue_growth_pct","Revenue growth %"],["collection_delay_days","Collection delay days"],["demand_change_pct","Demand change %"],["cost_change_pct","Cost change %"]].map(([k,l])=>`<label><span>${l}</span><input data-s="${k}" type="number" value="${esc(s[k]??0)}"></label>`).join("")}</div>${analysis?resultView():""}</section>`);document.querySelectorAll("[data-s]").forEach(i=>i.onchange=async()=>{state.scenario={...(state.scenario||{}),[i.dataset.s]:Number(i.value)};await save();toast("Scenario saved");});document.getElementById("runs").onclick=()=>run("scenario-simulation");}
function resultView(){const r=analysis?.result||analysis||{};return `<div class="result"><h3>${esc(r.summary||"Analysis result")}</h3><div class="metric-row">${Object.entries(r.metrics||{}).map(([k,v])=>`<div class="metric-chip"><span>${esc(k.replaceAll("_"," "))}</span><b>${esc(v)}</b></div>`).join("")}</div>${(r.findings||[]).length?`<h4>Findings</h4><ul>${r.findings.map(x=>`<li>${esc(x)}</li>`).join("")}</ul>`:""}<h4>Recommendations</h4><ul>${(r.recommendations||[]).map(x=>`<li>${esc(x)}</li>`).join("")}</ul></div>`;}
async function run(action){if(busy)return;busy=true;try{state.requested_action=action;await save();toast("Launching isolated Sandbox…");const data=await req("capability.execute",{action:"analyze"});analysis=data.result||data;tab=(action==="scenario-simulation"?"scenario":tab);render();toast("Sandbox function completed");}catch(e){toast(e.message,true);}finally{busy=false;}}
async function reset(){if(!confirm("Reset all Command Center demo data?"))return;try{const d=await req("state.reset",{});state=d.state||{};analysis=null;render();toast("Demo reset");}catch(e){toast(e.message,true);}}
function exportState(){const blob=new Blob([JSON.stringify(state,null,2)],{type:"application/json"});const a=document.createElement("a");a.href=URL.createObjectURL(blob);a.download="operly-command-center-state.json";a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);}
async function importState(e){const file=e.target.files?.[0];if(!file)return;try{const data=JSON.parse(await file.text());if(!data||typeof data!=="object"||Array.isArray(data))throw new Error("Invalid state file");state=data;await save();analysis=null;render();toast("Imported and persisted");}catch(err){toast(err.message||"Import failed",true);}}
function render(){if(tab==="dashboard")dashboard();else if(tab==="analysis")functionsView();else if(tab==="scenario")scenarioView();else if(SCHEMAS[tab])moduleView(tab);else{tab="dashboard";dashboard();}}
async function init(){root.innerHTML='<div style="min-height:100vh;display:grid;place-items:center;color:#9ba3b2">Loading Command Center…</div>';try{const d=await req("state.get",{});state=d.state||{};render();}catch(e){root.innerHTML=`<div style="padding:40px;color:#ffb4b4">${esc(e.message)}</div>`;}}
init();
})();'''


def manifest() -> dict:
    return {
        "schema_version": "operly.plugin/v1",
        "plugin_id": PLUGIN_ID,
        "version": "1.0.0",
        "display_name": PLUGIN_NAME,
        "description": "A maximal temporary business command center spanning sales, finance, inventory, operations, support, marketing, legal, tasks, capacity and scenario planning.",
        "execution_mode": "sandbox_job",
        "capabilities": [{
            "id": f"{PLUGIN_ID}.analyze",
            "display_name": "Run Command Center function",
            "description": "Run one of fifteen Command Center analysis functions against the current Workspace-scoped plugin state in an isolated Operly Sandbox computer.",
            "input_schema": {"type": "object", "properties": {"action": {"type": "string"}, "state": {"type": "object", "additionalProperties": True}}, "required": ["action", "state"], "additionalProperties": False},
            "output_schema": {"type": "object", "properties": {"summary": {"type": "string"}, "metrics": {"type": "object", "additionalProperties": True}, "recommendations": {"type": "array", "items": {"type": "string"}}, "findings": {"type": "array", "items": {"type": "string"}}}, "required": ["summary", "metrics", "recommendations"], "additionalProperties": False},
            "permissions": [], "risk": "read_only", "approval_required": False, "reversible": False,
            "aliases": ["command center", "business operating console", "executive operations"], "emits": [], "tags": ["temporary", "demo", "maximal-plugin", "business-ops"],
        }],
        "permissions": [],
        "configuration_schema": {"type": "object", "properties": {"temporary_demo": {"type": "boolean"}, "demo_token_hash": {"type": "string"}, "demo_name": {"type": "string"}, "demo_category": {"type": "string"}, "demo_description": {"type": "string"}, "demo_seed": {"type": "object", "additionalProperties": True}}, "additionalProperties": False},
        "runtime": {"profile": "sandbox-job", "kind": "job", "network": {"mode": "off", "allowed_hosts": []}, "resources": {"cpu_millicores": 2000, "memory_mb": 3072, "disk_mb": 3072, "max_runtime_seconds": 300, "max_concurrency": 4}},
        "storage": [{"name": "app", "kind": "document", "quota_bytes": 4 * 1024 * 1024}],
        "credentials": [], "produces_events": [], "consumes_events": [], "requested_bindings": [],
        "ui": [{"contribution_type": "navigation", "id": f"{PLUGIN_ID}.home", "title": PLUGIN_NAME, "configuration": {"hosted_entry": "index.html", "temporary": True}}],
        "metadata": {"source": "operly-temp-functional-app-suite", "temporary": True, "remove_later": True, "maximal_test": True, "function_count": len(ACTIONS), "category": "Business OS", "hosted_entry": "index.html"},
    }


def package() -> tuple[dict, bytes]:
    m = manifest()
    index = """<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>Operly Command Center</title><link rel=\"stylesheet\" href=\"assets/app.css\"></head><body><div id=\"app\"></div><script src=\"assets/app.js\"></script></body></html>"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("operly.plugin.json", json.dumps(m, separators=(",", ":"), sort_keys=True))
        archive.writestr("operly_runtime.py", RUNTIME)
        archive.writestr("index.html", index)
        archive.writestr("assets/app.js", APP_JS)
        archive.writestr("assets/app.css", APP_CSS)
        archive.writestr("seed.json", json.dumps(SEED_STATE, indent=2))
        archive.writestr("FUNCTIONS.json", json.dumps([{"id": a, "name": n} for a, n in ACTIONS], indent=2))
        archive.writestr("TEMPORARY.md", "Temporary maximal Operly plugin test. Delete the containing Workspace after evaluation.\n")
    return m, buf.getvalue()


async def main() -> None:
    await suite.init_db()
    base_url = (suite.os.getenv("PUBLIC_BASE_URL") or "https://operly.dragonzpyder.xyz").strip().rstrip("/")
    workspace_id, workspace_slug, session_secret, csrf_secret = await suite._bootstrap_identity()
    demo_token = secrets.token_urlsafe(32)
    demo_token_hash = hashlib.sha256(demo_token.encode("utf-8")).hexdigest()
    headers = {"Origin": base_url, "X-CSRF-Token": csrf_secret, "User-Agent": "Operly-Temp-Command-Center/1"}
    cookies = {suite.PROD_SESSION_COOKIE: session_secret, suite.PROD_CSRF_COOKIE: csrf_secret}
    m, package_bytes = package()
    print("TEMP_COMMAND_CENTER_WORKSPACE", workspace_id, workspace_slug, flush=True)
    async with httpx.AsyncClient(base_url=base_url, headers=headers, cookies=cookies, timeout=180.0) as client:
        health = suite._json(await client.get("/api/health"))
        if not health.get("ok"): raise RuntimeError("Operly API is unhealthy")
        upload = suite._json(await client.post("/api/artifacts/upload", files={"files": (f"{PLUGIN_ID}.zip", package_bytes, "application/zip")}))
        published = suite._json(await client.post("/api/plugin-platform/packages", json={"manifest": m, "package_artifact_id": upload["artifact_ids"][0]}))
        installed = suite._json(await client.post("/api/plugin-platform/installations", json={"version_id": published["version_id"], "granted_permissions": [], "configuration": {"temporary_demo": True, "demo_token_hash": demo_token_hash, "demo_name": PLUGIN_NAME, "demo_category": "Business OS", "demo_description": m["description"], "demo_seed": SEED_STATE}}))
        installation_id = installed["installation_id"]
        await suite._wait_validation(client, installation_id, timeout=600.0)
        accepted = suite._json(await client.post(f"/api/plugin-platform/installations/{installation_id}/runtime/reconcile", json={}))
        runtime = await suite._wait_runtime(client, installation_id, timeout=600.0)
        active = suite._json(await client.patch(f"/api/plugin-platform/installations/{installation_id}", json={"status": "active", "enabled": True}))
        if not active.get("enabled"): raise RuntimeError("Command Center failed to activate")
        demo_headers = {"X-Operly-Demo-Token": demo_token, "Origin": base_url}
        suite._json(await client.put(f"/api/public/plugin-demos/{workspace_id}/{PLUGIN_ID}/state", headers=demo_headers, cookies={}, json={"state": SEED_STATE}))
        hosted = await client.get(f"/api/public/plugins/{workspace_id}/{PLUGIN_ID}/", cookies={})
        asset = await client.get(f"/api/public/plugins/{workspace_id}/{PLUGIN_ID}/assets/app.js", cookies={})
        if hosted.status_code != 200 or "Operly Command Center" not in hosted.text: raise RuntimeError(f"Hosted Command Center UI failed: HTTP {hosted.status_code}")
        if asset.status_code != 200 or "scenario-simulation" not in asset.text: raise RuntimeError("Command Center JS asset failed")

        async def run_action(action: str) -> dict:
            state = json.loads(json.dumps(SEED_STATE))
            state["requested_action"] = action
            suite._json(await client.put(f"/api/public/plugin-demos/{workspace_id}/{PLUGIN_ID}/state", headers=demo_headers, cookies={}, json={"state": state}))
            response = await client.post(f"/api/public/plugin-demos/{workspace_id}/{PLUGIN_ID}/execute", headers=demo_headers, cookies={}, json={"action": "analyze"})
            data = suite._json(response)
            result = data.get("result") or {}
            if not result.get("summary"): raise RuntimeError(f"Missing result for {action}")
            print("TEMP_COMMAND_CENTER_FUNCTION_PASS", json.dumps({"action": action, "run_id": data.get("run_id")}, sort_keys=True), flush=True)
            return {"action": action, "run_id": data.get("run_id"), "summary": result.get("summary")}

        results = []
        for action, _ in ACTIONS:
            results.append(await run_action(action))
        lab_url = f"{base_url}/temp-app-lab/{workspace_id}?token={demo_token}"
        print("TEMP_COMMAND_CENTER_RESULT", json.dumps({"status": "PASS", "workspace_id": workspace_id, "workspace_slug": workspace_slug, "plugin_id": PLUGIN_ID, "installation_id": installation_id, "reconcile_job_id": accepted.get("job_id"), "runtime_provider": runtime.get("provider"), "function_count": len(results), "function_pass_count": len(results), "lab_url": lab_url, "hosted_url": f"{base_url}/api/public/plugins/{workspace_id}/{PLUGIN_ID}/", "functions": results}, sort_keys=True), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
