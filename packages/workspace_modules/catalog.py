from typing import Any


def field(key: str, label: str, field_type: str = "text", **extra: Any) -> dict[str, Any]:
    return {"key": key, "label": label, "type": field_type, **extra}


def entity(
    key: str,
    label: str,
    singular: str,
    columns: list[str],
    fields: list[dict[str, Any]],
    *,
    read_only: bool = False,
) -> dict[str, Any]:
    return {
        "entity": key,
        "label": label,
        "singular": singular,
        "columns": columns,
        "fields": fields,
        "readOnly": read_only,
    }


MODULE_CATALOG: dict[str, dict[str, Any]] = {
    "dashboard": {
        "name": "Overview", "category": "core", "description": "Workspace health, operating metrics, recent activity and quick actions.",
        "icon": "overview", "default_enabled": True, "required_permission": "workspace:read", "dependencies": [], "locked": True,
    },
    "crm": {
        "name": "CRM", "category": "customers", "description": "People, organizations, opportunities and every customer interaction from first touch to retention.",
        "icon": "crm", "default_enabled": True, "required_permission": "crm:read", "write_permission": "crm:write", "dependencies": [],
    },
    "catalog": {
        "name": "Products & Services", "category": "commerce", "description": "Products, services, SKUs, pricing, costs and commercial availability.",
        "icon": "catalog", "default_enabled": True, "required_permission": "catalog:read", "write_permission": "catalog:write", "dependencies": [],
    },
    "inventory": {
        "name": "Inventory & Warehouses", "category": "commerce", "description": "Stock, warehouses, transfers, reorder thresholds and movement history.",
        "icon": "inventory", "default_enabled": True, "required_permission": "inventory:read", "write_permission": "inventory:write", "dependencies": ["catalog"],
    },
    "sales": {
        "name": "Sales", "category": "commerce", "description": "Quotes, line items, orders, contracts and recurring subscriptions across the full lead-to-cash cycle.",
        "icon": "sales", "default_enabled": True, "required_permission": "orders:read", "write_permission": "orders:write", "dependencies": ["crm", "catalog"],
    },
    "finance": {
        "name": "Finance", "category": "finance", "description": "Invoices, payments, expenses, accounts, ledger entries and budgets for operating control.",
        "icon": "finance", "default_enabled": True, "required_permission": "finance:read", "write_permission": "finance:write", "dependencies": [],
    },
    "suppliers": {
        "name": "Procurement", "category": "commerce", "description": "Suppliers, purchase orders, purchasing line items, receiving and supplier economics.",
        "icon": "suppliers", "default_enabled": False, "required_permission": "suppliers:read", "write_permission": "suppliers:write", "dependencies": ["catalog"],
    },
    "fulfillment": {
        "name": "Fulfillment & Returns", "category": "commerce", "description": "In-house, dropship and 3PL fulfillment, tracking, delivery and returns.",
        "icon": "fulfillment", "default_enabled": False, "required_permission": "fulfillment:read", "write_permission": "fulfillment:write", "dependencies": ["sales"],
    },
    "projects": {
        "name": "Projects & Time", "category": "work", "description": "Projects, milestones and time tracking for client work, internal programs and delivery teams.",
        "icon": "projects", "default_enabled": True, "required_permission": "projects:read", "write_permission": "projects:write", "dependencies": ["tasks"],
    },
    "operations": {
        "name": "Operations & Assets", "category": "work", "description": "Assets, maintenance and work orders for field service, facilities, labs and operational teams.",
        "icon": "operations", "default_enabled": False, "required_permission": "operations:read", "write_permission": "operations:write", "dependencies": [],
    },
    "support": {
        "name": "Customer Support", "category": "customers", "description": "Tickets, priorities, channels, ownership, resolution and customer service history.",
        "icon": "support", "default_enabled": False, "required_permission": "support:read", "write_permission": "support:write", "dependencies": ["crm"],
    },
    "scheduling": {
        "name": "Scheduling", "category": "work", "description": "Appointments, bookings, assignments and operating schedules.",
        "icon": "scheduling", "default_enabled": True, "required_permission": "appointments:read", "write_permission": "appointments:write", "dependencies": [],
    },
    "tasks": {
        "name": "Tasks", "category": "work", "description": "Workspace tasks, ownership, due work and execution tracking.",
        "icon": "tasks", "default_enabled": True, "required_permission": "tasks:read", "write_permission": "tasks:write", "dependencies": [],
    },
    "team": {
        "name": "People & Time Off", "category": "people", "description": "Operational staff directory, leave requests and access-controlled workspace membership.",
        "icon": "team", "default_enabled": True, "required_permission": "team:read", "write_permission": "team:write", "dependencies": [],
    },
    "documents": {
        "name": "Documents & SOPs", "category": "knowledge", "description": "Notes, SOPs, policies, procedures and reusable operating knowledge.",
        "icon": "documents", "default_enabled": True, "required_permission": "documents:read", "write_permission": "documents:write", "dependencies": [],
    },
    "marketing": {
        "name": "Marketing", "category": "growth", "description": "Campaigns, content calendar, channels, budgets, spend and attributable revenue.",
        "icon": "marketing", "default_enabled": False, "required_permission": "marketing:read", "write_permission": "marketing:write", "dependencies": ["crm"],
    },
    "compliance": {
        "name": "Risk & Compliance", "category": "governance", "description": "Risk register, incidents, audits, corrective actions and operational governance.",
        "icon": "compliance", "default_enabled": False, "required_permission": "compliance:read", "write_permission": "compliance:write", "dependencies": ["documents"],
    },
    "research": {
        "name": "Research Lab", "category": "research", "description": "Research projects, experiments, samples, datasets, protocols and scientific traceability.",
        "icon": "research", "default_enabled": False, "required_permission": "research:read", "write_permission": "research:write", "dependencies": ["documents", "projects"],
    },
    "grants": {
        "name": "Grants & Funding", "category": "research", "description": "Funding prospects, submissions, awards, periods and project/research linkage.",
        "icon": "grants", "default_enabled": False, "required_permission": "grants:read", "write_permission": "grants:write", "dependencies": ["finance"],
    },
    "integrations": {
        "name": "Integrations", "category": "system", "description": "Workspace-scoped external systems, connection status, scopes and health.",
        "icon": "integrations", "default_enabled": True, "required_permission": "integrations:read", "write_permission": "integrations:manage", "dependencies": [],
    },
}


WORKSPACE_PRESETS: dict[str, dict[str, Any]] = {
    "small-business": {
        "name": "Small business", "description": "Core customer, sales, finance, projects, scheduling and operating tools.",
        "modules": ["crm", "catalog", "sales", "finance", "projects", "tasks", "scheduling", "team", "documents", "integrations"],
    },
    "ecommerce": {
        "name": "E-commerce / Dropshipping", "description": "Lead-to-order, products, inventory, suppliers, fulfillment, support and growth.",
        "modules": ["crm", "catalog", "inventory", "sales", "finance", "suppliers", "fulfillment", "support", "tasks", "documents", "marketing", "integrations"],
    },
    "professional-services": {
        "name": "Professional services", "description": "CRM, proposals, contracts, projects, time, invoicing, scheduling and knowledge.",
        "modules": ["crm", "catalog", "sales", "finance", "projects", "support", "scheduling", "tasks", "team", "documents", "marketing", "integrations"],
    },
    "operations": {
        "name": "Operations / Field service", "description": "Assets, work orders, inventory, procurement, projects, people and compliance.",
        "modules": ["crm", "catalog", "inventory", "sales", "finance", "suppliers", "projects", "operations", "scheduling", "tasks", "team", "documents", "compliance", "integrations"],
    },
    "research-group": {
        "name": "Research group / Lab", "description": "Research traceability, projects, grants, assets, risk, finance, people and SOPs.",
        "modules": ["finance", "projects", "operations", "scheduling", "tasks", "team", "documents", "compliance", "research", "grants", "integrations"],
    },
    "agency": {
        "name": "Agency / Studio", "description": "CRM, campaigns, sales, client projects, time, finance, tasks and team operations.",
        "modules": ["crm", "catalog", "sales", "finance", "projects", "support", "scheduling", "tasks", "team", "documents", "marketing", "integrations"],
    },
}


ENTITY_CATALOG: dict[str, list[dict[str, Any]]] = {
    "crm": [
        entity("organizations", "Organizations", "Organization", ["name", "account_type", "industry", "owner", "status"], [
            field("name", "Name", required=True), field("account_type", "Type", "select", options=["customer", "prospect", "partner", "vendor", "other"]),
            field("industry", "Industry"), field("website", "Website"), field("email", "Email", "email"), field("phone", "Phone"),
            field("status", "Status", "select", options=["active", "inactive"]), field("owner", "Owner"),
            field("billing_address", "Billing address", "textarea"), field("shipping_address", "Shipping address", "textarea"), field("notes", "Notes", "textarea"),
        ]),
        entity("contacts", "Contacts", "Contact", ["name", "company", "email", "phone", "status"], [
            field("name", "Name", required=True), field("company", "Company"), field("email", "Email", "email"), field("phone", "Phone"),
            field("source", "Source", placeholder="manual, referral, website…"), field("status", "Status", "select", options=["active", "inactive"]), field("notes", "Notes", "textarea"),
        ]),
        entity("leads", "Pipeline", "Opportunity", ["title", "stage", "value", "assigned_to", "next_action_at"], [
            field("contact_id", "Contact", "reference", referenceEntity="contacts", referenceLabel="name"), field("title", "Opportunity", required=True),
            field("stage", "Stage", "select", options=["new", "qualified", "proposal", "negotiation", "won", "lost"]), field("value", "Value", "number"),
            field("assigned_to", "Owner"), field("next_action", "Next action", "textarea"), field("last_contacted_at", "Last contacted", "datetime-local"), field("next_action_at", "Next action time", "datetime-local"),
        ]),
        entity("interactions", "Timeline", "Interaction", ["subject", "interaction_type", "channel", "owner", "occurred_at"], [
            field("contact_id", "Contact", "reference", referenceEntity="contacts", referenceLabel="name"), field("account_id", "Organization", "reference", referenceEntity="organizations", referenceLabel="name"),
            field("lead_id", "Opportunity", "reference", referenceEntity="leads", referenceLabel="title"), field("interaction_type", "Type", "select", options=["note", "call", "email", "meeting", "message", "task", "other"]),
            field("channel", "Channel"), field("subject", "Subject", required=True), field("body", "Details", "textarea"), field("owner", "Owner"),
            field("occurred_at", "Occurred", "datetime-local"), field("next_action_at", "Next action", "datetime-local"),
        ]),
    ],
    "catalog": [
        entity("catalog", "Products & Services", "Item", ["name", "sku", "item_type", "price", "cost", "stock_qty", "active"], [
            field("name", "Name", required=True), field("sku", "SKU"), field("item_type", "Type", "select", options=["product", "service", "subscription", "bundle"]),
            field("price", "Price", "number"), field("cost", "Cost", "number"), field("stock_qty", "Opening stock", "number"), field("reorder_level", "Reorder level", "number"), field("active", "Active", "checkbox"),
        ]),
    ],
    "inventory": [
        entity("warehouses", "Warehouses", "Warehouse", ["name", "code", "active", "location"], [
            field("name", "Name", required=True), field("code", "Code", required=True), field("location", "Location", "textarea"), field("active", "Active", "checkbox"), field("notes", "Notes", "textarea"),
        ]),
        entity("stock-transfers", "Stock Transfers", "Transfer", ["item_id", "from_warehouse_id", "to_warehouse_id", "quantity", "status", "requested_at"], [
            field("item_id", "Item", "reference", referenceEntity="catalog", referenceLabel="name", required=True), field("from_warehouse_id", "From", "reference", referenceEntity="warehouses", referenceLabel="name"),
            field("to_warehouse_id", "To", "reference", referenceEntity="warehouses", referenceLabel="name"), field("quantity", "Quantity", "number", required=True),
            field("status", "Status", "select", options=["requested", "approved", "in_transit", "completed", "cancelled"]), field("requested_at", "Requested", "datetime-local"), field("completed_at", "Completed", "datetime-local"), field("notes", "Notes", "textarea"),
        ]),
    ],
    "sales": [
        entity("quotes", "Quotes", "Quote", ["title", "status", "total", "valid_until"], [
            field("contact_id", "Customer", "reference", referenceEntity="contacts", referenceLabel="name"), field("title", "Title", required=True),
            field("status", "Status", "select", options=["draft", "sent", "accepted", "rejected", "expired"]), field("total", "Total", "number"), field("valid_until", "Valid until", "datetime-local"), field("notes", "Notes", "textarea"),
        ]),
        entity("quote-items", "Quote Lines", "Quote line", ["quote_id", "description", "quantity", "unit_price", "discount", "tax_rate"], [
            field("quote_id", "Quote", "reference", referenceEntity="quotes", referenceLabel="title", required=True), field("catalog_item_id", "Catalog item", "reference", referenceEntity="catalog", referenceLabel="name"),
            field("description", "Description", required=True), field("quantity", "Quantity", "number"), field("unit_price", "Unit price", "number"), field("discount", "Discount", "number"), field("tax_rate", "Tax %", "number"),
        ]),
        entity("orders", "Orders", "Order", ["status", "total", "contact_id", "created_at"], [
            field("contact_id", "Customer", "reference", referenceEntity="contacts", referenceLabel="name"), field("status", "Status", "select", options=["draft", "pending", "confirmed", "paid", "fulfilled", "cancelled"]), field("total", "Total", "number"), field("notes", "Notes", "textarea"),
        ]),
        entity("order-items", "Order Lines", "Order line", ["order_id", "description", "quantity", "unit_price"], [
            field("order_id", "Order", "reference", referenceEntity="orders", referenceLabel="id", required=True), field("catalog_item_id", "Catalog item", "reference", referenceEntity="catalog", referenceLabel="name"),
            field("description", "Description", required=True), field("quantity", "Quantity", "number"), field("unit_price", "Unit price", "number"),
        ]),
        entity("contracts", "Contracts", "Contract", ["title", "status", "value", "starts_at", "renewal_at"], [
            field("contact_id", "Contact", "reference", referenceEntity="contacts", referenceLabel="name"), field("account_id", "Organization", "reference", referenceEntity="organizations", referenceLabel="name"),
            field("title", "Title", required=True), field("status", "Status", "select", options=["draft", "sent", "active", "expired", "terminated"]), field("value", "Value", "number"), field("currency", "Currency"),
            field("starts_at", "Starts", "datetime-local"), field("ends_at", "Ends", "datetime-local"), field("renewal_at", "Renewal", "datetime-local"), field("terms", "Terms", "textarea"),
        ]),
        entity("subscriptions", "Subscriptions", "Subscription", ["status", "catalog_item_id", "quantity", "unit_price", "billing_interval", "next_billing_at"], [
            field("contact_id", "Contact", "reference", referenceEntity="contacts", referenceLabel="name"), field("account_id", "Organization", "reference", referenceEntity="organizations", referenceLabel="name"),
            field("catalog_item_id", "Item", "reference", referenceEntity="catalog", referenceLabel="name"), field("status", "Status", "select", options=["trial", "active", "past_due", "paused", "cancelled"]),
            field("quantity", "Quantity", "number"), field("unit_price", "Unit price", "number"), field("currency", "Currency"), field("billing_interval", "Billing interval", "select", options=["weekly", "monthly", "quarterly", "annual", "custom"]),
            field("started_at", "Started", "datetime-local"), field("next_billing_at", "Next billing", "datetime-local"), field("cancelled_at", "Cancelled", "datetime-local"), field("notes", "Notes", "textarea"),
        ]),
    ],
    "finance": [
        entity("invoices", "Invoices", "Invoice", ["number", "status", "total", "due_at", "paid_at"], [
            field("contact_id", "Customer", "reference", referenceEntity="contacts", referenceLabel="name"), field("order_id", "Order", "reference", referenceEntity="orders", referenceLabel="id"),
            field("number", "Invoice number", required=True), field("status", "Status", "select", options=["draft", "sent", "due", "paid", "overdue", "void"]), field("currency", "Currency"),
            field("subtotal", "Subtotal", "number"), field("tax", "Tax", "number"), field("total", "Total", "number"), field("due_at", "Due", "datetime-local"), field("paid_at", "Paid at", "datetime-local"), field("notes", "Notes", "textarea"),
        ]),
        entity("invoice-items", "Invoice Lines", "Invoice line", ["invoice_id", "description", "quantity", "unit_price", "discount", "tax_rate"], [
            field("invoice_id", "Invoice", "reference", referenceEntity="invoices", referenceLabel="number", required=True), field("catalog_item_id", "Catalog item", "reference", referenceEntity="catalog", referenceLabel="name"),
            field("description", "Description", required=True), field("quantity", "Quantity", "number"), field("unit_price", "Unit price", "number"), field("discount", "Discount", "number"), field("tax_rate", "Tax %", "number"),
        ]),
        entity("payments", "Payments", "Payment", ["direction", "amount", "status", "method", "reference", "paid_at"], [
            field("contact_id", "Contact", "reference", referenceEntity="contacts", referenceLabel="name"), field("invoice_id", "Invoice", "reference", referenceEntity="invoices", referenceLabel="number"), field("order_id", "Order", "reference", referenceEntity="orders", referenceLabel="id"),
            field("direction", "Direction", "select", options=["incoming", "outgoing"]), field("method", "Method"), field("provider", "Provider"), field("reference", "Reference"),
            field("status", "Status", "select", options=["pending", "completed", "failed", "refunded", "void"]), field("amount", "Amount", "number"), field("currency", "Currency"), field("paid_at", "Paid at", "datetime-local"), field("notes", "Notes", "textarea"),
        ]),
        entity("expenses", "Expenses", "Expense", ["vendor", "category", "amount", "currency", "incurred_at", "status"], [
            field("vendor", "Vendor", required=True), field("category", "Category"), field("amount", "Amount", "number"), field("currency", "Currency"),
            field("status", "Status", "select", options=["posted", "pending", "void"]), field("incurred_at", "Date", "datetime-local"), field("notes", "Notes", "textarea"),
        ]),
        entity("financial-accounts", "Accounts", "Financial account", ["code", "name", "account_type", "currency", "opening_balance", "active"], [
            field("code", "Code"), field("name", "Name", required=True), field("account_type", "Type", "select", options=["bank", "cash", "receivable", "payable", "income", "expense", "asset", "liability", "equity"]),
            field("currency", "Currency"), field("opening_balance", "Opening balance", "number"), field("active", "Active", "checkbox"), field("notes", "Notes", "textarea"),
        ]),
        entity("ledger", "Ledger", "Ledger entry", ["occurred_at", "financial_account_id", "category", "debit", "credit", "counterparty"], [
            field("financial_account_id", "Account", "reference", referenceEntity="financial-accounts", referenceLabel="name"), field("counterparty", "Counterparty"), field("category", "Category"),
            field("debit", "Debit", "number"), field("credit", "Credit", "number"), field("currency", "Currency"), field("occurred_at", "Date", "datetime-local"), field("source_type", "Source type"), field("source_id", "Source ID"), field("memo", "Memo", "textarea"),
        ]),
        entity("budgets", "Budgets", "Budget", ["name", "category", "period_start", "period_end", "amount", "spent", "status"], [
            field("name", "Name", required=True), field("category", "Category"), field("period_start", "Period start", "datetime-local", required=True), field("period_end", "Period end", "datetime-local", required=True),
            field("amount", "Budget", "number"), field("spent", "Spent", "number"), field("currency", "Currency"), field("status", "Status", "select", options=["draft", "active", "closed"]), field("notes", "Notes", "textarea"),
        ]),
    ],
    "suppliers": [
        entity("suppliers", "Suppliers", "Supplier", ["name", "status", "lead_time_days", "minimum_order_value", "email"], [
            field("name", "Name", required=True), field("email", "Email", "email"), field("phone", "Phone"), field("website", "Website"), field("status", "Status", "select", options=["active", "inactive"]),
            field("lead_time_days", "Lead time (days)", "number"), field("minimum_order_value", "Minimum order", "number"), field("currency", "Currency"), field("notes", "Notes", "textarea"),
        ]),
        entity("purchase-orders", "Purchase Orders", "Purchase order", ["reference", "status", "supplier_id", "total", "expected_at"], [
            field("supplier_id", "Supplier", "reference", referenceEntity="suppliers", referenceLabel="name"), field("reference", "Reference", required=True),
            field("status", "Status", "select", options=["draft", "approved", "ordered", "partially_received", "received", "cancelled"]), field("currency", "Currency"),
            field("subtotal", "Subtotal", "number"), field("shipping_cost", "Shipping", "number"), field("total", "Total", "number"), field("expected_at", "Expected", "datetime-local"), field("received_at", "Received", "datetime-local"), field("notes", "Notes", "textarea"),
        ]),
        entity("purchase-order-items", "PO Lines", "PO line", ["purchase_order_id", "description", "quantity", "unit_cost", "received_quantity"], [
            field("purchase_order_id", "Purchase order", "reference", referenceEntity="purchase-orders", referenceLabel="reference", required=True), field("catalog_item_id", "Catalog item", "reference", referenceEntity="catalog", referenceLabel="name"),
            field("description", "Description", required=True), field("quantity", "Quantity", "number"), field("unit_cost", "Unit cost", "number"), field("tax_rate", "Tax %", "number"), field("received_quantity", "Received qty", "number"),
        ]),
    ],
    "fulfillment": [
        entity("fulfillments", "Fulfillments", "Fulfillment", ["order_id", "method", "status", "carrier", "tracking_number", "delivered_at"], [
            field("order_id", "Order", "reference", referenceEntity="orders", referenceLabel="id", required=True), field("supplier_id", "Supplier", "reference", referenceEntity="suppliers", referenceLabel="name"),
            field("method", "Method", "select", options=["in_house", "dropship", "3pl"]), field("status", "Status", "select", options=["pending", "processing", "shipped", "delivered", "cancelled"]),
            field("carrier", "Carrier"), field("tracking_number", "Tracking number"), field("fulfillment_cost", "Cost", "number"), field("shipped_at", "Shipped", "datetime-local"), field("delivered_at", "Delivered", "datetime-local"), field("notes", "Notes", "textarea"),
        ]),
        entity("returns", "Returns", "Return", ["order_id", "status", "reason", "refund_amount", "received_back"], [
            field("order_id", "Order", "reference", referenceEntity="orders", referenceLabel="id", required=True), field("fulfillment_id", "Fulfillment", "reference", referenceEntity="fulfillments", referenceLabel="tracking_number"),
            field("status", "Status", "select", options=["requested", "approved", "in_transit", "received", "refunded", "rejected"]), field("reason", "Reason"), field("refund_amount", "Refund", "number"), field("currency", "Currency"), field("received_back", "Received back", "checkbox"), field("notes", "Notes", "textarea"),
        ]),
    ],
    "projects": [
        entity("projects", "Projects", "Project", ["code", "name", "project_type", "status", "owner", "due_at", "budget", "spent"], [
            field("contact_id", "Client/contact", "reference", referenceEntity="contacts", referenceLabel="name"), field("code", "Code"), field("name", "Name", required=True), field("project_type", "Type"),
            field("status", "Status", "select", options=["planning", "active", "on_hold", "completed", "cancelled"]), field("owner", "Owner"), field("starts_at", "Starts", "datetime-local"), field("due_at", "Due", "datetime-local"),
            field("budget", "Budget", "number"), field("spent", "Spent", "number"), field("description", "Description", "textarea"),
        ]),
        entity("milestones", "Milestones", "Milestone", ["project_id", "title", "status", "owner", "due_at", "completed_at"], [
            field("project_id", "Project", "reference", referenceEntity="projects", referenceLabel="name", required=True), field("title", "Title", required=True), field("status", "Status", "select", options=["open", "in_progress", "blocked", "done", "cancelled"]),
            field("owner", "Owner"), field("due_at", "Due", "datetime-local"), field("completed_at", "Completed", "datetime-local"), field("description", "Description", "textarea"),
        ]),
        entity("time-entries", "Time Entries", "Time entry", ["work_date", "project_id", "team_member_id", "hours", "billable", "hourly_rate"], [
            field("project_id", "Project", "reference", referenceEntity="projects", referenceLabel="name"), field("team_member_id", "Team member", "reference", referenceEntity="team", referenceLabel="name"),
            field("work_date", "Date", "datetime-local"), field("hours", "Hours", "number"), field("billable", "Billable", "checkbox"), field("hourly_rate", "Hourly rate", "number"), field("description", "Description", "textarea"),
        ]),
    ],
    "operations": [
        entity("assets", "Assets", "Asset", ["tag", "name", "asset_type", "status", "location", "next_maintenance_at"], [
            field("tag", "Asset tag"), field("name", "Name", required=True), field("asset_type", "Type"), field("status", "Status", "select", options=["active", "maintenance", "retired", "lost"]), field("serial_number", "Serial number"),
            field("location", "Location"), field("owner", "Owner"), field("acquired_at", "Acquired", "datetime-local"), field("acquisition_cost", "Acquisition cost", "number"), field("next_maintenance_at", "Next maintenance", "datetime-local"), field("notes", "Notes", "textarea"),
        ]),
        entity("maintenance", "Maintenance", "Maintenance record", ["asset_id", "title", "status", "scheduled_at", "completed_at", "cost"], [
            field("asset_id", "Asset", "reference", referenceEntity="assets", referenceLabel="name", required=True), field("title", "Title", required=True), field("status", "Status", "select", options=["scheduled", "in_progress", "completed", "cancelled"]),
            field("scheduled_at", "Scheduled", "datetime-local"), field("completed_at", "Completed", "datetime-local"), field("cost", "Cost", "number"), field("vendor", "Vendor"), field("notes", "Notes", "textarea"),
        ]),
        entity("work-orders", "Work Orders", "Work order", ["reference", "title", "status", "priority", "assigned_to", "scheduled_start", "actual_cost"], [
            field("project_id", "Project", "reference", referenceEntity="projects", referenceLabel="name"), field("contact_id", "Contact", "reference", referenceEntity="contacts", referenceLabel="name"), field("asset_id", "Asset", "reference", referenceEntity="assets", referenceLabel="name"),
            field("reference", "Reference", required=True), field("title", "Title", required=True), field("status", "Status", "select", options=["open", "scheduled", "in_progress", "completed", "cancelled"]), field("priority", "Priority", "select", options=["low", "normal", "high", "urgent"]),
            field("assigned_to", "Assigned to"), field("scheduled_start", "Start", "datetime-local"), field("scheduled_end", "End", "datetime-local"), field("estimated_cost", "Estimated cost", "number"), field("actual_cost", "Actual cost", "number"), field("description", "Description", "textarea"),
        ]),
    ],
    "support": [
        entity("tickets", "Tickets", "Ticket", ["subject", "status", "priority", "channel", "assigned_to", "opened_at"], [
            field("contact_id", "Customer", "reference", referenceEntity="contacts", referenceLabel="name"), field("subject", "Subject", required=True), field("status", "Status", "select", options=["open", "pending", "resolved", "closed"]),
            field("priority", "Priority", "select", options=["low", "normal", "high", "urgent"]), field("channel", "Channel", "select", options=["manual", "email", "phone", "chat", "social"]), field("assigned_to", "Assigned to"), field("description", "Description", "textarea"), field("resolution", "Resolution", "textarea"), field("resolved_at", "Resolved", "datetime-local"),
        ]),
    ],
    "scheduling": [
        entity("appointments", "Appointments", "Appointment", ["title", "starts_at", "ends_at", "status", "assigned_to"], [
            field("contact_id", "Customer", "reference", referenceEntity="contacts", referenceLabel="name"), field("title", "Title", required=True), field("starts_at", "Starts", "datetime-local", required=True), field("ends_at", "Ends", "datetime-local"),
            field("status", "Status", "select", options=["scheduled", "confirmed", "completed", "cancelled", "no_show"]), field("assigned_to", "Assigned to"), field("notes", "Notes", "textarea"),
        ]),
    ],
    "tasks": [
        entity("tasks", "Tasks", "Task", ["title", "status", "due_at", "created_at"], [
            field("title", "Task", required=True), field("status", "Status", "select", options=["open", "in_progress", "blocked", "done", "cancelled"]), field("due_at", "Due", "datetime-local"),
        ]),
    ],
    "team": [
        entity("team", "Team Directory", "Team member", ["name", "email", "role", "active"], [
            field("name", "Name", required=True), field("email", "Email", "email"), field("role", "Operational role"), field("active", "Active", "checkbox"),
        ]),
        entity("leave-requests", "Leave / Time Off", "Leave request", ["team_member_id", "leave_type", "status", "starts_at", "ends_at", "approved_by"], [
            field("team_member_id", "Team member", "reference", referenceEntity="team", referenceLabel="name", required=True), field("leave_type", "Type"), field("status", "Status", "select", options=["requested", "approved", "rejected", "cancelled"]),
            field("starts_at", "Starts", "datetime-local", required=True), field("ends_at", "Ends", "datetime-local", required=True), field("reason", "Reason", "textarea"), field("approved_by", "Approved by"),
        ]),
    ],
    "documents": [
        entity("documents", "Documents & SOPs", "Document", ["title", "document_type", "status", "created_at"], [
            field("title", "Title", required=True), field("document_type", "Type", "select", options=["note", "sop", "policy", "procedure", "contract", "research_protocol", "template", "other"]), field("status", "Status", "select", options=["draft", "active", "archived"]), field("content", "Content", "textarea"),
        ]),
    ],
    "marketing": [
        entity("campaigns", "Campaigns", "Campaign", ["name", "channel", "status", "budget", "spent", "attributed_revenue", "starts_at"], [
            field("name", "Name", required=True), field("channel", "Channel"), field("status", "Status", "select", options=["draft", "scheduled", "active", "paused", "completed", "cancelled"]), field("budget", "Budget", "number"), field("spent", "Spent", "number"), field("attributed_revenue", "Attributed revenue", "number"), field("starts_at", "Starts", "datetime-local"), field("ends_at", "Ends", "datetime-local"), field("notes", "Notes", "textarea"),
        ]),
        entity("marketing-content", "Content Calendar", "Content item", ["title", "content_type", "channel", "status", "publish_at", "external_url"], [
            field("campaign_id", "Campaign", "reference", referenceEntity="campaigns", referenceLabel="name"), field("title", "Title", required=True), field("content_type", "Type", "select", options=["post", "email", "ad", "video", "article", "landing_page", "other"]),
            field("channel", "Channel"), field("status", "Status", "select", options=["idea", "draft", "review", "scheduled", "published", "archived"]), field("body", "Content / brief", "textarea"), field("publish_at", "Publish at", "datetime-local"), field("external_url", "Published URL"), field("notes", "Notes", "textarea"),
        ]),
    ],
    "compliance": [
        entity("risks", "Risk Register", "Risk", ["title", "category", "likelihood", "impact", "status", "owner", "due_at"], [
            field("title", "Risk", required=True), field("category", "Category"), field("likelihood", "Likelihood", "select", options=["low", "medium", "high", "critical"]), field("impact", "Impact", "select", options=["low", "medium", "high", "critical"]),
            field("status", "Status", "select", options=["open", "mitigating", "accepted", "closed"]), field("owner", "Owner"), field("mitigation", "Mitigation", "textarea"), field("due_at", "Due", "datetime-local"),
        ]),
        entity("incidents", "Incidents", "Incident", ["title", "incident_type", "severity", "status", "occurred_at", "owner"], [
            field("title", "Incident", required=True), field("incident_type", "Type"), field("severity", "Severity", "select", options=["low", "medium", "high", "critical"]), field("status", "Status", "select", options=["open", "investigating", "contained", "resolved", "closed"]),
            field("occurred_at", "Occurred", "datetime-local"), field("reported_by", "Reported by"), field("owner", "Owner"), field("description", "Description", "textarea"), field("resolution", "Resolution", "textarea"),
        ]),
        entity("audits", "Audits", "Audit", ["title", "audit_type", "status", "owner", "scheduled_at", "score"], [
            field("title", "Audit", required=True), field("audit_type", "Type"), field("status", "Status", "select", options=["planned", "in_progress", "completed", "cancelled"]), field("owner", "Owner"), field("scheduled_at", "Scheduled", "datetime-local"), field("completed_at", "Completed", "datetime-local"), field("score", "Score", "number"), field("findings", "Findings", "textarea"), field("corrective_actions", "Corrective actions", "textarea"),
        ]),
    ],
    "research": [
        entity("research-projects", "Research Projects", "Research project", ["code", "title", "field", "status", "principal_investigator", "ethics_status"], [
            field("code", "Code"), field("title", "Title", required=True), field("field", "Field / discipline"), field("status", "Status", "select", options=["planning", "active", "paused", "completed", "archived"]), field("principal_investigator", "Principal investigator"), field("starts_at", "Starts", "datetime-local"), field("ends_at", "Ends", "datetime-local"), field("ethics_status", "Ethics / IRB", "select", options=["not_required", "pending", "approved", "expired"]), field("funding_source", "Funding source"), field("objective", "Objective", "textarea"),
        ]),
        entity("experiments", "Experiments", "Experiment", ["research_project_id", "name", "status", "owner", "started_at", "completed_at"], [
            field("research_project_id", "Research project", "reference", referenceEntity="research-projects", referenceLabel="title", required=True), field("name", "Name", required=True), field("status", "Status", "select", options=["planned", "running", "paused", "completed", "failed", "archived"]), field("owner", "Owner"), field("started_at", "Started", "datetime-local"), field("completed_at", "Completed", "datetime-local"), field("hypothesis", "Hypothesis", "textarea"), field("protocol", "Protocol", "textarea"), field("result_summary", "Result summary", "textarea"),
        ]),
        entity("samples", "Samples", "Sample", ["sample_code", "sample_type", "status", "storage_location", "collected_at"], [
            field("research_project_id", "Research project", "reference", referenceEntity="research-projects", referenceLabel="title", required=True), field("experiment_id", "Experiment", "reference", referenceEntity="experiments", referenceLabel="name"), field("sample_code", "Sample code", required=True), field("sample_type", "Sample type"), field("status", "Status", "select", options=["active", "consumed", "disposed", "lost", "archived"]), field("storage_location", "Storage location"), field("collected_at", "Collected", "datetime-local"), field("metadata_json", "Metadata JSON", "textarea"), field("notes", "Notes", "textarea"),
        ]),
        entity("datasets", "Datasets", "Dataset", ["name", "version", "status", "storage_uri", "license"], [
            field("research_project_id", "Research project", "reference", referenceEntity="research-projects", referenceLabel="title", required=True), field("experiment_id", "Experiment", "reference", referenceEntity="experiments", referenceLabel="name"), field("name", "Name", required=True), field("version", "Version"), field("status", "Status", "select", options=["active", "frozen", "published", "archived"]), field("storage_uri", "Storage URI"), field("license", "License"), field("checksum", "Checksum"), field("description", "Description", "textarea"),
        ]),
    ],
    "grants": [
        entity("grants", "Grants & Funding", "Grant", ["funder", "program", "reference", "status", "amount", "submitted_at", "awarded_at"], [
            field("project_id", "Project", "reference", referenceEntity="projects", referenceLabel="name"), field("research_project_id", "Research project", "reference", referenceEntity="research-projects", referenceLabel="title"), field("funder", "Funder", required=True), field("program", "Program"), field("reference", "Reference"), field("status", "Status", "select", options=["prospect", "preparing", "submitted", "under_review", "awarded", "declined", "closed"]), field("amount", "Amount", "number"), field("currency", "Currency"), field("submitted_at", "Submitted", "datetime-local"), field("awarded_at", "Awarded", "datetime-local"), field("starts_at", "Starts", "datetime-local"), field("ends_at", "Ends", "datetime-local"), field("notes", "Notes", "textarea"),
        ]),
    ],
    "integrations": [
        entity("integrations", "Connections", "Connection", ["display_name", "provider", "status", "enabled", "health_status", "last_health_check"], [], read_only=True),
    ],
}


def module_manifest(module_key: str) -> dict[str, Any]:
    key = str(module_key or "").strip().lower()
    manifest = MODULE_CATALOG.get(key)
    if manifest is None:
        raise KeyError(f"Unknown workspace module: {module_key}")
    return {"key": key, **manifest, "entities": ENTITY_CATALOG.get(key, [])}


def preset_manifest(preset_key: str) -> dict[str, Any]:
    key = str(preset_key or "").strip().lower()
    preset = WORKSPACE_PRESETS.get(key)
    if preset is None:
        raise KeyError(f"Unknown workspace preset: {preset_key}")
    return {"key": key, **preset}
