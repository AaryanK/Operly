from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import secrets
import time
import zipfile
from datetime import datetime, timedelta
from uuid import uuid4

import httpx

from apps.api.auth_cookies import PROD_CSRF_COOKIE, PROD_SESSION_COOKIE
from apps.api.security import hash_token, random_token
from packages.database.db import SessionFactory, init_db
from packages.database.models import AppUser, AuthSession, Tenant, TenantMember

APP_SPECS = json.loads(r'''[
  {
    "id": "temp.support-desk",
    "name": "Support Desk",
    "category": "Support",
    "description": "Run a practical support queue with SLA risk, ownership, priorities and resolution tracking.",
    "kind": "support",
    "cpu": 500,
    "memory": 768,
    "fields": [
      {"key": "subject", "label": "Ticket", "type": "text", "required": true},
      {"key": "customer", "label": "Customer", "type": "text", "required": true},
      {"key": "priority", "label": "Priority", "type": "select", "options": ["Urgent", "High", "Normal", "Low"]},
      {"key": "status", "label": "Status", "type": "select", "options": ["New", "In progress", "Waiting", "Resolved"]},
      {"key": "owner", "label": "Owner", "type": "text"},
      {"key": "sla_hours", "label": "SLA hours", "type": "number"},
      {"key": "age_hours", "label": "Age hours", "type": "number"}
    ],
    "seed": [
      {"id": "T-1048", "subject": "Checkout fails on mobile", "customer": "Northstar Dental", "priority": "Urgent", "status": "New", "owner": "Maya", "sla_hours": 4, "age_hours": 3.2},
      {"id": "T-1047", "subject": "Export missing March records", "customer": "Apex Foods", "priority": "High", "status": "In progress", "owner": "Jon", "sla_hours": 12, "age_hours": 8},
      {"id": "T-1044", "subject": "Need teammate role changed", "customer": "Harbor Studio", "priority": "Normal", "status": "Waiting", "owner": "Rina", "sla_hours": 24, "age_hours": 19},
      {"id": "T-1039", "subject": "Invoice receipt duplicate", "customer": "Bluebird Labs", "priority": "Low", "status": "Resolved", "owner": "Maya", "sla_hours": 48, "age_hours": 10}
    ]
  },
  {
    "id": "temp.receivables",
    "name": "Receivables",
    "category": "Finance",
    "description": "Track invoices, aging, overdue exposure and collection follow-ups.",
    "kind": "receivables",
    "cpu": 500,
    "memory": 768,
    "fields": [
      {"key": "customer", "label": "Customer", "type": "text", "required": true},
      {"key": "invoice", "label": "Invoice", "type": "text", "required": true},
      {"key": "amount", "label": "Amount", "type": "number"},
      {"key": "days_due", "label": "Days past due", "type": "number"},
      {"key": "status", "label": "Status", "type": "select", "options": ["Open", "Overdue", "Promise to pay", "Paid"]},
      {"key": "owner", "label": "Owner", "type": "text"}
    ],
    "seed": [
      {"id": "INV-2408", "customer": "Mason Retail", "invoice": "INV-2408", "amount": 8420, "days_due": 18, "status": "Overdue", "owner": "Ari"},
      {"id": "INV-2411", "customer": "Luna Fitness", "invoice": "INV-2411", "amount": 3150, "days_due": 5, "status": "Overdue", "owner": "Ari"},
      {"id": "INV-2420", "customer": "Redwood Cafe", "invoice": "INV-2420", "amount": 6725, "days_due": -8, "status": "Open", "owner": "Sam"},
      {"id": "INV-2397", "customer": "Vertex Build", "invoice": "INV-2397", "amount": 11900, "days_due": 31, "status": "Promise to pay", "owner": "Sam"}
    ]
  },
  {
    "id": "temp.inventory-planner",
    "name": "Inventory Planner",
    "category": "Operations",
    "description": "Monitor stock coverage and generate practical reorder recommendations.",
    "kind": "inventory",
    "cpu": 750,
    "memory": 1024,
    "fields": [
      {"key": "sku", "label": "SKU", "type": "text", "required": true},
      {"key": "item", "label": "Item", "type": "text", "required": true},
      {"key": "stock", "label": "On hand", "type": "number"},
      {"key": "reorder_point", "label": "Reorder point", "type": "number"},
      {"key": "daily_demand", "label": "Daily demand", "type": "number"},
      {"key": "lead_days", "label": "Lead days", "type": "number"},
      {"key": "supplier", "label": "Supplier", "type": "text"}
    ],
    "seed": [
      {"id": "SKU-100", "sku": "SKU-100", "item": "12oz compostable cups", "stock": 380, "reorder_point": 420, "daily_demand": 55, "lead_days": 9, "supplier": "EcoServe"},
      {"id": "SKU-214", "sku": "SKU-214", "item": "Paper carry bags", "stock": 980, "reorder_point": 600, "daily_demand": 72, "lead_days": 6, "supplier": "PackCo"},
      {"id": "SKU-330", "sku": "SKU-330", "item": "PLA cutlery sets", "stock": 145, "reorder_point": 300, "daily_demand": 36, "lead_days": 12, "supplier": "GreenWare"},
      {"id": "SKU-501", "sku": "SKU-501", "item": "Thermal labels", "stock": 860, "reorder_point": 400, "daily_demand": 28, "lead_days": 5, "supplier": "LabelWorks"}
    ]
  },
  {
    "id": "temp.lead-pipeline",
    "name": "Lead Pipeline",
    "category": "Sales",
    "description": "Manage prospects, next actions and weighted pipeline without losing follow-up.",
    "kind": "leads",
    "cpu": 500,
    "memory": 768,
    "fields": [
      {"key": "company", "label": "Company", "type": "text", "required": true},
      {"key": "contact", "label": "Contact", "type": "text"},
      {"key": "stage", "label": "Stage", "type": "select", "options": ["New", "Qualified", "Proposal", "Negotiation", "Won", "Lost"]},
      {"key": "value", "label": "Deal value", "type": "number"},
      {"key": "probability", "label": "Probability %", "type": "number"},
      {"key": "days_idle", "label": "Days idle", "type": "number"},
      {"key": "owner", "label": "Owner", "type": "text"}
    ],
    "seed": [
      {"id": "L-101", "company": "Everwell Clinics", "contact": "Priya Shah", "stage": "Proposal", "value": 18000, "probability": 65, "days_idle": 2, "owner": "Nina"},
      {"id": "L-102", "company": "Sunline Foods", "contact": "Marco Ruiz", "stage": "Qualified", "value": 9200, "probability": 40, "days_idle": 9, "owner": "Dev"},
      {"id": "L-103", "company": "Atlas Repair", "contact": "Leo Park", "stage": "Negotiation", "value": 27000, "probability": 80, "days_idle": 1, "owner": "Nina"},
      {"id": "L-104", "company": "Bloom Events", "contact": "Kara Young", "stage": "New", "value": 6400, "probability": 20, "days_idle": 12, "owner": "Dev"}
    ]
  },
  {
    "id": "temp.procurement-hub",
    "name": "Procurement Hub",
    "category": "Procurement",
    "description": "Track purchase orders, supplier performance and delivery risk.",
    "kind": "procurement",
    "cpu": 750,
    "memory": 1024,
    "fields": [
      {"key": "po", "label": "PO", "type": "text", "required": true},
      {"key": "supplier", "label": "Supplier", "type": "text", "required": true},
      {"key": "amount", "label": "Amount", "type": "number"},
      {"key": "status", "label": "Status", "type": "select", "options": ["Draft", "Approved", "Ordered", "In transit", "Received"]},
      {"key": "days_to_due", "label": "Days to due", "type": "number"},
      {"key": "risk", "label": "Risk", "type": "select", "options": ["Low", "Medium", "High"]}
    ],
    "seed": [
      {"id": "PO-884", "po": "PO-884", "supplier": "EcoServe", "amount": 12800, "status": "In transit", "days_to_due": 2, "risk": "Medium"},
      {"id": "PO-887", "po": "PO-887", "supplier": "GreenWare", "amount": 7600, "status": "Ordered", "days_to_due": -1, "risk": "High"},
      {"id": "PO-890", "po": "PO-890", "supplier": "PackCo", "amount": 4200, "status": "Approved", "days_to_due": 8, "risk": "Low"},
      {"id": "PO-876", "po": "PO-876", "supplier": "LabelWorks", "amount": 2900, "status": "Received", "days_to_due": -4, "risk": "Low"}
    ]
  },
  {
    "id": "temp.fulfillment-board",
    "name": "Fulfillment Board",
    "category": "Operations",
    "description": "Move orders through pick, pack and ship while spotting delay risk.",
    "kind": "fulfillment",
    "cpu": 500,
    "memory": 768,
    "fields": [
      {"key": "order", "label": "Order", "type": "text", "required": true},
      {"key": "customer", "label": "Customer", "type": "text", "required": true},
      {"key": "stage", "label": "Stage", "type": "select", "options": ["Queued", "Picking", "Packing", "Ready", "Shipped"]},
      {"key": "items", "label": "Items", "type": "number"},
      {"key": "hours_open", "label": "Hours open", "type": "number"},
      {"key": "promise_hours", "label": "Promise hours", "type": "number"},
      {"key": "carrier", "label": "Carrier", "type": "text"}
    ],
    "seed": [
      {"id": "SO-5512", "order": "SO-5512", "customer": "Redwood Cafe", "stage": "Packing", "items": 18, "hours_open": 22, "promise_hours": 24, "carrier": "UPS"},
      {"id": "SO-5518", "order": "SO-5518", "customer": "Luna Fitness", "stage": "Queued", "items": 7, "hours_open": 5, "promise_hours": 18, "carrier": "FedEx"},
      {"id": "SO-5509", "order": "SO-5509", "customer": "Mason Retail", "stage": "Ready", "items": 32, "hours_open": 29, "promise_hours": 24, "carrier": "UPS"},
      {"id": "SO-5501", "order": "SO-5501", "customer": "Atlas Repair", "stage": "Shipped", "items": 4, "hours_open": 11, "promise_hours": 18, "carrier": "USPS"}
    ]
  },
  {
    "id": "temp.campaign-planner",
    "name": "Campaign Planner",
    "category": "Marketing",
    "description": "Plan launches, budgets, channel readiness and blockers in one place.",
    "kind": "campaigns",
    "cpu": 500,
    "memory": 768,
    "fields": [
      {"key": "campaign", "label": "Campaign", "type": "text", "required": true},
      {"key": "channel", "label": "Channel", "type": "select", "options": ["Email", "Paid social", "Organic", "Events", "Search"]},
      {"key": "budget", "label": "Budget", "type": "number"},
      {"key": "spent", "label": "Spent", "type": "number"},
      {"key": "progress", "label": "Progress %", "type": "number"},
      {"key": "status", "label": "Status", "type": "select", "options": ["Planning", "Ready", "Live", "Paused", "Complete"]},
      {"key": "blocker", "label": "Blocker", "type": "text"}
    ],
    "seed": [
      {"id": "C-21", "campaign": "Fall wholesale push", "channel": "Email", "budget": 4500, "spent": 1900, "progress": 68, "status": "Live", "blocker": ""},
      {"id": "C-22", "campaign": "Local restaurant demo", "channel": "Events", "budget": 8200, "spent": 3100, "progress": 52, "status": "Planning", "blocker": "Venue creative pending"},
      {"id": "C-23", "campaign": "Eco packaging search", "channel": "Search", "budget": 6200, "spent": 5900, "progress": 76, "status": "Live", "blocker": "CPA above target"},
      {"id": "C-24", "campaign": "Customer referral loop", "channel": "Organic", "budget": 1200, "spent": 350, "progress": 85, "status": "Ready", "blocker": ""}
    ]
  },
  {
    "id": "temp.contract-review",
    "name": "Contract Review",
    "category": "Legal Ops",
    "description": "Track contract risk flags, review progress and approval readiness.",
    "kind": "contracts",
    "cpu": 1000,
    "memory": 1536,
    "fields": [
      {"key": "contract", "label": "Contract", "type": "text", "required": true},
      {"key": "counterparty", "label": "Counterparty", "type": "text", "required": true},
      {"key": "value", "label": "Value", "type": "number"},
      {"key": "risk_flags", "label": "Risk flags", "type": "number"},
      {"key": "status", "label": "Status", "type": "select", "options": ["Intake", "Review", "Redline", "Approval", "Signed"]},
      {"key": "owner", "label": "Owner", "type": "text"},
      {"key": "days_open", "label": "Days open", "type": "number"}
    ],
    "seed": [
      {"id": "CTR-88", "contract": "Packaging supply MSA", "counterparty": "GreenWare", "value": 87000, "risk_flags": 3, "status": "Redline", "owner": "Rina", "days_open": 9},
      {"id": "CTR-91", "contract": "Warehouse lease addendum", "counterparty": "Parkside LLC", "value": 42000, "risk_flags": 1, "status": "Approval", "owner": "Jon", "days_open": 5},
      {"id": "CTR-93", "contract": "Agency SOW", "counterparty": "Brightline", "value": 18000, "risk_flags": 5, "status": "Review", "owner": "Rina", "days_open": 12},
      {"id": "CTR-84", "contract": "Carrier agreement", "counterparty": "RapidShip", "value": 33000, "risk_flags": 0, "status": "Signed", "owner": "Jon", "days_open": 3}
    ]
  },
  {
    "id": "temp.data-reconcile",
    "name": "Data Reconcile",
    "category": "Data",
    "description": "Resolve mismatched records across systems with confidence and source choices.",
    "kind": "reconcile",
    "cpu": 1000,
    "memory": 1536,
    "fields": [
      {"key": "record", "label": "Record", "type": "text", "required": true},
      {"key": "source_a", "label": "Source A", "type": "text"},
      {"key": "source_b", "label": "Source B", "type": "text"},
      {"key": "field", "label": "Field", "type": "text"},
      {"key": "confidence", "label": "Confidence %", "type": "number"},
      {"key": "status", "label": "Status", "type": "select", "options": ["Mismatch", "Reviewing", "Resolved", "Ignored"]}
    ],
    "seed": [
      {"id": "R-300", "record": "Mason Retail", "source_a": "CRM: Wichita", "source_b": "Billing: Derby", "field": "Address", "confidence": 72, "status": "Mismatch"},
      {"id": "R-301", "record": "SKU-330", "source_a": "ERP: 145", "source_b": "Warehouse: 131", "field": "On hand", "confidence": 94, "status": "Reviewing"},
      {"id": "R-302", "record": "INV-2411", "source_a": "Billing: Open", "source_b": "Bank: $3,150", "field": "Payment", "confidence": 88, "status": "Mismatch"},
      {"id": "R-303", "record": "Everwell Clinics", "source_a": "CRM: Priya", "source_b": "Marketing: Priya S.", "field": "Contact", "confidence": 97, "status": "Resolved"}
    ]
  },
  {
    "id": "temp.customer-health",
    "name": "Customer Health",
    "category": "Customer Success",
    "description": "Track renewal risk, health scores and intervention priorities across accounts.",
    "kind": "customer_health",
    "cpu": 500,
    "memory": 768,
    "fields": [
      {"key": "account", "label": "Account", "type": "text", "required": true},
      {"key": "arr", "label": "ARR", "type": "number"},
      {"key": "health", "label": "Health score", "type": "number"},
      {"key": "days_to_renewal", "label": "Days to renewal", "type": "number"},
      {"key": "open_issues", "label": "Open issues", "type": "number"},
      {"key": "owner", "label": "Owner", "type": "text"},
      {"key": "status", "label": "Status", "type": "select", "options": ["Healthy", "Watch", "At risk", "Renewed"]}
    ],
    "seed": [
      {"id": "A-11", "account": "Northstar Dental", "arr": 24000, "health": 82, "days_to_renewal": 74, "open_issues": 1, "owner": "Maya", "status": "Healthy"},
      {"id": "A-12", "account": "Mason Retail", "arr": 42000, "health": 58, "days_to_renewal": 29, "open_issues": 3, "owner": "Dev", "status": "Watch"},
      {"id": "A-13", "account": "Apex Foods", "arr": 36000, "health": 41, "days_to_renewal": 18, "open_issues": 5, "owner": "Maya", "status": "At risk"},
      {"id": "A-14", "account": "Luna Fitness", "arr": 18000, "health": 90, "days_to_renewal": 112, "open_issues": 0, "owner": "Dev", "status": "Healthy"}
    ]
  },
  {
    "id": "temp.ops-board",
    "name": "Ops Board",
    "category": "Team",
    "description": "Coordinate operational work, owners, due dates and blocked tasks.",
    "kind": "tasks",
    "cpu": 500,
    "memory": 768,
    "fields": [
      {"key": "task", "label": "Task", "type": "text", "required": true},
      {"key": "owner", "label": "Owner", "type": "text"},
      {"key": "priority", "label": "Priority", "type": "select", "options": ["Critical", "High", "Normal", "Low"]},
      {"key": "status", "label": "Status", "type": "select", "options": ["Backlog", "In progress", "Blocked", "Done"]},
      {"key": "days_to_due", "label": "Days to due", "type": "number"},
      {"key": "estimate_hours", "label": "Estimate hours", "type": "number"}
    ],
    "seed": [
      {"id": "OP-51", "task": "Cycle-count compostable cup stock", "owner": "Sam", "priority": "High", "status": "In progress", "days_to_due": 1, "estimate_hours": 2},
      {"id": "OP-52", "task": "Confirm PO-887 revised ETA", "owner": "Rina", "priority": "Critical", "status": "Blocked", "days_to_due": 0, "estimate_hours": 1},
      {"id": "OP-53", "task": "Reconcile August carrier fees", "owner": "Jon", "priority": "Normal", "status": "Backlog", "days_to_due": 4, "estimate_hours": 3},
      {"id": "OP-54", "task": "Publish new wholesale price sheet", "owner": "Maya", "priority": "Normal", "status": "Done", "days_to_due": -1, "estimate_hours": 2}
    ]
  },
  {
    "id": "temp.cash-forecast",
    "name": "Cash Forecast",
    "category": "Finance",
    "description": "Model near-term cash movements and flag weeks where liquidity gets tight.",
    "kind": "cash",
    "cpu": 750,
    "memory": 1024,
    "fields": [
      {"key": "label", "label": "Cash item", "type": "text", "required": true},
      {"key": "week", "label": "Week", "type": "number"},
      {"key": "type", "label": "Type", "type": "select", "options": ["Inflow", "Outflow"]},
      {"key": "amount", "label": "Amount", "type": "number"},
      {"key": "confidence", "label": "Confidence %", "type": "number"},
      {"key": "status", "label": "Status", "type": "select", "options": ["Expected", "Committed", "Received", "Paid"]}
    ],
    "seed": [
      {"id": "CF-1", "label": "Wholesale customer receipts", "week": 1, "type": "Inflow", "amount": 28400, "confidence": 90, "status": "Expected"},
      {"id": "CF-2", "label": "Payroll", "week": 1, "type": "Outflow", "amount": 18600, "confidence": 100, "status": "Committed"},
      {"id": "CF-3", "label": "Supplier PO-884", "week": 2, "type": "Outflow", "amount": 12800, "confidence": 100, "status": "Committed"},
      {"id": "CF-4", "label": "New customer deposits", "week": 2, "type": "Inflow", "amount": 17200, "confidence": 65, "status": "Expected"},
      {"id": "CF-5", "label": "Rent + utilities", "week": 3, "type": "Outflow", "amount": 7400, "confidence": 100, "status": "Committed"},
      {"id": "CF-6", "label": "Retail settlements", "week": 3, "type": "Inflow", "amount": 21600, "confidence": 85, "status": "Expected"}
    ]
  }
]''')

APP_JS = r'''(() => {
  const cfg = JSON.parse(document.getElementById("operly-config").textContent);
  const root = document.getElementById("app");
  let state = {records: []};
  let selectedTab = "overview";
  let query = "";
  let analysis = null;
  let busy = false;
  const pending = new Map();

  function request(action, payload={}) {
    const requestId = crypto.randomUUID();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { pending.delete(requestId); reject(new Error("Operly bridge timed out")); }, 45000);
      pending.set(requestId, {resolve, reject, timer});
      parent.postMessage({source:"operly.temp-app", requestId, action, payload}, "*");
    });
  }
  addEventListener("message", (event) => {
    const msg = event.data || {};
    if (msg.source !== "operly.temp-app.response" || !pending.has(msg.requestId)) return;
    const item = pending.get(msg.requestId); pending.delete(msg.requestId); clearTimeout(item.timer);
    msg.ok ? item.resolve(msg.data) : item.reject(new Error(msg.error || "Operly action failed"));
  });

  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const n = (v) => Number(v || 0);
  const money = (v) => new Intl.NumberFormat("en-US",{style:"currency",currency:"USD",maximumFractionDigits:0}).format(n(v));
  const records = () => Array.isArray(state.records) ? state.records : [];

  function metrics() {
    const r = records();
    if (!r.length) return [["Records","0"],["Needs attention","0"],["Active","0"]];
    switch(cfg.kind) {
      case "support": return [["Open", r.filter(x=>x.status!=="Resolved").length],["Urgent",r.filter(x=>x.priority==="Urgent"&&x.status!=="Resolved").length],["SLA risk",r.filter(x=>x.status!=="Resolved"&&n(x.age_hours)>=n(x.sla_hours)*.75).length]];
      case "receivables": return [["Outstanding",money(r.filter(x=>x.status!=="Paid").reduce((a,x)=>a+n(x.amount),0))],["Overdue",money(r.filter(x=>n(x.days_due)>0&&x.status!=="Paid").reduce((a,x)=>a+n(x.amount),0))],["31+ days",r.filter(x=>n(x.days_due)>=31&&x.status!=="Paid").length]];
      case "inventory": return [["SKUs",r.length],["Below reorder",r.filter(x=>n(x.stock)<=n(x.reorder_point)).length],["Days cover",Math.round(r.reduce((a,x)=>a+(n(x.stock)/Math.max(1,n(x.daily_demand))),0)/r.length)]];
      case "leads": return [["Pipeline",money(r.filter(x=>!["Won","Lost"].includes(x.stage)).reduce((a,x)=>a+n(x.value),0))],["Weighted",money(r.reduce((a,x)=>a+n(x.value)*n(x.probability)/100,0))],["Stale",r.filter(x=>n(x.days_idle)>=7&&!["Won","Lost"].includes(x.stage)).length]];
      case "procurement": return [["Open POs",r.filter(x=>x.status!=="Received").length],["Committed",money(r.filter(x=>x.status!=="Received").reduce((a,x)=>a+n(x.amount),0))],["Late/risky",r.filter(x=>x.status!=="Received"&&(n(x.days_to_due)<0||x.risk==="High")).length]];
      case "fulfillment": return [["Open orders",r.filter(x=>x.stage!=="Shipped").length],["Units open",r.filter(x=>x.stage!=="Shipped").reduce((a,x)=>a+n(x.items),0)],["Promise risk",r.filter(x=>x.stage!=="Shipped"&&n(x.hours_open)>=n(x.promise_hours)*.8).length]];
      case "campaigns": return [["Active budget",money(r.filter(x=>["Ready","Live"].includes(x.status)).reduce((a,x)=>a+n(x.budget),0))],["Spent",money(r.reduce((a,x)=>a+n(x.spent),0))],["Blocked",r.filter(x=>String(x.blocker||"").trim()).length]];
      case "contracts": return [["In review",r.filter(x=>!["Signed"].includes(x.status)).length],["Value",money(r.reduce((a,x)=>a+n(x.value),0))],["High risk",r.filter(x=>n(x.risk_flags)>=3&&x.status!=="Signed").length]];
      case "reconcile": return [["Mismatches",r.filter(x=>x.status==="Mismatch").length],["Resolved",r.filter(x=>x.status==="Resolved").length],["90%+ confidence",r.filter(x=>n(x.confidence)>=90&&x.status!=="Resolved").length]];
      case "customer_health": return [["ARR",money(r.reduce((a,x)=>a+n(x.arr),0))],["At risk",r.filter(x=>x.status==="At risk"||n(x.health)<50).length],["Renew <30d",r.filter(x=>n(x.days_to_renewal)<=30&&x.status!=="Renewed").length]];
      case "tasks": return [["Open",r.filter(x=>x.status!=="Done").length],["Blocked",r.filter(x=>x.status==="Blocked").length],["Due/overdue",r.filter(x=>x.status!=="Done"&&n(x.days_to_due)<=1).length]];
      case "cash": {
        const inflow=r.filter(x=>x.type==="Inflow").reduce((a,x)=>a+n(x.amount)*n(x.confidence)/100,0);
        const outflow=r.filter(x=>x.type==="Outflow").reduce((a,x)=>a+n(x.amount),0);
        return [["Expected inflow",money(inflow)],["Committed outflow",money(outflow)],["Net forecast",money(inflow-outflow)]];
      }
      default: return [["Records",r.length],["Active",r.filter(x=>x.status!=="Done").length],["Updated","Now"]];
    }
  }

  function rowText(x){ return cfg.fields.map(f=>String(x[f.key]??"")).join(" ").toLowerCase(); }
  function filtered(){ const q=query.trim().toLowerCase(); return q ? records().filter(x=>rowText(x).includes(q)) : records(); }

  async function save(next) {
    state = next; render();
    try { await request("state.put", {state}); } catch(e) { toast(e.message, true); }
  }
  function toast(text, bad=false) {
    const el=document.createElement("div"); el.className=`toast ${bad?"bad":""}`; el.textContent=text; document.body.appendChild(el);
    setTimeout(()=>el.remove(),2600);
  }
  function inputFor(f, value="") {
    if(f.type==="select") return `<select name="${esc(f.key)}">${(f.options||[]).map(o=>`<option ${String(value)===String(o)?"selected":""}>${esc(o)}</option>`).join("")}</select>`;
    return `<input name="${esc(f.key)}" type="${f.type==="number"?"number":"text"}" value="${esc(value)}" ${f.required?"required":""} />`;
  }
  function openEditor(record=null) {
    const overlay=document.createElement("div"); overlay.className="modal-wrap";
    overlay.innerHTML=`<form class="modal"><div class="modal-head"><div><span class="eyebrow">${record?"Edit":"New"} record</span><h2>${esc(cfg.name)}</h2></div><button type="button" class="icon close">×</button></div>
      <div class="form-grid">${cfg.fields.map(f=>`<label><span>${esc(f.label)}</span>${inputFor(f, record?.[f.key])}</label>`).join("")}</div>
      <div class="modal-actions">${record?'<button type="button" class="danger delete">Delete</button>':""}<span></span><button type="button" class="ghost close2">Cancel</button><button class="primary">Save</button></div></form>`;
    document.body.appendChild(overlay);
    const close=()=>overlay.remove();
    overlay.querySelector(".close").onclick=close; overlay.querySelector(".close2").onclick=close;
    if(record) overlay.querySelector(".delete").onclick=async()=>{ await save({...state,records:records().filter(x=>x.id!==record.id)}); close(); toast("Record deleted"); };
    overlay.querySelector("form").onsubmit=async(e)=>{
      e.preventDefault(); const fd=new FormData(e.currentTarget); const out={id:record?.id||`${cfg.id.split(".").pop().toUpperCase()}-${Date.now().toString().slice(-6)}`};
      cfg.fields.forEach(f=>{ const val=fd.get(f.key); out[f.key]=f.type==="number"?Number(val||0):String(val||""); });
      const next=record?records().map(x=>x.id===record.id?out:x):[out,...records()];
      await save({...state,records:next}); close(); toast(record?"Record updated":"Record added");
    };
  }

  async function runAnalysis() {
    busy=true; analysis=null; render();
    try {
      const data=await request("capability.execute",{action:"analyze"});
      analysis=data.result || data;
      selectedTab="analysis"; toast("Sandbox analysis complete");
    } catch(e){ toast(e.message,true); analysis={summary:"Analysis failed",recommendations:[e.message],metrics:{}}; selectedTab="analysis"; }
    busy=false; render();
  }
  async function resetDemo() {
    if(!confirm("Reset this temporary app to its original demo records?")) return;
    busy=true; render();
    try { const data=await request("state.reset",{}); state=data.state; analysis=null; toast("Demo data reset"); } catch(e){toast(e.message,true);}
    busy=false; render();
  }

  function table() {
    const rows=filtered();
    return `<div class="table-wrap"><table><thead><tr>${cfg.fields.slice(0,6).map(f=>`<th>${esc(f.label)}</th>`).join("")}<th></th></tr></thead><tbody>
    ${rows.map(x=>`<tr data-id="${esc(x.id)}">${cfg.fields.slice(0,6).map(f=>`<td>${f.type==="number"&&["amount","value","arr","budget","spent"].includes(f.key)?money(x[f.key]):esc(x[f.key])}</td>`).join("")}<td><button class="row-edit" data-edit="${esc(x.id)}">Edit</button></td></tr>`).join("")}
    </tbody></table>${!rows.length?'<div class="empty">No records match this view.</div>':""}</div>`;
  }

  function analysisView(){
    if(busy) return `<div class="analysis-card"><div class="spinner"></div><h2>Running in Sandbox Runner…</h2><p>Operly is executing this app’s declared capability in an isolated computer.</p></div>`;
    if(!analysis) return `<div class="analysis-card"><h2>Operational analysis</h2><p>Run the app capability to analyze the current persisted records.</p><button class="primary analyze">Run analysis</button></div>`;
    const metrics=analysis.metrics||{};
    return `<div class="analysis-card"><span class="eyebrow">Sandbox result</span><h2>${esc(analysis.summary||"Analysis complete")}</h2>
      <div class="analysis-metrics">${Object.entries(metrics).map(([k,v])=>`<div><span>${esc(k.replaceAll("_"," "))}</span><strong>${esc(v)}</strong></div>`).join("")}</div>
      <h3>Recommended actions</h3><ol>${(analysis.recommendations||[]).map(x=>`<li>${esc(x)}</li>`).join("")}</ol>
      <button class="ghost analyze">Run again</button></div>`;
  }

  function render() {
    const ms=metrics();
    root.innerHTML=`<div class="app-shell">
      <aside><div class="brand"><span class="brand-dot"></span><div><b>OPERLY APP</b><small>Temporary Workspace plugin</small></div></div>
        <nav>${["overview","records","analysis"].map(t=>`<button data-tab="${t}" class="${selectedTab===t?"active":""}">${t[0].toUpperCase()+t.slice(1)}</button>`).join("")}</nav>
        <div class="aside-foot"><span>Isolated UI</span><small>${esc(cfg.id)}</small></div>
      </aside>
      <main><header><div><span class="eyebrow">${esc(cfg.category)}</span><h1>${esc(cfg.name)}</h1><p>${esc(cfg.description)}</p></div>
        <div class="header-actions"><button class="ghost reset">Reset</button><button class="primary new">+ New</button></div></header>
      <section class="metrics">${ms.map(([a,b])=>`<div class="metric"><span>${esc(a)}</span><strong>${esc(b)}</strong></div>`).join("")}</section>
      ${selectedTab==="overview"?`<section class="panel"><div class="panel-head"><div><h2>Current work</h2><p>${records().length} persisted records in this plugin namespace.</p></div><button class="primary analyze">${busy?"Running…":"Run analysis"}</button></div>${table()}</section>`:""}
      ${selectedTab==="records"?`<section class="panel"><div class="panel-head"><div><h2>Records</h2><p>Edit, add or remove operational records.</p></div><input class="search" placeholder="Search records…" value="${esc(query)}"></div>${table()}</section>`:""}
      ${selectedTab==="analysis"?analysisView():""}
      </main></div>`;
    root.querySelectorAll("[data-tab]").forEach(b=>b.onclick=()=>{selectedTab=b.dataset.tab;render();});
    root.querySelectorAll("[data-edit]").forEach(b=>b.onclick=()=>openEditor(records().find(x=>x.id===b.dataset.edit)));
    root.querySelector(".new").onclick=()=>openEditor();
    root.querySelector(".reset").onclick=resetDemo;
    root.querySelectorAll(".analyze").forEach(b=>b.onclick=runAnalysis);
    const s=root.querySelector(".search"); if(s) s.oninput=e=>{query=e.target.value; const pos=e.target.selectionStart; render(); const ns=root.querySelector(".search"); ns.focus(); ns.setSelectionRange(pos,pos);};
  }

  async function init(){
    root.innerHTML='<div class="boot"><div class="spinner"></div><h2>Opening app…</h2><p>Loading Workspace-scoped plugin state from Operly.</p></div>';
    try { const data=await request("state.get",{}); state=data.state||{records:[]}; render(); }
    catch(e){ root.innerHTML=`<div class="boot error"><h2>Could not open app</h2><p>${esc(e.message)}</p></div>`; }
  }
  init();
})();'''

APP_CSS = r'''*{box-sizing:border-box}html,body,#app{margin:0;min-height:100%;font-family:Inter,ui-sans-serif,system-ui,-apple-system,sans-serif;background:#090b10;color:#f7f8fb}button,input,select{font:inherit}button{cursor:pointer}.app-shell{min-height:100vh;display:grid;grid-template-columns:218px minmax(0,1fr)}aside{position:sticky;top:0;height:100vh;background:#0d1016;border-right:1px solid #232833;padding:20px 14px;display:flex;flex-direction:column}.brand{display:flex;gap:10px;align-items:center;padding:4px 8px 24px}.brand-dot{width:12px;height:12px;border-radius:50%;background:#8b5cf6;box-shadow:0 0 22px #8b5cf699}.brand b{display:block;font-size:11px;letter-spacing:.13em}.brand small,.aside-foot small{display:block;color:#737b8a;font-size:10px;margin-top:3px}nav{display:grid;gap:5px}nav button{border:0;background:transparent;color:#9ba3b2;text-align:left;border-radius:9px;padding:10px 12px}nav button.active{background:#1b202a;color:#fff}.aside-foot{margin-top:auto;border-top:1px solid #232833;padding:14px 8px;color:#8b93a2;font-size:11px}main{min-width:0;padding:34px 40px 60px;background:radial-gradient(circle at 75% -20%,#2c1d4a55,transparent 30%),#090b10}header{display:flex;justify-content:space-between;gap:30px;align-items:flex-start;max-width:1400px;margin:auto}header h1{font-size:34px;line-height:1;margin:6px 0 8px}header p,.panel p{margin:0;color:#929aa9;max-width:690px;line-height:1.55}.eyebrow{color:#a78bfa;font-size:10px;font-weight:750;letter-spacing:.14em;text-transform:uppercase}.header-actions{display:flex;gap:8px}.primary,.ghost,.danger,.row-edit,.icon{border-radius:9px;padding:9px 13px;border:1px solid #313744;background:#171b23;color:#dfe3eb}.primary{background:#7c3aed;border-color:#8b5cf6;color:#fff}.danger{color:#ff9c9c;border-color:#653333;background:#291618}.icon{padding:5px 10px;font-size:20px}.metrics{max-width:1400px;margin:26px auto 18px;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.metric{border:1px solid #252b36;border-radius:14px;background:#11151c;padding:17px}.metric span{display:block;color:#858e9e;font-size:11px;text-transform:uppercase;letter-spacing:.08em}.metric strong{display:block;font-size:25px;margin-top:8px}.panel,.analysis-card{max-width:1400px;margin:auto;border:1px solid #252b36;background:#0f131a;border-radius:16px;overflow:hidden}.panel-head{display:flex;justify-content:space-between;gap:18px;align-items:center;padding:18px 20px;border-bottom:1px solid #232934}.panel-head h2,.analysis-card h2{margin:0 0 4px;font-size:18px}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;min-width:760px}th{text-align:left;color:#7f8999;font-size:10px;letter-spacing:.08em;text-transform:uppercase;padding:12px 16px;border-bottom:1px solid #222833}td{padding:14px 16px;border-bottom:1px solid #1d222c;color:#d8dce5;font-size:13px}tbody tr:hover{background:#151a22}.row-edit{padding:5px 9px;font-size:11px}.search{width:250px;max-width:42vw;background:#090d13;color:#fff;border:1px solid #303744;border-radius:9px;padding:9px 11px}.empty{text-align:center;padding:44px;color:#747d8c}.analysis-card{padding:26px}.analysis-card>p{color:#9099a8}.analysis-metrics{display:flex;flex-wrap:wrap;gap:10px;margin:20px 0}.analysis-metrics div{min-width:150px;border:1px solid #292f3a;border-radius:11px;padding:12px}.analysis-metrics span{display:block;color:#7f8897;font-size:11px;text-transform:capitalize}.analysis-metrics strong{display:block;margin-top:5px;font-size:19px}.analysis-card li{margin:9px 0;color:#cfd4dd;line-height:1.5}.modal-wrap{position:fixed;inset:0;background:#000a;display:grid;place-items:center;padding:18px;z-index:20}.modal{width:min(720px,100%);max-height:90vh;overflow:auto;background:#11151c;border:1px solid #303744;border-radius:18px;padding:22px;box-shadow:0 30px 100px #000}.modal-head{display:flex;justify-content:space-between;align-items:flex-start}.modal-head h2{margin:5px 0 18px}.form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:13px}.form-grid label span{display:block;font-size:11px;color:#858e9e;margin-bottom:6px}.form-grid input,.form-grid select{width:100%;padding:10px;background:#0a0d12;color:#fff;border:1px solid #303744;border-radius:8px}.modal-actions{display:grid;grid-template-columns:auto 1fr auto auto;gap:8px;margin-top:20px}.toast{position:fixed;right:18px;bottom:18px;background:#17251e;border:1px solid #2f6544;color:#baf7d0;padding:11px 14px;border-radius:10px;z-index:40}.toast.bad{background:#2b1717;border-color:#713636;color:#ffb7b7}.boot{min-height:100vh;display:grid;place-content:center;text-align:center;color:#9aa3b2}.boot h2{color:#fff;margin-bottom:2px}.spinner{width:28px;height:28px;border:3px solid #343b47;border-top-color:#8b5cf6;border-radius:50%;animation:spin .8s linear infinite;margin:0 auto 14px}@keyframes spin{to{transform:rotate(360deg)}}@media(max-width:760px){.app-shell{display:block}aside{position:static;height:auto;padding:12px;flex-direction:row;align-items:center;gap:10px;overflow:auto}.brand{padding:0 6px}.brand small,.aside-foot{display:none}nav{display:flex;margin-left:auto}nav button{white-space:nowrap;padding:8px 10px}main{padding:22px 14px 50px}header{display:block}header h1{font-size:28px}.header-actions{margin-top:16px}.metrics{grid-template-columns:1fr 1fr}.metric:last-child{grid-column:1/-1}.panel-head{align-items:flex-start;flex-direction:column}.search{max-width:none;width:100%}.form-grid{grid-template-columns:1fr}.modal-actions{grid-template-columns:auto 1fr auto}.modal-actions .ghost{display:none}}'''

RUNTIME_TEMPLATE = r'''from __future__ import annotations
import json, sys

KIND = __KIND__
APP_NAME = __APP_NAME__

def num(value):
    try: return float(value or 0)
    except (TypeError, ValueError): return 0.0

def money(value):
    return f"${value:,.0f}"

packet=json.load(sys.stdin)
args=packet.get("arguments") or {}
state=args.get("state") or {}
records=state.get("records") if isinstance(state,dict) else []
records=records if isinstance(records,list) else []
metrics={}
recs=[]

if KIND=="support":
    open_rows=[x for x in records if x.get("status")!="Resolved"]
    risk=[x for x in open_rows if num(x.get("age_hours")) >= .75*max(1,num(x.get("sla_hours")))]
    metrics={"open_tickets":len(open_rows),"sla_risk":len(risk),"urgent":sum(1 for x in open_rows if x.get("priority")=="Urgent")}
    for x in sorted(risk,key=lambda y:num(y.get("age_hours"))/max(1,num(y.get("sla_hours"))),reverse=True)[:3]:
        recs.append(f"Act on {x.get('id')}: {x.get('subject')} is approaching or beyond its SLA.")
elif KIND=="receivables":
    open_rows=[x for x in records if x.get("status")!="Paid"]
    overdue=[x for x in open_rows if num(x.get("days_due"))>0]
    metrics={"outstanding":money(sum(num(x.get("amount")) for x in open_rows)),"overdue":money(sum(num(x.get("amount")) for x in overdue)),"overdue_accounts":len(overdue)}
    for x in sorted(overdue,key=lambda y:(num(y.get("days_due")),num(y.get("amount"))),reverse=True)[:3]:
        recs.append(f"Follow up with {x.get('customer')} on {x.get('invoice')} ({int(num(x.get('days_due')))} days overdue).")
elif KIND=="inventory":
    low=[x for x in records if num(x.get("stock"))<=num(x.get("reorder_point"))]
    metrics={"skus":len(records),"below_reorder":len(low),"units_on_hand":int(sum(num(x.get("stock")) for x in records))}
    for x in sorted(low,key=lambda y:num(y.get("stock"))-num(y.get("reorder_point")))[:4]:
        target=max(num(x.get("reorder_point"))*1.5, num(x.get("daily_demand"))*(num(x.get("lead_days"))+14))
        qty=max(0,round(target-num(x.get("stock"))))
        recs.append(f"Reorder about {qty} units of {x.get('item')} from {x.get('supplier')}.")
elif KIND=="leads":
    active=[x for x in records if x.get("stage") not in {"Won","Lost"}]
    weighted=sum(num(x.get("value"))*num(x.get("probability"))/100 for x in active)
    metrics={"active_deals":len(active),"pipeline":money(sum(num(x.get("value")) for x in active)),"weighted_pipeline":money(weighted)}
    for x in sorted(active,key=lambda y:(num(y.get("days_idle")),num(y.get("value"))),reverse=True)[:3]:
        recs.append(f"Follow up with {x.get('company')}; {int(num(x.get('days_idle')))} days idle in {x.get('stage')}.")
elif KIND=="procurement":
    open_rows=[x for x in records if x.get("status")!="Received"]
    risky=[x for x in open_rows if num(x.get("days_to_due"))<0 or x.get("risk")=="High"]
    metrics={"open_pos":len(open_rows),"committed":money(sum(num(x.get("amount")) for x in open_rows)),"delivery_risk":len(risky)}
    for x in risky[:3]: recs.append(f"Escalate {x.get('po')} with {x.get('supplier')} due to delivery risk.")
elif KIND=="fulfillment":
    open_rows=[x for x in records if x.get("stage")!="Shipped"]
    risk=[x for x in open_rows if num(x.get("hours_open")) >= .8*max(1,num(x.get("promise_hours")))]
    metrics={"open_orders":len(open_rows),"open_units":int(sum(num(x.get("items")) for x in open_rows)),"promise_risk":len(risk)}
    for x in sorted(risk,key=lambda y:num(y.get("hours_open"))/max(1,num(y.get("promise_hours"))),reverse=True)[:3]:
        recs.append(f"Prioritize {x.get('order')} for {x.get('customer')} before its fulfillment promise slips.")
elif KIND=="campaigns":
    live=[x for x in records if x.get("status")=="Live"]
    blocked=[x for x in records if str(x.get("blocker") or "").strip()]
    metrics={"live_campaigns":len(live),"total_spend":money(sum(num(x.get("spent")) for x in records)),"blocked":len(blocked)}
    for x in blocked[:3]: recs.append(f"Clear blocker on {x.get('campaign')}: {x.get('blocker')}.")
    for x in live:
        if num(x.get("budget")) and num(x.get("spent"))/num(x.get("budget"))>.9 and num(x.get("progress"))<90:
            recs.append(f"Review pacing for {x.get('campaign')}; spend is ahead of completion.")
elif KIND=="contracts":
    active=[x for x in records if x.get("status")!="Signed"]
    risky=[x for x in active if num(x.get("risk_flags"))>=3]
    metrics={"active_reviews":len(active),"contract_value":money(sum(num(x.get("value")) for x in active)),"high_risk":len(risky)}
    for x in sorted(risky,key=lambda y:num(y.get("risk_flags")),reverse=True)[:3]:
        recs.append(f"Escalate {x.get('contract')} with {int(num(x.get('risk_flags')))} risk flags before approval.")
elif KIND=="reconcile":
    mismatch=[x for x in records if x.get("status")=="Mismatch"]
    auto=[x for x in mismatch if num(x.get("confidence"))>=90]
    metrics={"mismatches":len(mismatch),"high_confidence":len(auto),"resolved":sum(1 for x in records if x.get("status")=="Resolved")}
    for x in auto[:3]: recs.append(f"Review {x.get('record')} {x.get('field')} first; confidence is {int(num(x.get('confidence')))}%.")
elif KIND=="customer_health":
    risk=[x for x in records if x.get("status")=="At risk" or num(x.get("health"))<50]
    renew=[x for x in records if num(x.get("days_to_renewal"))<=30 and x.get("status")!="Renewed"]
    metrics={"accounts":len(records),"at_risk":len(risk),"renewing_30d":len(renew),"arr":money(sum(num(x.get("arr")) for x in records))}
    for x in sorted(risk+renew,key=lambda y:(num(y.get("health")),-num(y.get("arr"))))[:3]:
        recs.append(f"Create a recovery/renewal plan for {x.get('account')} (health {int(num(x.get('health')))}).")
elif KIND=="tasks":
    open_rows=[x for x in records if x.get("status")!="Done"]
    urgent=[x for x in open_rows if num(x.get("days_to_due"))<=1 or x.get("priority")=="Critical"]
    metrics={"open_tasks":len(open_rows),"blocked":sum(1 for x in open_rows if x.get("status")=="Blocked"),"due_now":len(urgent)}
    for x in urgent[:4]: recs.append(f"Unblock or finish {x.get('task')} owned by {x.get('owner')}.")
elif KIND=="cash":
    expected_in=sum(num(x.get("amount"))*num(x.get("confidence"))/100 for x in records if x.get("type")=="Inflow")
    out=sum(num(x.get("amount")) for x in records if x.get("type")=="Outflow")
    metrics={"probability_weighted_inflow":money(expected_in),"planned_outflow":money(out),"net":money(expected_in-out)}
    by_week={}
    for x in records:
        w=int(num(x.get("week")))
        by_week[w]=by_week.get(w,0)+(num(x.get("amount"))*(num(x.get("confidence"))/100 if x.get("type")=="Inflow" else -1))
    for w,b in sorted(by_week.items()):
        if b<0: recs.append(f"Week {w} has a projected net cash drain of {money(abs(b))}; confirm collections or defer discretionary outflow.")

if not recs: recs=["No immediate exception is dominating the current data. Continue normal review cadence."]
summary=f"{APP_NAME}: {len(records)} records analyzed; {len(recs)} action signal{'s' if len(recs)!=1 else ''} generated."
print(json.dumps({"result":{"summary":summary,"metrics":metrics,"recommendations":recs[:5]}},separators=(",",":"),sort_keys=True))
'''


def _capability_id(plugin_id: str) -> str:
    return f"{plugin_id}.analyze"


def _manifest(spec: dict) -> dict:
    capability_id = _capability_id(spec["id"])
    return {
        "schema_version": "operly.plugin/v1",
        "plugin_id": spec["id"],
        "version": "1.0.0",
        "display_name": spec["name"],
        "description": spec["description"],
        "execution_mode": "sandbox_job",
        "capabilities": [{
            "id": capability_id,
            "display_name": f"Analyze {spec['name']}",
            "description": f"Analyze the current {spec['name']} records inside an isolated Operly Sandbox computer.",
            "input_schema": {"type": "object", "properties": {"action": {"type": "string"}, "state": {"type": "object", "additionalProperties": True}}, "required": ["action", "state"], "additionalProperties": False},
            "output_schema": {"type": "object", "properties": {"summary": {"type": "string"}, "metrics": {"type": "object", "additionalProperties": True}, "recommendations": {"type": "array", "items": {"type": "string"}}}, "required": ["summary", "metrics", "recommendations"], "additionalProperties": False},
            "permissions": [], "risk": "read_only", "approval_required": False, "reversible": False,
            "aliases": [spec["name"].lower(), f"{spec['category'].lower()} analysis"], "emits": [], "tags": ["temporary", "demo", "functional-app", spec["kind"]],
        }],
        "permissions": [],
        "configuration_schema": {"type": "object", "properties": {"temporary_demo": {"type": "boolean"}, "demo_token_hash": {"type": "string"}, "demo_name": {"type": "string"}, "demo_category": {"type": "string"}, "demo_description": {"type": "string"}, "demo_seed": {"type": "object", "additionalProperties": True}}, "additionalProperties": False},
        "runtime": {"profile": "sandbox-job", "kind": "job", "network": {"mode": "off", "allowed_hosts": []}, "resources": {"cpu_millicores": spec["cpu"], "memory_mb": spec["memory"], "disk_mb": 2048, "max_runtime_seconds": 300, "max_concurrency": 2}},
        "storage": [{"name": "app", "kind": "document", "quota_bytes": 2 * 1024 * 1024}],
        "credentials": [], "produces_events": [], "consumes_events": [], "requested_bindings": [],
        "ui": [{"contribution_type": "navigation", "id": f"{spec['id']}.home", "title": spec["name"], "configuration": {"hosted_entry": "index.html", "temporary": True}}],
        "metadata": {"source": "operly-temp-functional-app-suite", "temporary": True, "remove_later": True, "app_kind": spec["kind"], "category": spec["category"], "hosted_entry": "index.html"},
    }


def _index(spec: dict) -> str:
    config = {"id": spec["id"], "name": spec["name"], "category": spec["category"], "description": spec["description"], "kind": spec["kind"], "fields": spec["fields"]}
    config_json = json.dumps(config, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{spec['name']} · Operly</title><link rel="stylesheet" href="assets/app.css"></head><body><div id="app"></div><script id="operly-config" type="application/json">{config_json}</script><script src="assets/app.js"></script></body></html>'''


def _runtime(spec: dict) -> str:
    return RUNTIME_TEMPLATE.replace("__KIND__", repr(spec["kind"])).replace("__APP_NAME__", repr(spec["name"]))


def _package(spec: dict) -> tuple[dict, bytes]:
    manifest = _manifest(spec)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("operly.plugin.json", json.dumps(manifest, separators=(",", ":"), sort_keys=True))
        archive.writestr("operly_runtime.py", _runtime(spec))
        archive.writestr("index.html", _index(spec))
        archive.writestr("assets/app.js", APP_JS)
        archive.writestr("assets/app.css", APP_CSS)
        archive.writestr("app-config.json", json.dumps({k: v for k, v in spec.items() if k != "seed"}, indent=2))
        archive.writestr("seed.json", json.dumps({"records": spec["seed"]}, indent=2))
        archive.writestr("TEMPORARY.md", "Temporary Operly functional-app demo. Safe to remove with the containing temp Workspace.\n")
    return manifest, buf.getvalue()


async def _bootstrap_identity() -> tuple[str, str, str, str]:
    suffix = uuid4().hex[:10]
    now = datetime.utcnow()
    session_secret = random_token()
    csrf_secret = random_token()
    async with SessionFactory() as db:
        tenant = Tenant(name=f"Temporary Functional App Lab {suffix}", slug=f"temp-app-lab-{suffix}", timezone="UTC")
        user = AppUser(email=f"temp-app-suite-{suffix}@example.com", display_name="Temporary App Suite Owner", active=True, email_verified_at=now)
        db.add_all([tenant, user]); await db.flush()
        db.add(TenantMember(tenant_id=tenant.id, user_id=user.id, role="owner"))
        db.add(AuthSession(token_hash=hash_token(session_secret, purpose="session"), csrf_token_hash=hash_token(csrf_secret, purpose="csrf"), user_id=user.id, tenant_id=tenant.id, created_at=now, expires_at=now + timedelta(hours=4), last_activity_at=now, authenticated_at=now, user_agent="Operly Temporary Functional App Suite"))
        await db.commit()
        return tenant.id, tenant.slug or tenant.id, session_secret, csrf_secret


def _json(response: httpx.Response) -> dict:
    try: data = response.json()
    except ValueError as error: raise RuntimeError(f"HTTP {response.status_code} returned non-JSON: {response.text[:800]}") from error
    if response.status_code >= 400: raise RuntimeError(f"HTTP {response.status_code}: {json.dumps(data, sort_keys=True)[:1600]}")
    if not isinstance(data, dict): raise RuntimeError("Operly API returned an invalid response")
    return data


async def _wait_validation(client: httpx.AsyncClient, installation_id: str, timeout: float = 420.0) -> None:
    deadline = time.monotonic() + timeout; last = None
    while time.monotonic() < deadline:
        data = _json(await client.get(f"/api/plugin-platform/installations/{installation_id}/runtime")); status = data.get("validation_status")
        if status != last: print("TEMP_APP_VALIDATION", installation_id, status, flush=True); last = status
        if status == "passed": return
        if status == "failed": raise RuntimeError(f"Plugin validation failed: {installation_id}")
        await asyncio.sleep(2)
    raise TimeoutError(f"Plugin validation timed out: {installation_id}")


async def _wait_runtime(client: httpx.AsyncClient, installation_id: str, timeout: float = 420.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = _json(await client.get(f"/api/plugin-platform/installations/{installation_id}/runtime"))
        healthy = next((x for x in data.get("instances", []) if x.get("state") in {"ready", "running"} and x.get("health_state") == "healthy"), None)
        if healthy: return healthy
        await asyncio.sleep(2)
    raise TimeoutError(f"Plugin runtime timed out: {installation_id}")


async def main() -> None:
    await init_db()
    base_url = (os.getenv("PUBLIC_BASE_URL") or "https://operly.dragonzpyder.xyz").strip().rstrip("/")
    workspace_id, workspace_slug, session_secret, csrf_secret = await _bootstrap_identity()
    demo_token = secrets.token_urlsafe(32); demo_token_hash = hashlib.sha256(demo_token.encode("utf-8")).hexdigest()
    print("TEMP_APP_SUITE_WORKSPACE", workspace_id, workspace_slug, flush=True)
    headers = {"Origin": base_url, "X-CSRF-Token": csrf_secret, "User-Agent": "Operly-Temp-App-Suite/1"}
    cookies = {PROD_SESSION_COOKIE: session_secret, PROD_CSRF_COOKIE: csrf_secret}
    records = []
    async with httpx.AsyncClient(base_url=base_url, headers=headers, cookies=cookies, timeout=120.0) as client:
        health = _json(await client.get("/api/health"))
        if not health.get("ok"): raise RuntimeError("Operly API is unhealthy")
        for spec in APP_SPECS:
            manifest, package_bytes = _package(spec)
            upload = _json(await client.post("/api/artifacts/upload", files={"files": (f"{spec['id']}.zip", package_bytes, "application/zip")}))
            published = _json(await client.post("/api/plugin-platform/packages", json={"manifest": manifest, "package_artifact_id": upload["artifact_ids"][0]}))
            seed_state = {"records": spec["seed"], "updated_at": datetime.utcnow().isoformat()}
            installed = _json(await client.post("/api/plugin-platform/installations", json={"version_id": published["version_id"], "granted_permissions": [], "configuration": {"temporary_demo": True, "demo_token_hash": demo_token_hash, "demo_name": spec["name"], "demo_category": spec["category"], "demo_description": spec["description"], "demo_seed": seed_state}}))
            item = {"plugin_id": spec["id"], "name": spec["name"], "installation_id": installed["installation_id"], "capability_id": _capability_id(spec["id"])}
            records.append(item); print("TEMP_APP_PUBLISHED", json.dumps(item, sort_keys=True), flush=True)
        await asyncio.gather(*(_wait_validation(client, x["installation_id"]) for x in records))
        for item in records:
            accepted = _json(await client.post(f"/api/plugin-platform/installations/{item['installation_id']}/runtime/reconcile", json={})); item["reconcile_job_id"] = accepted["job_id"]
        instances = await asyncio.gather(*(_wait_runtime(client, x["installation_id"]) for x in records))
        for item, instance in zip(records, instances):
            item["runtime_provider"] = instance.get("provider")
            active = _json(await client.patch(f"/api/plugin-platform/installations/{item['installation_id']}", json={"status": "active", "enabled": True}))
            if not active.get("enabled"): raise RuntimeError(f"Failed to activate {item['plugin_id']}")
        demo_headers = {"X-Operly-Demo-Token": demo_token, "Origin": base_url}
        for item in records:
            spec = next(x for x in APP_SPECS if x["id"] == item["plugin_id"])
            state = {"records": spec["seed"], "updated_at": datetime.utcnow().isoformat()}
            _json(await client.put(f"/api/public/plugin-demos/{workspace_id}/{item['plugin_id']}/state", headers=demo_headers, json={"state": state}))
            hosted = await client.get(f"/api/public/plugins/{workspace_id}/{item['plugin_id']}/")
            if hosted.status_code != 200 or spec["name"] not in hosted.text: raise RuntimeError(f"Hosted UI failed for {item['plugin_id']}: HTTP {hosted.status_code}")
            asset = await client.get(f"/api/public/plugins/{workspace_id}/{item['plugin_id']}/assets/app.js")
            if asset.status_code != 200 or "operly.temp-app" not in asset.text: raise RuntimeError(f"Hosted JS failed for {item['plugin_id']}")
            execution = _json(await client.post(f"/api/public/plugin-demos/{workspace_id}/{item['plugin_id']}/execute", headers=demo_headers, json={"action": "analyze"}))
            if not execution.get("result", {}).get("summary"): raise RuntimeError(f"Sandbox analysis failed for {item['plugin_id']}")
            item["hosted_url"] = f"{base_url}/api/public/plugins/{workspace_id}/{item['plugin_id']}/"; item["analysis_run_id"] = execution.get("run_id")
            print("TEMP_APP_VERIFIED", json.dumps(item, sort_keys=True), flush=True)
        lab_url = f"{base_url}/temp-app-lab/{workspace_id}?token={demo_token}"
        print("TEMP_APP_SUITE_RESULT", json.dumps({"status": "PASS", "workspace_id": workspace_id, "workspace_slug": workspace_slug, "app_count": len(records), "lab_url": lab_url, "apps": records}, sort_keys=True), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
