import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import { api } from "../api";


type ModuleInfo = {
  key: string;
  name: string;
  category: string;
  description: string;
  enabled: boolean;
  locked?: boolean;
  dependencies: string[];
  can_read: boolean;
  can_write: boolean;
  can_manage: boolean;
};

type WorkspaceContext = {
  workspace: { id: string; name: string; slug?: string | null; timezone: string; logo_url?: string | null };
  user: { id: string; email: string; display_name: string };
  role: string;
  permissions: string[];
  modules: ModuleInfo[];
};

type Summary = {
  contacts: number;
  open_leads: number;
  pipeline_value: number;
  orders: number;
  sales_total: number;
  invoice_total: number;
  expenses_total: number;
  net_operating: number;
  products: number;
  low_stock: number;
  open_tickets: number;
  upcoming_appointments: number;
};

type RecordPage = { items: Record<string, unknown>[]; total: number; limit: number; offset: number };
type Member = { user_id: string; display_name: string; email: string; role: string };
type RoleInfo = { key: string; name: string; system: boolean; permissions: string[] };
type RolesResponse = { roles: RoleInfo[]; known_permissions: string[] };

type FieldType = "text" | "email" | "number" | "textarea" | "datetime-local" | "select" | "checkbox" | "reference";
type FieldDef = {
  key: string;
  label: string;
  type?: FieldType;
  required?: boolean;
  options?: string[];
  referenceEntity?: string;
  referenceLabel?: string;
  placeholder?: string;
};
type EntityDef = {
  entity: string;
  label: string;
  singular: string;
  columns: string[];
  fields: FieldDef[];
  readOnly?: boolean;
};

const STATUS_OPTIONS = ["active", "inactive"];
const LEAD_STAGES = ["new", "qualified", "proposal", "negotiation", "won", "lost"];
const ORDER_STATUSES = ["draft", "pending", "confirmed", "paid", "fulfilled", "cancelled"];
const QUOTE_STATUSES = ["draft", "sent", "accepted", "rejected", "expired"];
const INVOICE_STATUSES = ["draft", "sent", "due", "paid", "overdue", "void"];
const TICKET_STATUSES = ["open", "pending", "resolved", "closed"];

const ENTITY_DEFS: Record<string, EntityDef[]> = {
  crm: [
    {
      entity: "contacts", label: "Contacts", singular: "Contact", columns: ["name", "company", "email", "phone", "status"],
      fields: [
        { key: "name", label: "Name", required: true }, { key: "company", label: "Company" },
        { key: "email", label: "Email", type: "email" }, { key: "phone", label: "Phone" },
        { key: "source", label: "Source", placeholder: "manual, referral, website…" },
        { key: "status", label: "Status", type: "select", options: STATUS_OPTIONS },
        { key: "notes", label: "Notes", type: "textarea" },
      ],
    },
    {
      entity: "leads", label: "Pipeline", singular: "Lead", columns: ["title", "stage", "value", "assigned_to", "next_action"],
      fields: [
        { key: "contact_id", label: "Contact", type: "reference", referenceEntity: "contacts", referenceLabel: "name" },
        { key: "title", label: "Opportunity", required: true },
        { key: "stage", label: "Stage", type: "select", options: LEAD_STAGES },
        { key: "value", label: "Value", type: "number" }, { key: "assigned_to", label: "Owner" },
        { key: "next_action", label: "Next action", type: "textarea" },
        { key: "last_contacted_at", label: "Last contacted", type: "datetime-local" },
        { key: "next_action_at", label: "Next action time", type: "datetime-local" },
      ],
    },
  ],
  catalog: [
    {
      entity: "catalog", label: "Products & Services", singular: "Item", columns: ["name", "sku", "item_type", "price", "cost", "stock_qty", "active"],
      fields: [
        { key: "name", label: "Name", required: true }, { key: "sku", label: "SKU" },
        { key: "item_type", label: "Type", type: "select", options: ["product", "service"] },
        { key: "price", label: "Price", type: "number" }, { key: "cost", label: "Cost", type: "number" },
        { key: "stock_qty", label: "Opening stock", type: "number" }, { key: "reorder_level", label: "Reorder level", type: "number" },
        { key: "active", label: "Active", type: "checkbox" },
      ],
    },
  ],
  sales: [
    {
      entity: "orders", label: "Orders", singular: "Order", columns: ["status", "total", "contact_id", "created_at"],
      fields: [
        { key: "contact_id", label: "Customer", type: "reference", referenceEntity: "contacts", referenceLabel: "name" },
        { key: "status", label: "Status", type: "select", options: ORDER_STATUSES }, { key: "total", label: "Total", type: "number" },
        { key: "notes", label: "Notes", type: "textarea" },
      ],
    },
    {
      entity: "quotes", label: "Quotes", singular: "Quote", columns: ["title", "status", "total", "valid_until"],
      fields: [
        { key: "contact_id", label: "Customer", type: "reference", referenceEntity: "contacts", referenceLabel: "name" },
        { key: "title", label: "Title", required: true }, { key: "status", label: "Status", type: "select", options: QUOTE_STATUSES },
        { key: "total", label: "Total", type: "number" }, { key: "valid_until", label: "Valid until", type: "datetime-local" },
        { key: "notes", label: "Notes", type: "textarea" },
      ],
    },
  ],
  finance: [
    {
      entity: "invoices", label: "Invoices", singular: "Invoice", columns: ["number", "status", "total", "due_at", "paid_at"],
      fields: [
        { key: "contact_id", label: "Customer", type: "reference", referenceEntity: "contacts", referenceLabel: "name" },
        { key: "order_id", label: "Order", type: "reference", referenceEntity: "orders", referenceLabel: "id" },
        { key: "number", label: "Invoice number", required: true }, { key: "status", label: "Status", type: "select", options: INVOICE_STATUSES },
        { key: "currency", label: "Currency", placeholder: "USD" }, { key: "subtotal", label: "Subtotal", type: "number" },
        { key: "tax", label: "Tax", type: "number" }, { key: "total", label: "Total", type: "number" },
        { key: "due_at", label: "Due", type: "datetime-local" }, { key: "paid_at", label: "Paid at", type: "datetime-local" },
        { key: "notes", label: "Notes", type: "textarea" },
      ],
    },
    {
      entity: "expenses", label: "Expenses", singular: "Expense", columns: ["vendor", "category", "amount", "currency", "incurred_at", "status"],
      fields: [
        { key: "vendor", label: "Vendor", required: true }, { key: "category", label: "Category" },
        { key: "amount", label: "Amount", type: "number" }, { key: "currency", label: "Currency", placeholder: "USD" },
        { key: "status", label: "Status", type: "select", options: ["posted", "pending", "void"] },
        { key: "incurred_at", label: "Date", type: "datetime-local" }, { key: "notes", label: "Notes", type: "textarea" },
      ],
    },
  ],
  suppliers: [
    {
      entity: "suppliers", label: "Suppliers", singular: "Supplier", columns: ["name", "status", "lead_time_days", "minimum_order_value", "email"],
      fields: [
        { key: "name", label: "Name", required: true }, { key: "email", label: "Email", type: "email" },
        { key: "phone", label: "Phone" }, { key: "website", label: "Website" },
        { key: "status", label: "Status", type: "select", options: STATUS_OPTIONS },
        { key: "lead_time_days", label: "Lead time (days)", type: "number" },
        { key: "minimum_order_value", label: "Minimum order", type: "number" }, { key: "currency", label: "Currency", placeholder: "USD" },
        { key: "notes", label: "Notes", type: "textarea" },
      ],
    },
    {
      entity: "purchase-orders", label: "Purchase Orders", singular: "Purchase order", columns: ["reference", "status", "supplier_id", "total", "expected_at"],
      fields: [
        { key: "supplier_id", label: "Supplier", type: "reference", referenceEntity: "suppliers", referenceLabel: "name" },
        { key: "reference", label: "Reference", required: true }, { key: "status", label: "Status", type: "select", options: ["draft", "ordered", "partially_received", "received", "cancelled"] },
        { key: "currency", label: "Currency", placeholder: "USD" }, { key: "subtotal", label: "Subtotal", type: "number" },
        { key: "shipping_cost", label: "Shipping", type: "number" }, { key: "total", label: "Total", type: "number" },
        { key: "expected_at", label: "Expected", type: "datetime-local" }, { key: "received_at", label: "Received", type: "datetime-local" },
        { key: "notes", label: "Notes", type: "textarea" },
      ],
    },
  ],
  fulfillment: [
    {
      entity: "fulfillments", label: "Fulfillments", singular: "Fulfillment", columns: ["order_id", "method", "status", "carrier", "tracking_number", "delivered_at"],
      fields: [
        { key: "order_id", label: "Order", type: "reference", referenceEntity: "orders", referenceLabel: "id", required: true },
        { key: "supplier_id", label: "Supplier", type: "reference", referenceEntity: "suppliers", referenceLabel: "name" },
        { key: "method", label: "Method", type: "select", options: ["in_house", "dropship", "3pl"] },
        { key: "status", label: "Status", type: "select", options: ["pending", "processing", "shipped", "delivered", "cancelled"] },
        { key: "carrier", label: "Carrier" }, { key: "tracking_number", label: "Tracking number" },
        { key: "fulfillment_cost", label: "Cost", type: "number" }, { key: "shipped_at", label: "Shipped", type: "datetime-local" },
        { key: "delivered_at", label: "Delivered", type: "datetime-local" }, { key: "notes", label: "Notes", type: "textarea" },
      ],
    },
    {
      entity: "returns", label: "Returns", singular: "Return", columns: ["order_id", "status", "reason", "refund_amount", "received_back"],
      fields: [
        { key: "order_id", label: "Order", type: "reference", referenceEntity: "orders", referenceLabel: "id", required: true },
        { key: "fulfillment_id", label: "Fulfillment", type: "reference", referenceEntity: "fulfillments", referenceLabel: "tracking_number" },
        { key: "status", label: "Status", type: "select", options: ["requested", "approved", "in_transit", "received", "refunded", "rejected"] },
        { key: "reason", label: "Reason" }, { key: "refund_amount", label: "Refund", type: "number" },
        { key: "currency", label: "Currency", placeholder: "USD" }, { key: "received_back", label: "Received back", type: "checkbox" },
        { key: "notes", label: "Notes", type: "textarea" },
      ],
    },
  ],
  support: [
    {
      entity: "tickets", label: "Tickets", singular: "Ticket", columns: ["subject", "status", "priority", "channel", "assigned_to", "opened_at"],
      fields: [
        { key: "contact_id", label: "Customer", type: "reference", referenceEntity: "contacts", referenceLabel: "name" },
        { key: "subject", label: "Subject", required: true }, { key: "status", label: "Status", type: "select", options: TICKET_STATUSES },
        { key: "priority", label: "Priority", type: "select", options: ["low", "normal", "high", "urgent"] },
        { key: "channel", label: "Channel", type: "select", options: ["manual", "email", "phone", "chat", "social"] },
        { key: "assigned_to", label: "Assigned to" }, { key: "description", label: "Description", type: "textarea" },
        { key: "resolution", label: "Resolution", type: "textarea" }, { key: "resolved_at", label: "Resolved", type: "datetime-local" },
      ],
    },
  ],
  scheduling: [
    {
      entity: "appointments", label: "Appointments", singular: "Appointment", columns: ["title", "starts_at", "ends_at", "status", "assigned_to"],
      fields: [
        { key: "contact_id", label: "Customer", type: "reference", referenceEntity: "contacts", referenceLabel: "name" },
        { key: "title", label: "Title", required: true }, { key: "starts_at", label: "Starts", type: "datetime-local", required: true },
        { key: "ends_at", label: "Ends", type: "datetime-local" }, { key: "status", label: "Status", type: "select", options: ["scheduled", "confirmed", "completed", "cancelled", "no_show"] },
        { key: "assigned_to", label: "Assigned to" }, { key: "notes", label: "Notes", type: "textarea" },
      ],
    },
  ],
  tasks: [
    {
      entity: "tasks", label: "Tasks", singular: "Task", columns: ["title", "status", "due_at", "created_at"],
      fields: [
        { key: "title", label: "Task", required: true }, { key: "status", label: "Status", type: "select", options: ["open", "in_progress", "blocked", "done", "cancelled"] },
        { key: "due_at", label: "Due", type: "datetime-local" },
      ],
    },
  ],
  team: [
    {
      entity: "team", label: "Team Directory", singular: "Team member", columns: ["name", "email", "role", "active"],
      fields: [
        { key: "name", label: "Name", required: true }, { key: "email", label: "Email", type: "email" },
        { key: "role", label: "Operational role" }, { key: "active", label: "Active", type: "checkbox" },
      ],
    },
  ],
  documents: [
    {
      entity: "documents", label: "Documents & SOPs", singular: "Document", columns: ["title", "document_type", "status", "created_at"],
      fields: [
        { key: "title", label: "Title", required: true }, { key: "document_type", label: "Type", type: "select", options: ["note", "sop", "policy", "template", "contract"] },
        { key: "status", label: "Status", type: "select", options: ["draft", "active", "archived"] }, { key: "content", label: "Content", type: "textarea" },
      ],
    },
  ],
  marketing: [
    {
      entity: "campaigns", label: "Campaigns", singular: "Campaign", columns: ["name", "channel", "status", "budget", "spent", "attributed_revenue"],
      fields: [
        { key: "name", label: "Name", required: true }, { key: "channel", label: "Channel", type: "select", options: ["email", "sms", "social", "search", "display", "affiliate", "other"] },
        { key: "status", label: "Status", type: "select", options: ["draft", "scheduled", "active", "paused", "completed"] },
        { key: "budget", label: "Budget", type: "number" }, { key: "spent", label: "Spent", type: "number" },
        { key: "attributed_revenue", label: "Attributed revenue", type: "number" }, { key: "starts_at", label: "Starts", type: "datetime-local" },
        { key: "ends_at", label: "Ends", type: "datetime-local" }, { key: "notes", label: "Notes", type: "textarea" },
      ],
    },
  ],
  integrations: [
    {
      entity: "integrations", label: "Connections", singular: "Connection", columns: ["display_name", "provider", "status", "enabled", "health_status", "last_health_check"],
      fields: [], readOnly: true,
    },
  ],
};

function errorText(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

function pathSection(pathname: string, workspaceId: string): string {
  const prefix = `/channels/${encodeURIComponent(workspaceId)}`;
  if (!pathname.startsWith(prefix)) return "dashboard";
  const remainder = pathname.slice(prefix.length).replace(/^\/+/, "");
  return remainder.split("/", 1)[0] || "dashboard";
}

function workspacePath(workspaceId: string, section: string): string {
  return `/channels/${encodeURIComponent(workspaceId)}/${section}`;
}

function humanize(key: string): string {
  return key.replaceAll("_", " ").replaceAll("-", " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

function formatValue(key: string, value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") {
    if (["price", "cost", "value", "total", "subtotal", "tax", "amount", "budget", "spent", "attributed_revenue", "minimum_order_value", "refund_amount", "fulfillment_cost"].includes(key)) {
      return new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(value);
    }
    return new Intl.NumberFormat().format(value);
  }
  const text = String(value);
  if (key.endsWith("_at") || key === "created_at" || key === "updated_at") {
    const date = new Date(text);
    if (!Number.isNaN(date.getTime())) return date.toLocaleString();
  }
  if (key.endsWith("_id") && text.length > 14) return `${text.slice(0, 8)}…${text.slice(-4)}`;
  return text;
}

function Modal({ children, onClose }: { children: React.ReactNode; onClose: () => void }) {
  return <div className="workspace-os-modal-backdrop" onMouseDown={onClose}><div className="workspace-os-modal" onMouseDown={(event) => event.stopPropagation()}>{children}</div></div>;
}

function ReferenceField({ field, defaultValue }: { field: FieldDef; defaultValue: unknown }) {
  const [items, setItems] = useState<Record<string, unknown>[]>([]);
  useEffect(() => {
    if (!field.referenceEntity) return;
    api<RecordPage>(`/workspace-os/records/${field.referenceEntity}?limit=200&direction=asc`)
      .then((result) => setItems(result.items))
      .catch(() => setItems([]));
  }, [field.referenceEntity]);
  return <select name={field.key} defaultValue={String(defaultValue ?? "")} required={field.required}>
    <option value="">None</option>
    {items.map((item) => {
      const id = String(item.id ?? "");
      const labelKey = field.referenceLabel || "name";
      const label = String(item[labelKey] ?? item.id ?? "Record");
      return <option key={id} value={id}>{label}</option>;
    })}
  </select>;
}

function RecordEditor({ def, record, onClose, onSaved }: { def: EntityDef; record?: Record<string, unknown>; onClose: () => void; onSaved: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    const form = new FormData(event.currentTarget);
    const payload: Record<string, unknown> = {};
    for (const field of def.fields) {
      if (field.type === "checkbox") payload[field.key] = form.get(field.key) === "on";
      else {
        const value = form.get(field.key);
        if (value !== null && String(value) !== "") payload[field.key] = value;
        else if (record && field.key in record) payload[field.key] = null;
      }
    }
    try {
      await api(`/workspace-os/records/${def.entity}${record?.id ? `/${String(record.id)}` : ""}`, {
        method: record?.id ? "PATCH" : "POST",
        body: JSON.stringify(payload),
      });
      onSaved();
      onClose();
    } catch (caught) {
      setError(errorText(caught, `Could not save ${def.singular.toLowerCase()}`));
    } finally {
      setBusy(false);
    }
  };
  return <Modal onClose={onClose}>
    <form className="workspace-os-form" onSubmit={submit}>
      <div className="workspace-os-modal-heading"><div><span>{record ? "EDIT" : "NEW"}</span><h2>{record ? `Edit ${def.singular}` : `Create ${def.singular}`}</h2></div><button type="button" onClick={onClose}>×</button></div>
      <div className="workspace-os-form-grid">
        {def.fields.map((field) => {
          const value = record?.[field.key];
          return <label key={field.key} className={field.type === "textarea" ? "wide" : ""}>
            <span>{field.label}</span>
            {field.type === "textarea" ? <textarea name={field.key} defaultValue={String(value ?? "")} rows={5} required={field.required} placeholder={field.placeholder} />
              : field.type === "select" ? <select name={field.key} defaultValue={String(value ?? field.options?.[0] ?? "")} required={field.required}>{field.options?.map((option) => <option key={option} value={option}>{humanize(option)}</option>)}</select>
              : field.type === "reference" ? <ReferenceField field={field} defaultValue={value} />
              : field.type === "checkbox" ? <input name={field.key} type="checkbox" defaultChecked={value === undefined ? true : Boolean(value)} />
              : <input name={field.key} type={field.type || "text"} defaultValue={String(value ?? "")} required={field.required} placeholder={field.placeholder} step={field.type === "number" ? "any" : undefined} />}
          </label>;
        })}
      </div>
      {error && <div className="workspace-os-error">{error}</div>}
      <div className="workspace-os-form-actions"><button type="button" onClick={onClose}>Cancel</button><button className="primary" disabled={busy}>{busy ? "Saving…" : "Save"}</button></div>
    </form>
  </Modal>;
}

function RecordTable({ def, writable }: { def: EntityDef; writable: boolean }) {
  const [data, setData] = useState<RecordPage>({ items: [], total: 0, limit: 50, offset: 0 });
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [editing, setEditing] = useState<Record<string, unknown> | null | undefined>(undefined);
  const [error, setError] = useState("");

  const load = useCallback(async (offset = data.offset) => {
    setLoading(true);
    setError("");
    try {
      const result = await api<RecordPage>(`/workspace-os/records/${def.entity}?limit=50&offset=${offset}&q=${encodeURIComponent(query)}`);
      setData(result);
    } catch (caught) {
      setError(errorText(caught, `Could not load ${def.label.toLowerCase()}`));
    } finally {
      setLoading(false);
    }
  }, [data.offset, def.entity, def.label, query]);

  useEffect(() => { void load(0); }, [def.entity, query]);

  const remove = async (record: Record<string, unknown>) => {
    if (!record.id || !window.confirm(`Delete this ${def.singular.toLowerCase()}?`)) return;
    try {
      await api(`/workspace-os/records/${def.entity}/${String(record.id)}`, { method: "DELETE" });
      await load(data.offset);
    } catch (caught) {
      setError(errorText(caught, "Could not delete record"));
    }
  };

  return <section className="workspace-os-records">
    <div className="workspace-os-toolbar">
      <div><h2>{def.label}</h2><span>{data.total} record{data.total === 1 ? "" : "s"}</span></div>
      <div className="workspace-os-toolbar-actions">
        <form onSubmit={(event) => { event.preventDefault(); setQuery(q.trim()); }}><input value={q} onChange={(event) => setQ(event.target.value)} placeholder={`Search ${def.label.toLowerCase()}…`} /><button>Search</button></form>
        {writable && !def.readOnly && <button className="primary" onClick={() => setEditing(null)}>+ New {def.singular}</button>}
      </div>
    </div>
    {error && <div className="workspace-os-error">{error}</div>}
    <div className="workspace-os-table-wrap">
      <table><thead><tr>{def.columns.map((column) => <th key={column}>{humanize(column)}</th>)}{writable && !def.readOnly && <th />}</tr></thead>
        <tbody>{loading ? <tr><td colSpan={def.columns.length + 1}>Loading…</td></tr> : data.items.length === 0 ? <tr><td className="workspace-os-empty" colSpan={def.columns.length + 1}>No records yet.</td></tr> : data.items.map((record) => <tr key={String(record.id)}>
          {def.columns.map((column) => <td key={column}>{formatValue(column, record[column])}</td>)}
          {writable && !def.readOnly && <td className="workspace-os-row-actions"><button onClick={() => setEditing(record)}>Edit</button><button onClick={() => void remove(record)}>Delete</button></td>}
        </tr>)}</tbody></table>
    </div>
    {data.total > data.limit && <div className="workspace-os-pagination"><button disabled={data.offset === 0} onClick={() => void load(Math.max(0, data.offset - data.limit))}>Previous</button><span>{data.offset + 1}–{Math.min(data.total, data.offset + data.limit)} of {data.total}</span><button disabled={data.offset + data.limit >= data.total} onClick={() => void load(data.offset + data.limit)}>Next</button></div>}
    {editing !== undefined && <RecordEditor def={def} record={editing || undefined} onClose={() => setEditing(undefined)} onSaved={() => void load(data.offset)} />}
  </section>;
}

function InventoryView({ writable }: { writable: boolean }) {
  const [data, setData] = useState<RecordPage>({ items: [], total: 0, limit: 100, offset: 0 });
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try { setData(await api<RecordPage>("/workspace-os/records/catalog?limit=200&direction=asc&sort=name")); }
    catch (caught) { setError(errorText(caught, "Could not load inventory")); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const adjust = async (itemId: string, amount: number, reason: string) => {
    try {
      await api(`/workspace-os/inventory/${itemId}/adjust`, { method: "POST", body: JSON.stringify({ quantity_change: amount, reason }) });
      await load();
    } catch (caught) { setError(errorText(caught, "Could not adjust inventory")); }
  };
  return <section className="workspace-os-records"><div className="workspace-os-toolbar"><div><h2>Inventory</h2><span>{data.total} catalog items</span></div></div>{error && <div className="workspace-os-error">{error}</div>}
    <div className="workspace-os-inventory-grid">{data.items.map((item) => <article key={String(item.id)} className={Number(item.stock_qty) <= Number(item.reorder_level) ? "low" : ""}>
      <div><strong>{String(item.name ?? "Item")}</strong><span>{String(item.sku ?? "No SKU")}</span></div><div className="workspace-os-stock"><b>{Number(item.stock_qty || 0)}</b><small>in stock · reorder at {Number(item.reorder_level || 0)}</small></div>
      {writable && <InventoryAdjust onAdjust={(amount, reason) => adjust(String(item.id), amount, reason)} />}
    </article>)}</div>
  </section>;
}

function InventoryAdjust({ onAdjust }: { onAdjust: (amount: number, reason: string) => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); const form = new FormData(event.currentTarget); const amount = Number(form.get("amount")); if (!amount) return;
    setBusy(true); await onAdjust(amount, String(form.get("reason") || "adjustment")); setBusy(false); event.currentTarget.reset();
  };
  return <form className="workspace-os-adjust" onSubmit={submit}><input name="amount" type="number" placeholder="± qty" required /><input name="reason" placeholder="reason" /><button disabled={busy}>{busy ? "…" : "Adjust"}</button></form>;
}

function Dashboard({ summary, modules }: { summary: Summary | null; modules: ModuleInfo[] }) {
  const cards = [
    ["Open leads", summary?.open_leads ?? 0], ["Pipeline", summary ? formatValue("value", summary.pipeline_value) : "—"],
    ["Sales", summary ? formatValue("total", summary.sales_total) : "—"], ["Net operating", summary ? formatValue("total", summary.net_operating) : "—"],
    ["Low stock", summary?.low_stock ?? 0], ["Open tickets", summary?.open_tickets ?? 0],
    ["Upcoming appointments", summary?.upcoming_appointments ?? 0], ["Contacts", summary?.contacts ?? 0],
  ];
  return <div className="workspace-os-dashboard"><section className="workspace-os-hero"><span>WORKSPACE OVERVIEW</span><h1>Operate the business from one place.</h1><p>Native business modules are live independently of the AI runtime. Enable only what this workspace needs.</p></section>
    <div className="workspace-os-metrics">{cards.map(([label, value]) => <article key={String(label)}><span>{label}</span><strong>{value}</strong></article>)}</div>
    <section className="workspace-os-module-cards"><div className="workspace-os-section-title"><h2>Active tools</h2><a href="#modules">Manage in Settings</a></div><div>{modules.filter((module) => module.enabled && module.can_read && module.key !== "dashboard").map((module) => <a key={module.key} href={module.key}><strong>{module.name}</strong><span>{module.description}</span></a>)}</div></section>
  </div>;
}

function ModulePage({ module }: { module: ModuleInfo }) {
  const defs = ENTITY_DEFS[module.key] || [];
  const [activeEntity, setActiveEntity] = useState(defs[0]?.entity || "");
  useEffect(() => setActiveEntity(defs[0]?.entity || ""), [module.key]);
  if (module.key === "inventory") return <InventoryView writable={module.can_write} />;
  if (defs.length === 0) return <section className="workspace-os-blank"><h1>{module.name}</h1><p>{module.description}</p></section>;
  const def = defs.find((candidate) => candidate.entity === activeEntity) || defs[0];
  return <div className="workspace-os-module-page">
    <header className="workspace-os-module-header"><div><span>{module.category.toUpperCase()}</span><h1>{module.name}</h1><p>{module.description}</p></div>{defs.length > 1 && <nav>{defs.map((candidate) => <button key={candidate.entity} className={candidate.entity === def.entity ? "active" : ""} onClick={() => setActiveEntity(candidate.entity)}>{candidate.label}</button>)}</nav>}</header>
    <RecordTable def={def} writable={module.can_write} />
  </div>;
}

function GeneralSettings({ context, onReload }: { context: WorkspaceContext; onReload: () => Promise<void> }) {
  const canManage = context.permissions.includes("workspace:settings:manage") || context.role === "owner";
  const [busy, setBusy] = useState(false); const [error, setError] = useState(""); const [saved, setSaved] = useState(false);
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); const form = new FormData(event.currentTarget); setBusy(true); setError(""); setSaved(false);
    try { await api("/workspace-os/settings", { method: "PATCH", body: JSON.stringify({ name: form.get("name"), timezone: form.get("timezone"), logo_url: form.get("logo_url") }) }); await onReload(); setSaved(true); }
    catch (caught) { setError(errorText(caught, "Could not update workspace")); } finally { setBusy(false); }
  };
  return <form className="workspace-os-settings-card" onSubmit={submit}><h2>Workspace identity</h2><p>Name, timezone and visual identity are shared by everyone in this workspace.</p>
    <label><span>Name</span><input name="name" defaultValue={context.workspace.name} disabled={!canManage} required /></label>
    <label><span>Timezone</span><input name="timezone" defaultValue={context.workspace.timezone} disabled={!canManage} required /></label>
    <label><span>Logo URL</span><input name="logo_url" defaultValue={context.workspace.logo_url || ""} disabled={!canManage} placeholder="https://…" /></label>
    {error && <div className="workspace-os-error">{error}</div>}{saved && <div className="workspace-os-success">Workspace updated.</div>}
    {canManage && <button className="primary" disabled={busy}>{busy ? "Saving…" : "Save workspace"}</button>}
  </form>;
}

function ModuleSettings({ context, onReload }: { context: WorkspaceContext; onReload: () => Promise<void> }) {
  const [busy, setBusy] = useState(""); const [error, setError] = useState("");
  const toggle = async (module: ModuleInfo) => {
    setBusy(module.key); setError(""); try { await api(`/workspace-os/modules/${module.key}`, { method: "PUT", body: JSON.stringify({ enabled: !module.enabled, configuration: {} }) }); await onReload(); }
    catch (caught) { setError(errorText(caught, "Could not change module")); } finally { setBusy(""); }
  };
  return <div className="workspace-os-settings-card"><h2>Business modules</h2><p>Modules are installed once in Operly and activated per workspace. Dependencies are enabled automatically.</p>{error && <div className="workspace-os-error">{error}</div>}
    <div className="workspace-os-settings-modules">{context.modules.filter((module) => module.key !== "dashboard").map((module) => <article key={module.key}><div><strong>{module.name}</strong><span>{module.description}</span>{module.dependencies.length > 0 && <small>Requires {module.dependencies.join(", ")}</small>}</div><button className={module.enabled ? "on" : ""} disabled={Boolean(module.locked) || !module.can_manage || busy === module.key} onClick={() => void toggle(module)}>{busy === module.key ? "…" : module.enabled ? "Enabled" : "Enable"}</button></article>)}</div>
  </div>;
}

function MembersSettings({ context }: { context: WorkspaceContext }) {
  const [members, setMembers] = useState<Member[]>([]); const [roles, setRoles] = useState<RoleInfo[]>([]); const [error, setError] = useState("");
  const canManage = context.permissions.includes("workspace:members:manage") || context.role === "owner";
  const load = useCallback(async () => {
    try { const [memberRows, roleRows] = await Promise.all([api<Member[]>("/workspace-os/members"), api<RolesResponse>("/workspace-os/roles")]); setMembers(memberRows); setRoles(roleRows.roles); }
    catch (caught) { setError(errorText(caught, "Could not load members")); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const add = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); const form = new FormData(event.currentTarget); setError("");
    try { await api("/workspace-os/members", { method: "POST", body: JSON.stringify({ email: form.get("email"), role: form.get("role") }) }); event.currentTarget.reset(); await load(); }
    catch (caught) { setError(errorText(caught, "Could not add member")); }
  };
  const setRole = async (userId: string, role: string) => { try { await api(`/workspace-os/members/${userId}`, { method: "PATCH", body: JSON.stringify({ role }) }); await load(); } catch (caught) { setError(errorText(caught, "Could not change role")); } };
  const remove = async (member: Member) => { if (!window.confirm(`Remove ${member.display_name || member.email} from this workspace?`)) return; try { await api(`/workspace-os/members/${member.user_id}`, { method: "DELETE" }); await load(); } catch (caught) { setError(errorText(caught, "Could not remove member")); } };
  return <div className="workspace-os-settings-card"><h2>Members & access</h2><p>Workspace membership is the authority boundary. A session can only operate the workspace currently selected and the member's role determines what can be changed.</p>{error && <div className="workspace-os-error">{error}</div>}
    {canManage && <form className="workspace-os-member-add" onSubmit={add}><input name="email" type="email" placeholder="Existing Operly account email" required /><select name="role" defaultValue="employee">{roles.map((role) => <option key={role.key} value={role.key}>{role.name}</option>)}</select><button className="primary">Add member</button></form>}
    <div className="workspace-os-members">{members.map((member) => <article key={member.user_id}><div><strong>{member.display_name || member.email}</strong><span>{member.email}</span></div>{canManage ? <><select value={member.role} onChange={(event) => void setRole(member.user_id, event.target.value)}>{roles.map((role) => <option key={role.key} value={role.key}>{role.name}</option>)}</select><button onClick={() => void remove(member)}>Remove</button></> : <b>{humanize(member.role)}</b>}</article>)}</div>
  </div>;
}

function RolesSettings({ context }: { context: WorkspaceContext }) {
  const [data, setData] = useState<RolesResponse>({ roles: [], known_permissions: [] }); const [selected, setSelected] = useState(""); const [selectedPermissions, setSelectedPermissions] = useState<Set<string>>(new Set()); const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  const canManage = context.permissions.includes("workspace:roles:manage") || context.role === "owner";
  const load = useCallback(async () => { try { const result = await api<RolesResponse>("/workspace-os/roles"); setData(result); setSelected((value) => value || result.roles[0]?.key || ""); } catch (caught) { setError(errorText(caught, "Could not load roles")); } }, []);
  useEffect(() => { void load(); }, [load]);
  const role = data.roles.find((candidate) => candidate.key === selected);
  useEffect(() => { setSelectedPermissions(new Set(role?.permissions || [])); }, [role?.key, role?.permissions.join("|")]);
  const save = async () => { if (!role) return; setBusy(true); setError(""); try { await api(`/workspace-os/roles/${role.key}`, { method: "PUT", body: JSON.stringify({ name: role.name, permissions: [...selectedPermissions] }) }); await load(); } catch (caught) { setError(errorText(caught, "Could not save role")); } finally { setBusy(false); } };
  const create = async (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); const form = new FormData(event.currentTarget); const name = String(form.get("name") || "").trim(); const key = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, ""); if (!key) return; try { await api(`/workspace-os/roles/${key}`, { method: "PUT", body: JSON.stringify({ name, permissions: ["workspace:read"] }) }); await load(); setSelected(key); event.currentTarget.reset(); } catch (caught) { setError(errorText(caught, "Could not create role")); } };
  const groups = useMemo(() => { const result: Record<string, string[]> = {}; for (const permission of data.known_permissions) { const group = permission.split(":", 1)[0]; (result[group] ||= []).push(permission); } return result; }, [data.known_permissions]);
  return <div className="workspace-os-settings-card"><h2>Roles & permissions</h2><p>Built-in roles keep their minimum Operly defaults. Create a custom role when you need a narrower permission set.</p>{error && <div className="workspace-os-error">{error}</div>}
    {canManage && <form className="workspace-os-role-add" onSubmit={create}><input name="name" placeholder="New custom role" required /><button>Create role</button></form>}
    <div className="workspace-os-role-layout"><nav>{data.roles.map((item) => <button key={item.key} className={selected === item.key ? "active" : ""} onClick={() => setSelected(item.key)}><strong>{item.name}</strong><small>{item.system ? "Built-in" : "Custom"}</small></button>)}</nav>
      {role && <div className="workspace-os-permissions"><h3>{role.name}</h3>{Object.entries(groups).map(([group, permissions]) => <fieldset key={group}><legend>{humanize(group)}</legend>{permissions.map((permission) => <label key={permission}><input type="checkbox" checked={selectedPermissions.has(permission)} disabled={!canManage} onChange={(event) => setSelectedPermissions((current) => { const next = new Set(current); if (event.target.checked) next.add(permission); else next.delete(permission); return next; })} /><span>{permission}</span></label>)}</fieldset>)}{canManage && <button className="primary" disabled={busy} onClick={() => void save()}>{busy ? "Saving…" : "Save permissions"}</button>}</div>}
    </div>
  </div>;
}

function Settings({ context, onReload }: { context: WorkspaceContext; onReload: () => Promise<void> }) {
  const [tab, setTab] = useState("general");
  return <div className="workspace-os-settings"><header><span>WORKSPACE SETTINGS</span><h1>{context.workspace.name}</h1><p>Changes are enforced by the same workspace membership and role authority used by the session.</p></header>
    <nav className="workspace-os-settings-tabs">{[["general", "General"], ["modules", "Modules"], ["members", "Members"], ["roles", "Roles & permissions"]].map(([key, label]) => <button key={key} className={tab === key ? "active" : ""} onClick={() => setTab(key)}>{label}</button>)}</nav>
    {tab === "general" && <GeneralSettings context={context} onReload={onReload} />}{tab === "modules" && <ModuleSettings context={context} onReload={onReload} />}{tab === "members" && <MembersSettings context={context} />}{tab === "roles" && <RolesSettings context={context} />}
  </div>;
}

export function WorkspaceOSPanel({ workspaceId, pathname }: { workspaceId: string; pathname: string }) {
  const [context, setContext] = useState<WorkspaceContext | null>(null); const [summary, setSummary] = useState<Summary | null>(null); const [error, setError] = useState("");
  const section = pathSection(pathname, workspaceId);
  const reload = useCallback(async () => {
    setError("");
    try { const current = await api<WorkspaceContext>("/workspace-os/context"); if (current.workspace.id !== workspaceId) throw new Error("Workspace session is still switching"); setContext(current); setSummary(await api<Summary>("/workspace-os/summary")); }
    catch (caught) { setError(errorText(caught, "Could not open workspace")); }
  }, [workspaceId]);
  useEffect(() => { void reload(); }, [reload]);

  if (error && !context) return <div className="workspace-os-fatal"><h1>Workspace unavailable</h1><p>{error}</p><button onClick={() => void reload()}>Retry</button></div>;
  if (!context) return <div className="workspace-os-loading">Loading workspace…</div>;
  const activeModule = context.modules.find((module) => module.key === section);
  const readableModules = context.modules.filter((module) => module.enabled && module.can_read);

  return <div className="workspace-os-layout">
    <aside className="workspace-os-sidebar"><div className="workspace-os-sidebar-head"><strong>{context.workspace.name}</strong><span>{humanize(context.role)}</span></div>
      <nav>{readableModules.map((module) => <a key={module.key} className={section === module.key ? "active" : ""} href={workspacePath(workspaceId, module.key)}><span className="workspace-os-nav-dot" />{module.name}</a>)}</nav>
      <a className={`workspace-os-settings-link ${section === "settings" ? "active" : ""}`} href={workspacePath(workspaceId, "settings")}>⚙ Workspace settings</a>
    </aside>
    <div className="workspace-os-pane">
      {error && <div className="workspace-os-error workspace-os-top-error">{error}</div>}
      {section === "settings" ? <Settings context={context} onReload={reload} />
        : section === "dashboard" ? <Dashboard summary={summary} modules={context.modules} />
        : activeModule && activeModule.enabled && activeModule.can_read ? <ModulePage module={activeModule} />
        : activeModule && !activeModule.enabled ? <section className="workspace-os-blank"><h1>{activeModule.name}</h1><p>This module is installed but not enabled for this workspace.</p>{activeModule.can_manage && <a className="primary-link" href={workspacePath(workspaceId, "settings")}>Enable in workspace settings</a>}</section>
        : <section className="workspace-os-blank"><h1>Module unavailable</h1><p>This role does not have access to that workspace module.</p></section>}
    </div>
  </div>;
}
